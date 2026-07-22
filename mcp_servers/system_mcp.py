#!/usr/bin/env python3
"""Fernando MCP server: system & process control.

Tools: run_command, run_daemon, run_steps, authorize, mutate, reboot

NOTE: This server intentionally concentrates all shell-execution, process-group
management, and host-control behavior so it can be isolated (and, if flagged by
endpoint security, allowlisted) independently of the other Fernando tools.
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)
from _mcp_common import PROJECT_ROOT, read_api_key, find_my_session_id, save_continuation

import asyncio
import json
import os
import re
import secrets
import signal
import subprocess
import time
import urllib.request

from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("system")

# --- run_steps progress ---


def _step_progress_emit(api_key, session_id, pipeline_id, steps, results, running_index):
    """POST step progress to Flask for broadcast to chat UI."""
    if not session_id:
        return
    payload = json.dumps({
        "session_id": session_id,
        "pipeline_id": pipeline_id,
        "steps": [s["label"] for s in steps],
        "commands": [s["command"] for s in steps],
        "timeouts": [s.get("timeout", 30) for s in steps],
        "results": results,
        "running_index": running_index,
    }).encode()
    try:
        req = urllib.request.Request(
            "http://localhost:5000/api/step_progress",
            data=payload,
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


# --- Authorization system ---

_AUTH_CONFIG_PATH = os.path.join(PROJECT_ROOT, "data", "authorization.json")
_AUTH_DIR = "/tmp/agent_authorization"


def _load_auth_config():
    try:
        with open(_AUTH_CONFIG_PATH) as f:
            return json.load(f).get("authorizations", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _check_authorization(cmd, session_id):
    """Check if cmd requires authorization. Returns (allowed, auth_name) tuple."""
    config = _load_auth_config()
    for auth_name, auth in config.items():
        pattern = auth["match_command"]
        # Match as a command invocation: at start, after &&, after ;, or after |
        if re.search(r'(?:^|&&|;|\|)\s*' + re.escape(pattern), cmd):
            auth_file = os.path.join(_AUTH_DIR, session_id, auth_name)
            if not os.path.exists(auth_file):
                return False, auth_name
            # Check if expired
            try:
                with open(auth_file) as f:
                    grant = json.load(f)
                if grant.get("expires_at") and time.time() > grant["expires_at"]:
                    os.remove(auth_file)
                    return False, auth_name
            except (OSError, json.JSONDecodeError):
                return False, auth_name
    return True, None


def _consume_authorization(cmd, session_id):
    """If the command matched an expire_on_use authorization, delete the grant file."""
    config = _load_auth_config()
    for auth_name, auth in config.items():
        pattern = auth["match_command"]
        if re.search(r'(?:^|&&|;|\|)\s*' + re.escape(pattern), cmd) and auth.get("expire_on_use"):
            auth_file = os.path.join(_AUTH_DIR, session_id, auth_name)
            try:
                with open(auth_file) as f:
                    grant = json.load(f)
                if grant.get("auto_approve"):
                    continue
                os.remove(auth_file)
            except (OSError, json.JSONDecodeError):
                try:
                    os.remove(auth_file)
                except OSError:
                    pass


def _grant_authorization(session_id, auth_name):
    """Write the grant file with expiry."""
    config = _load_auth_config()
    auth = config.get(auth_name)
    if not auth:
        return False
    auth_dir = os.path.join(_AUTH_DIR, session_id)
    os.makedirs(auth_dir, exist_ok=True)
    grant = {
        "granted_at": time.time(),
        "expires_at": time.time() + auth.get("timeout_seconds", 300),
    }
    with open(os.path.join(auth_dir, auth_name), "w") as f:
        json.dump(grant, f)
    return True


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_command",
            description="Run a shell command with a mandatory timeout. Returns stdout, stderr, and exit code. The process group is killed if the timeout is exceeded. Use this for all command execution — it guarantees the call will never hang indefinitely.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds the command may run before being killed (required)",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory (optional, defaults to project root)",
                    },
                },
                "required": ["command", "timeout"],
            },
        ),
        Tool(
            name="authorize",
            description="Request authorization for a protected action (e.g. 'commit', 'push'). This prompts the user in the chat UI for approval. Returns success/denied. Call this BEFORE attempting git commit or git push — run_command and run_steps will reject those commands without a valid authorization grant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The authorization name to request (e.g. 'commit', 'push')",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of what you intend to do (shown to user)",
                    },
                },
                "required": ["action", "reason"],
            },
        ),
        Tool(
            name="run_daemon",
            description="Launch a long-lived background process (server, watcher, etc.) that persists after this tool returns. Returns immediately with the PID. Use this instead of the shell tool for any process that should keep running (e.g., dev servers, jupyter, file watchers). The process is fully detached and will not block the session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command to run (e.g., 'jupyter notebook --no-browser --port=9999')",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional working directory for the command",
                    },
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="run_steps",
            description="Run a sequence of shell commands with live progress updates in the chat UI. Each step runs sequentially; if one fails (non-zero exit code), subsequent steps are skipped. A non-zero exit code is treated as failure — if a command is expected to return non-zero, wrap it to coerce the exit code (e.g. 'grep pattern file || true'). The chat shows a live-updating step list with status, duration, and a cancel button. Use this instead of multiple sequential shell calls when you want the user to see progress. SSH + background processes: When starting a persistent process on a remote host via SSH, you MUST redirect all three file descriptors (stdin, stdout, stderr) to /dev/null AND background with & INSIDE the remote shell's command string. The tool waits for all child FDs to close — SSH won't exit until the remote process's inherited FDs close. Correct: ssh host 'bash -c \"cmd > /dev/null 2>&1 < /dev/null & disown\"'. Wrong: nohup, setsid, or & outside the SSH quotes. Note: disown is a bash builtin — always use bash -c on the remote side since the remote user's shell may be sh.",
            inputSchema={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Human-readable description of the step"},
                                "command": {"type": "string", "description": "Shell command to execute"},
                                "timeout": {"type": "integer", "description": "Optional timeout in seconds for this step (default: 30). Set higher for long-running commands."},
                            },
                            "required": ["label", "command"],
                        },
                        "description": "List of steps to run in order",
                    },
                },
                "required": ["steps"],
            },
        ),
        Tool(
            name="mutate",
            description="Restart Fernando to apply code changes. Returns immediately, then the restart triggers in the background within seconds. MCP server changes require the user to manually restart the Kiro CLI session. IMPORTANT: This call will kill the active conversation within seconds. Do NOT write any response after calling this tool. Put ALL follow-up information in the continuation message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "continuation": {
                        "type": "string",
                        "description": "Optional message to auto-send to all active chat sessions after restart completes, so the conversation can continue autonomously.",
                    },
                },
            },
        ),
        Tool(
            name="reboot",
            description="Reboot the host machine using 'sudo reboot'. This will terminate all sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "continuation": {
                        "type": "string",
                        "description": "Optional message to auto-send to all active chat sessions after restart completes, so the conversation can continue autonomously.",
                    },
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "run_command":
        cmd = arguments["command"]
        timeout_secs = arguments["timeout"]
        cwd = arguments.get("working_dir", PROJECT_ROOT)

        # Authorization check
        session_id = find_my_session_id() or "unknown"
        allowed, needed_auth = _check_authorization(cmd, session_id)
        if not allowed:
            return [TextContent(type="text", text=json.dumps({
                "error": f"Authorization required: '{needed_auth}'. Use the authorize tool to request permission first.",
                "authorization_needed": needed_auth,
            }, indent=2))]

        def _exec():
            start_t = time.time()
            cmd_pid_path = f"/tmp/fernando-cmd-{os.getpid()}.pid"
            try:
                proc = subprocess.Popen(
                    ["bash", "-c", cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=cwd, start_new_session=True,
                )
                try:
                    with open(cmd_pid_path, "w") as pf:
                        pf.write(str(proc.pid))
                except OSError:
                    pass
                try:
                    stdout, stderr = proc.communicate(timeout=timeout_secs)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, 9)
                    except OSError:
                        pass
                    proc.wait()
                    try:
                        os.unlink(cmd_pid_path)
                    except OSError:
                        pass
                    return {"status": "timeout", "exit_code": -1, "stdout": "", "stderr": f"Killed: exceeded {timeout_secs}s timeout", "duration": round(time.time() - start_t, 1)}
                try:
                    os.unlink(cmd_pid_path)
                except OSError:
                    pass
                duration = round(time.time() - start_t, 1)
                # Truncate large output, spill full content to file
                stdout_out = stdout or ""
                stderr_out = stderr or ""
                stdout_file = None
                stderr_file = None
                if len(stdout_out) > 16000:
                    stdout_file = f"/tmp/run_cmd_stdout_{os.getpid()}_{int(start_t)}.txt"
                    with open(stdout_file, "w") as f:
                        f.write(stdout_out)
                    stdout_out = stdout_out[:16000]
                if len(stderr_out) > 4000:
                    stderr_file = f"/tmp/run_cmd_stderr_{os.getpid()}_{int(start_t)}.txt"
                    with open(stderr_file, "w") as f:
                        f.write(stderr_out)
                    stderr_out = stderr_out[:4000]
                r = {"exit_code": proc.returncode, "stdout": stdout_out, "stderr": stderr_out, "duration": duration}
                if stdout_file:
                    r["stdout_truncated"] = True
                    r["stdout_file"] = stdout_file
                if stderr_file:
                    r["stderr_truncated"] = True
                    r["stderr_file"] = stderr_file
                return r
            except Exception as e:
                return {"exit_code": -1, "stdout": "", "stderr": str(e), "duration": round(time.time() - start_t, 1)}

        result = await asyncio.to_thread(_exec)
        # Consume authorization on successful execution
        if result.get("exit_code", -1) == 0:
            _consume_authorization(cmd, session_id)
    elif name == "authorize":
        action = arguments["action"]
        reason = arguments.get("reason", "")
        session_id = find_my_session_id() or "unknown"
        config = _load_auth_config()
        if action not in config:
            result = {"error": f"Unknown authorization: '{action}'"}
        else:
            auth_file = os.path.join(_AUTH_DIR, session_id, action)
            already_granted = False
            if os.path.exists(auth_file):
                try:
                    with open(auth_file) as f:
                        grant = json.load(f)
                    if not grant.get("expires_at") or time.time() <= grant["expires_at"]:
                        already_granted = True
                    else:
                        os.remove(auth_file)
                except (OSError, json.JSONDecodeError):
                    pass
            if already_granted:
                result = {"status": "authorized", "action": action}
            else:
                def _do_authorize():
                    auth_id = secrets.token_hex(8)
                    api_key = read_api_key()
                    req_data = json.dumps({
                        "session_id": session_id,
                        "auth_id": auth_id,
                        "action": action,
                        "reason": reason,
                        "description": config[action].get("description", action),
                    }).encode()
                    try:
                        req = urllib.request.Request(
                            "http://localhost:5000/api/authorization/request",
                            data=req_data,
                            headers={"Content-Type": "application/json", "X-API-Key": api_key},
                        )
                        resp = urllib.request.urlopen(req, timeout=10)
                        resp.read()
                    except Exception as e:
                        return {"error": f"Failed to emit authorization request: {e}"}
                    deny_file = auth_file + ".denied"
                    granted = False
                    deny_reason = ""
                    while True:
                        if os.path.exists(auth_file):
                            granted = True
                            break
                        if os.path.exists(deny_file):
                            try:
                                with open(deny_file) as f:
                                    d = json.load(f)
                                    deny_reason = d.get("reason", "")
                                os.remove(deny_file)
                            except (OSError, json.JSONDecodeError):
                                pass
                            break
                        time.sleep(0.25)
                    if granted:
                        return {"status": "authorized", "action": action}
                    r = {"status": "denied", "action": action}
                    if deny_reason:
                        r["reason"] = deny_reason
                    return r
                result = await asyncio.to_thread(_do_authorize)
    elif name == "run_daemon":
        cmd = arguments["command"]
        cwd = arguments.get("working_dir", PROJECT_ROOT)
        proc = subprocess.Popen(
            ["bash", "-c", f"nohup {cmd} > /dev/null 2>&1 &"],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.wait()
        # Find the actual daemon PID (child of the bash -c)
        time.sleep(0.5)
        try:
            ps = subprocess.run(
                ["pgrep", "-f", cmd[:60]],
                capture_output=True, text=True, timeout=5
            )
            pids = [p for p in ps.stdout.strip().split("\n") if p]
            result = {"status": "started", "command": cmd, "pids": pids, "working_dir": cwd}
        except Exception:
            result = {"status": "started", "command": cmd, "working_dir": cwd}
    elif name == "run_steps":
        steps = arguments["steps"]
        session_id = find_my_session_id() or ""

        # Authorization check: verify all step commands before executing any
        auth_blocked = False
        for step in steps:
            allowed, needed_auth = _check_authorization(step["command"], session_id)
            if not allowed:
                result = {"error": f"Authorization required: '{needed_auth}' (in step '{step['label']}'). Use the authorize tool to request permission first.", "authorization_needed": needed_auth}
                auth_blocked = True
                break

        if not auth_blocked:
            api_key = read_api_key()
            pipeline_id = secrets.token_hex(8)
            pid_path = f"/tmp/fernando-pipeline-{pipeline_id}.pid"
            results = []
            cancelled = False
            # Send initial state
            _step_progress_emit(api_key, session_id, pipeline_id, steps, results, -1)
            for i, step in enumerate(steps):
                # Mark step as running
                _step_progress_emit(api_key, session_id, pipeline_id, steps, results, i)
                start_t = time.time()
                try:
                    timeout = step.get("timeout", 30)
                    proc = subprocess.Popen(
                        ["bash", "-c", step["command"]],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, start_new_session=True,
                    )
                    # Write PID file so the Flask cancel endpoint can SIGTERM this process group
                    try:
                        with open(pid_path, "w") as pf:
                            pf.write(str(proc.pid))
                    except OSError:
                        pass
                    try:
                        stdout, stderr = proc.communicate(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        # Kill the entire process group on timeout
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except OSError:
                            pass
                        # Give processes a moment to exit gracefully, then force kill
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except OSError:
                                pass
                            proc.wait()
                        duration = round(time.time() - start_t, 1)
                        results.append({"label": step["label"], "status": "failed", "exit_code": -1, "stdout": "", "stderr": f"Timed out after {timeout}s", "duration": duration})
                        _step_progress_emit(api_key, session_id, pipeline_id, steps, results, i)
                        for j in range(i + 1, len(steps)):
                            results.append({"label": steps[j]["label"], "status": "skipped", "exit_code": None, "stdout": "", "stderr": "", "duration": 0})
                        _step_progress_emit(api_key, session_id, pipeline_id, steps, results, i)
                        break
                    # Remove PID file now that step finished
                    try:
                        os.unlink(pid_path)
                    except OSError:
                        pass
                    duration = round(time.time() - start_t, 1)
                    # Process killed by SIGKILL = cancelled by user (Flask cancel endpoint)
                    if proc.returncode == -signal.SIGKILL:
                        cancelled = True
                        results.append({"label": step["label"], "status": "cancelled", "exit_code": proc.returncode, "stdout": "", "stderr": "Cancelled by user", "duration": duration})
                        _step_progress_emit(api_key, session_id, pipeline_id, steps, results, i)
                        for j in range(i + 1, len(steps)):
                            results.append({"label": steps[j]["label"], "status": "cancelled", "exit_code": None, "stdout": "", "stderr": "", "duration": 0})
                        break
                    status = "succeeded" if proc.returncode == 0 else "failed"
                    results.append({"label": step["label"], "status": status, "exit_code": proc.returncode, "stdout": stdout[-4000:] if stdout else "", "stderr": stderr[-2000:] if stderr else "", "duration": duration})
                except Exception as e:
                    duration = round(time.time() - start_t, 1)
                    results.append({"label": step["label"], "status": "failed", "exit_code": -1, "stdout": "", "stderr": str(e), "duration": duration})
                _step_progress_emit(api_key, session_id, pipeline_id, steps, results, i)
                # Stop on failure
                if results[-1]["status"] == "failed":
                    for j in range(i + 1, len(steps)):
                        results.append({"label": steps[j]["label"], "status": "skipped", "exit_code": None, "stdout": "", "stderr": "", "duration": 0})
                    _step_progress_emit(api_key, session_id, pipeline_id, steps, results, i)
                    break
            # Clean up PID file if it still exists
            try:
                os.unlink(pid_path)
            except OSError:
                pass
            # Send final state
            _step_progress_emit(api_key, session_id, pipeline_id, steps, results, -2)
            # Consume authorizations for successfully executed steps
            for i, step_result in enumerate(results):
                if step_result.get("exit_code") == 0:
                    _consume_authorization(steps[i]["command"], session_id)
            result = {"pipeline_id": pipeline_id, "cancelled": cancelled, "steps": results}
    elif name == "mutate":
        save_continuation(arguments.get("continuation"))
        subprocess.Popen(
            [os.path.join(PROJECT_ROOT, "scripts", "mutate.sh")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        result = {
            "status": "restart_initiated",
            "message": "Fernando restart initiated. This process will be terminated as part of the restart. The conversation will resume via the continuation message.",
        }
    elif name == "reboot":
        save_continuation(arguments.get("continuation"))
        try:
            api_key = read_api_key()
            subprocess.run(["curl", "-s", "-X", "POST", "-H", f"X-API-Key: {api_key}", "http://localhost:5000/api/mutating"], timeout=2)
        except Exception:
            pass
        subprocess.Popen(
            ["nohup", "bash", "-c", "sleep 5 && sudo reboot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        result = {"status": "rebooting", "message": "Host will reboot in 5 seconds."}
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
