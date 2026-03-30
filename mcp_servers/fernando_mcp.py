#!/usr/bin/env python3
import os
import sys

# Activate the project venv so transitive deps (Flask etc.) are available
# even when launched by a bare system python (e.g. from MCP config)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_venv_site = os.path.join(_project_root, "venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
if os.path.isdir(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)

sys.path.insert(0, _project_root)

import asyncio
import json
import subprocess
import time
import urllib.request

from src.services.subagent_core import (
    create_workspace,
    resolve_context_path,
    write_task_json,
    write_status_json,
    write_instructions,
    write_spawn_script,
    schedule_at,
    schedule_cron,
    run_immediately,
    get_subagent_status,
    list_subagents,
    terminate_subagent,
)
from mcp.server import Server
from mcp.types import Tool, TextContent

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _save_continuation(continuation):
    """Save a continuation message for the calling chat session."""
    if not continuation:
        return
    session_id = None
    try:
        with open(os.path.join(_project_root, "data", "acp_pid_map.json")) as f:
            pid_map = json.load(f)
        pid = os.getpid()
        for _ in range(5):
            pid = os.popen(f"ps -o ppid= -p {pid}").read().strip()
            if not pid:
                break
            session_id = pid_map.get(pid)
            if session_id:
                break
    except Exception:
        pass
    with open(os.path.join(_project_root, "data", "pending_continuation.json"), "w") as f:
        json.dump({"message": continuation, "session_id": session_id}, f)


app = Server("fernando")


def create_subagent(
    task_id,
    task,
    additional_context="",
    context_path=None,
    at_schedule=None,
    cron_schedule=None,
):
    task_id, workspace = create_workspace(task_id)
    context_file = resolve_context_path(context_path)
    session_name = f"subagent-{task_id}"

    schedule = at_schedule or cron_schedule
    write_task_json(
        workspace, task_id, task, context_file, additional_context, schedule=schedule
    )
    write_status_json(workspace, scheduled=bool(schedule))
    instructions_file = write_instructions(
        workspace, task_id, task, context_file, additional_context
    )
    script_path = write_spawn_script(workspace, session_name, instructions_file)

    if at_schedule:
        schedule_at(script_path, at_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "scheduled_at": at_schedule,
        }

    if cron_schedule:
        schedule_cron(script_path, cron_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "cron": cron_schedule,
        }

    run_immediately(session_name, instructions_file)
    return {"task_id": task_id, "session_name": session_name, "workspace": workspace}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="spawn_subagent",
            description="Spawn a subagent in a new tmux session to work on a delegated task. The subagent will save proof of work and communicate progress via JSON files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique identifier for this task (e.g., 'research-aws-pricing', 'debug-issue-123')",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description for the subagent",
                    },
                    "additional_context": {
                        "type": "string",
                        "description": "Optional additional context or instructions",
                    },
                    "context_path": {
                        "type": "string",
                        "description": "Optional path to a context file that will be read at task start and updated with progress. File will be created if it doesn't exist.",
                    },
                    "at_schedule": {
                        "type": "string",
                        "description": "Run at specific time using 'at' command (e.g., '14:30', 'now + 1 hour'). Mutually exclusive with cron_schedule.",
                    },
                    "cron_schedule": {
                        "type": "string",
                        "description": "Run on cron schedule (e.g., '*/5 * * * *' for every 5 minutes, '0 * * * *' for hourly). Mutually exclusive with at_schedule.",
                    },
                },
                "required": ["task_id", "task"],
            },
        ),
        Tool(
            name="get_subagent_status",
            description="Check the status and progress of a subagent task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to check"}
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="list_subagents",
            description="List all subagent tasks and their current status",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="terminate_subagent",
            description="Terminate a subagent tmux session",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to terminate",
                    }
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="mutate",
            description="Restart Fernando to apply code changes. Blocks until Fernando is back up and healthy, or reports failure with logs. Runs stop/start in a detached background process so the calling Kiro agent session survives. NOTE: This restarts the Flask backend and nginx but preserves tmux sessions including your own. MCP server changes require the user to manually restart the Kiro CLI session. For ACP chat sessions, provide a continuation message to auto-resume after restart.",
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
    if name == "spawn_subagent":
        result = create_subagent(
            arguments["task_id"],
            arguments["task"],
            arguments.get("additional_context", ""),
            arguments.get("context_path"),
            arguments.get("at_schedule"),
            arguments.get("cron_schedule"),
        )
    elif name == "get_subagent_status":
        result = get_subagent_status(arguments["task_id"])
    elif name == "list_subagents":
        result = list_subagents()
    elif name == "terminate_subagent":
        result = terminate_subagent(arguments["task_id"])
    elif name == "mutate":
        _save_continuation(arguments.get("continuation"))
        proc = subprocess.Popen(
            [os.path.join(_project_root, "mutate.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = proc.communicate(timeout=5)

        # Wait for Fernando to come back up
        healthy = False
        for i in range(30):
            time.sleep(2)
            try:
                resp = urllib.request.urlopen("http://localhost:5000", timeout=2)
                if resp.status == 200:
                    healthy = True
                    break
            except Exception:
                pass

        if healthy:
            result = {
                "status": "restart_complete",
                "message": f"Fernando restarted successfully and is healthy.",
            }
        else:
            # Read the log for diagnostics
            log = ""
            try:
                with open("/tmp/fernando-mutate.log") as f:
                    log = f.read()[-2000:]
            except Exception:
                pass
            result = {
                "status": "restart_failed",
                "message": "Fernando did not come back up within 60 seconds.",
                "log": log,
            }
    elif name == "reboot":
        _save_continuation(arguments.get("continuation"))
        try:
            subprocess.run(["curl", "-s", "-X", "POST", "http://localhost:5000/api/mutating"], timeout=2)
        except Exception:
            pass
        subprocess.Popen(["sudo", "reboot"])
        result = {"status": "rebooting", "message": "Host is rebooting now."}
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
