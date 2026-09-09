"""ACP (Agent Client Protocol) service for managing kiro-cli acp subprocesses."""

import glob
import json
import logging
import os
import select
import subprocess
import threading
import time
import shutil
import uuid

from src.services import rag

logger = logging.getLogger(__name__)

KIRO_CLI = shutil.which("kiro-cli") or os.path.expanduser("~/.local/bin/kiro-cli")
OPENCODE_CLI = os.path.expanduser("~/.opencode/bin/opencode")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SESSIONS_FILE = os.path.join(DATA_DIR, "chat_sessions.json")
ARCHIVED_FILE = os.path.join(DATA_DIR, "chat_sessions_archived.json")
PID_MAP_FILE = os.path.join(DATA_DIR, "acp_pid_map.json")
LINEAGE_FILE = os.path.join(DATA_DIR, "session_lineage.json")
CHILD_MESSAGES_DIR = os.path.join(DATA_DIR, "child_messages")
HISTORY_DIR = os.path.join(DATA_DIR, "chat_history")
KIRO_SESSIONS_DIR = os.path.expanduser("~/.kiro/sessions/cli")


def load_history_file(session_id):
    """Load history for a session from its JSONL file."""
    history = []
    try:
        with open(os.path.join(HISTORY_DIR, f"{session_id}.jsonl")) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        pass
    except OSError:
        pass
    return history


def _save_sessions_map(sessions_map):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions_map, f, indent=2)


def _load_sessions_map():
    try:
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


_archived_lock = threading.Lock()


def _save_archived_map(archived_map):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ARCHIVED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(archived_map, f, indent=2)
    os.replace(tmp, ARCHIVED_FILE)


def _load_archived_map():
    try:
        with open(ARCHIVED_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


_lineage_lock = threading.Lock()


def _load_lineage():
    """Load session lineage map: {session_id: {"parent": parent_id, "children": [child_ids]}}"""
    try:
        with open(LINEAGE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_lineage(lineage):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = LINEAGE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lineage, f, indent=2)
    os.replace(tmp, LINEAGE_FILE)


def set_parent(child_id, parent_id):
    """Record that parent_id spawned child_id."""
    with _lineage_lock:
        lineage = _load_lineage()
        # Set child's parent
        if child_id not in lineage:
            lineage[child_id] = {"parent": None, "children": []}
        lineage[child_id]["parent"] = parent_id
        # Add to parent's children
        if parent_id not in lineage:
            lineage[parent_id] = {"parent": None, "children": []}
        if child_id not in lineage[parent_id]["children"]:
            lineage[parent_id]["children"].append(child_id)
        _save_lineage(lineage)


def get_parent(child_id):
    """Get the parent session ID for a child, or None if no parent."""
    with _lineage_lock:
        lineage = _load_lineage()
        return lineage.get(child_id, {}).get("parent")


def get_children(parent_id):
    """Get list of child session IDs for a parent."""
    with _lineage_lock:
        lineage = _load_lineage()
        return lineage.get(parent_id, {}).get("children", [])


def is_child_of(child_id, parent_id):
    """Check if child_id is a direct child of parent_id."""
    with _lineage_lock:
        lineage = _load_lineage()
        return lineage.get(child_id, {}).get("parent") == parent_id


# --- Child message queue ---

def _get_message_queue_path(parent_id):
    """Get path to message queue file for a parent session."""
    return os.path.join(CHILD_MESSAGES_DIR, f"{parent_id}.json")


def queue_child_message(parent_id, child_id, message):
    """Add a message from child to parent's queue. Returns message ID."""
    os.makedirs(CHILD_MESSAGES_DIR, exist_ok=True)
    queue_path = _get_message_queue_path(parent_id)
    with _lineage_lock:  # Reuse lock for simplicity
        try:
            with open(queue_path) as f:
                queue = json.load(f)
        except (OSError, json.JSONDecodeError):
            queue = []
        msg_id = uuid.uuid4().hex[:8]
        queue.append({
            "id": msg_id,
            "from": child_id,
            "message": message,
            "ts": time.time(),
            "read": False,
        })
        tmp = queue_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(queue, f, indent=2)
        os.replace(tmp, queue_path)
    return msg_id


def get_unread_child_messages(parent_id, mark_read=True):
    """Get unread messages from children. Optionally marks them as read."""
    queue_path = _get_message_queue_path(parent_id)
    with _lineage_lock:
        try:
            with open(queue_path) as f:
                queue = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        unread = [m for m in queue if not m.get("read")]
        if mark_read and unread:
            for m in queue:
                m["read"] = True
            tmp = queue_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(queue, f, indent=2)
            os.replace(tmp, queue_path)
        return unread


def count_unread_child_messages(parent_id):
    """Count unread messages without marking them read."""
    queue_path = _get_message_queue_path(parent_id)
    try:
        with open(queue_path) as f:
            queue = json.load(f)
        return sum(1 for m in queue if not m.get("read"))
    except (OSError, json.JSONDecodeError):
        return 0


def _save_pid_map(sessions):
    """Write mapping of kiro-cli PID -> fernando session ID."""
    pid_map = {}
    for sid, session in sessions.items():
        if session.proc and session.proc.poll() is None:
            pid_map[str(session.proc.pid)] = sid
    try:
        with open(PID_MAP_FILE, "w") as f:
            json.dump(pid_map, f)
    except Exception:
        pass


CONTINUATION_FILE = os.path.join(DATA_DIR, "pending_continuation.json")


def _pop_continuation():
    """Read and delete the pending continuation file, if any."""
    try:
        with open(CONTINUATION_FILE) as f:
            data = json.load(f)
        os.remove(CONTINUATION_FILE)
        return data  # {message, session_id}
    except Exception:
        return None


class ACPSession:
    """Manages a single kiro-cli acp subprocess and its ACP session."""

    DEFAULT_MODEL = "claude-opus-4.6"

    def __init__(self, session_id, on_event=None, backend="kiro"):
        self.id = session_id
        self.on_event = on_event
        self.backend = backend  # "kiro" or "opencode"
        self.proc = None
        self.acp_session_id = None
        self.display_name = "Chat-" + session_id
        from src.services.settings import get as get_setting
        self.model = get_setting("default_model") or self.DEFAULT_MODEL
        self.effort = get_setting("default_effort") or "max"
        self._reader_thread = None
        self._stderr_thread = None
        self._next_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._alive = False
        self.history = []
        self.ready = False
        self._recording = True  # gate for _record_event
        self._broadcasting = True  # gate for on_event dispatch
        self._last_activity = time.time()  # track last stdout data for stall detection
        self._is_prompting = False  # True while waiting for agent response
        self._has_unread = False  # True when agent turn completed but user hasn't viewed
        self._on_status_change = None  # callback for status changes
        self._flushed = 0  # number of history entries already written to disk
        self._retry_count = 0  # current consecutive retry attempts for model unavailability
        self._max_retries = 5  # give up after this many consecutive failures
        self._retry_backoff_base = 5  # seconds, doubles each retry
        self._retry_pending = False  # True while a retry is waiting to fire
        self._reloading = False  # True while reload is in progress (prevents race)

    def _spawn_and_init(self):
        """Spawn kiro-cli or opencode acp and run initialize handshake."""
        if self.backend == "opencode":
            logger.info(f"[{self.id}] Spawning opencode acp subprocess")
            env = os.environ.copy()
            config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            key, _, value = line.partition("=")
                            env[key] = value
            if self.model:
                import json as _json
                env["OPENCODE_CONFIG_CONTENT"] = _json.dumps({"model": self.model})
            logger.info(f"[{self.id}] env has AWS_BEARER_TOKEN_BEDROCK: {'AWS_BEARER_TOKEN_BEDROCK' in env}")
            logger.info(f"[{self.id}] model={self.model}")
            self.proc = subprocess.Popen(
                [OPENCODE_CLI, "acp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.path.expanduser("~/fernando"),
                env=env,
            )
            logger.info(f"[{self.id}] opencode pid={self.proc.pid}")
        else:
            logger.info(f"[{self.id}] Spawning kiro-cli acp subprocess")
            self.proc = subprocess.Popen(
                [KIRO_CLI, "acp", "-a", "--model", self.model, "--effort", self.effort],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.path.expanduser("~/fernando"),
            )
            logger.info(f"[{self.id}] kiro-cli pid={self.proc.pid}")
        self._alive = True
        self._last_activity = time.time()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        # Drain stderr to prevent pipe buffer deadlock
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()

        resp = self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "fernando-chat", "version": "1.0.0"},
        }, timeout=30 if self.backend == "opencode" else 15)
        if not resp:
            raise RuntimeError("ACP initialize failed")
        logger.info(f"[{self.id}] ACP initialized successfully")

    def start(self):
        """Create a new ACP session."""
        self._spawn_and_init()
        resp = self._request("session/new", {
            "cwd": os.path.expanduser("~/fernando"),
            "mcpServers": [],
        }, timeout=120)
        logger.info(f"[{self.id}] session/new response: sessionId={resp.get('sessionId') if resp else 'None'}")
        if resp and "sessionId" in resp:
            self.acp_session_id = resp["sessionId"]
            logger.info(f"[{self.id}] acp_session_id set to {self.acp_session_id}")
        else:
            raise RuntimeError("ACP session/new failed")

    @staticmethod
    def _patch_incomplete_mutate(acp_session_id):
        """If the session's last tool use is a mutate with no ToolResults, append one."""
        jsonl_path = os.path.join(KIRO_SESSIONS_DIR, f"{acp_session_id}.jsonl")
        try:
            with open(jsonl_path, "rb") as f:
                lines = f.readlines()
        except OSError:
            return
        # Walk backwards to find a dangling mutate/reboot tool use
        pending_tool_use_id = None
        has_result_for = set()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            kind = obj.get("kind", "")
            if kind == "ToolResults":
                for c in obj.get("data", {}).get("content", []):
                    if not isinstance(c, dict):
                        continue
                    tid = c.get("data", {}).get("toolUseId")
                    if tid:
                        has_result_for.add(tid)
            if kind == "AssistantMessage":
                for c in obj.get("data", {}).get("content", []):
                    if not isinstance(c, dict):
                        continue
                    if c.get("kind") == "toolUse" and c.get("data", {}).get("name") in ("mutate", "reboot"):
                        tid = c["data"]["toolUseId"]
                        if tid not in has_result_for:
                            pending_tool_use_id = tid
                if pending_tool_use_id:
                    break
        if not pending_tool_use_id:
            return
        result_entry = {
            "version": "v1",
            "kind": "ToolResults",
            "data": {
                "message_id": str(uuid.uuid4()),
                "content": [{
                    "kind": "toolResult",
                    "data": {
                        "toolUseId": pending_tool_use_id,
                        "content": [{"kind": "text", "data": json.dumps({
                            "status": "restart_complete",
                            "message": "Fernando restarted successfully. The mutate tool call was in-flight when the old process was terminated as part of the restart sequence. This result was backfilled on session reload.",
                        })}],
                        "status": "success",
                    },
                }],
            },
        }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(result_entry) + "\n")
        logger.info(f"Patched incomplete mutate tool use {pending_tool_use_id} in {acp_session_id}")

    def load(self, acp_session_id):
        """Load an existing ACP session (resume after restart)."""
        if self.backend == "kiro":
            self._patch_incomplete_mutate(acp_session_id)
        self._load_history()
        self._patch_our_incomplete_mutate()
        self._recording = False  # Don't overwrite rich history with kiro's stripped replay
        self._broadcasting = False  # Don't fire on_event for replay events
        self._spawn_and_init()
        self.acp_session_id = acp_session_id

        if self.backend == "kiro":
            # Remove stale lock file (Kiro-specific)
            lock_file = os.path.join(KIRO_SESSIONS_DIR, f"{acp_session_id}.lock")
            try:
                os.remove(lock_file)
            except OSError:
                pass

        load_params = {
            "sessionId": acp_session_id,
            "cwd": os.path.expanduser("~/fernando"),
            "mcpServers": [],
        }
        if self.model:
            load_params["model"] = self.model
        logger.info(f"[{self.id}] Calling session/load for {acp_session_id} (backend={self.backend})")
        resp = self._request("session/load", load_params, timeout=120)
        if not resp:
            raise RuntimeError(f"session/load failed for {acp_session_id}")
        logger.info(f"[{self.id}] session/load succeeded")
        self._recording = True
        self._broadcasting = True

    def send_prompt(self, text):
        if not self.acp_session_id:
            logger.warning(f"[{self.id}] send_prompt called but no acp_session_id")
            return
        if not self.ready:
            logger.warning(f"[{self.id}] send_prompt called but session not ready")
            return
        logger.info(f"[{self.id}] send_prompt: {len(text)} chars, alive={self._alive}, proc_poll={self.proc.poll() if self.proc else 'N/A'}, was_prompting={self._is_prompting}")
        if self._retry_pending:
            self._retry_pending = False
            self._retry_count = 0
        if self._is_prompting:
            logger.info(f"[{self.id}] cancelling stuck prompt before sending new one")
            self.cancel()
            time.sleep(0.5)
        self._is_prompting = True
        self._has_unread = False  # User is actively engaged, clear unread
        self._notify_status_change()
        self._last_activity = time.time()
        evt = {"type": "user_prompt", "text": text, "ts": time.time()}
        self.history.append(evt)
        self._save_history()
        if self.on_event:
            self.on_event(self.id, evt)
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/prompt",
            "params": {
                "sessionId": self.acp_session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        })

    def send_continuation(self, text):
        """Send a prompt that displays as a system message, not a user message.
        Returns True if agent was idle (immediate delivery), False if busy (queued)."""
        if not self.acp_session_id:
            return False
        was_idle = not self._is_prompting
        logger.info(f"[{self.id}] send_continuation: {len(text)} chars, was_idle={was_idle}")
        self._is_prompting = True
        self._has_unread = False
        self._notify_status_change()
        self._last_activity = time.time()
        prefixed = "[CONTINUATION] " + text
        evt = {"type": "continuation", "text": prefixed, "ts": time.time()}
        self.history.append(evt)
        self._save_history()
        if self.on_event:
            self.on_event(self.id, evt)
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/prompt",
            "params": {
                "sessionId": self.acp_session_id,
                "prompt": [{"type": "text", "text": prefixed}],
            },
        })
        return was_idle

    def send_agent_message(self, from_session, text):
        """Send a message from another agent (parent). Displays distinctly from continuations.
        Returns True if agent was idle (immediate delivery), False if busy (queued)."""
        if not self.acp_session_id:
            return False
        was_idle = not self._is_prompting
        logger.info(f"[{self.id}] agent_message from {from_session}: {len(text)} chars, was_idle={was_idle}")
        self._is_prompting = True
        self._has_unread = False
        self._notify_status_change()
        self._last_activity = time.time()
        prefixed = f"[AGENT MESSAGE from {from_session}] {text}"
        evt = {"type": "agent_message", "from": from_session, "text": text, "ts": time.time()}
        self.history.append(evt)
        self._save_history()
        if self.on_event:
            self.on_event(self.id, evt)
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/prompt",
            "params": {
                "sessionId": self.acp_session_id,
                "prompt": [{"type": "text", "text": prefixed}],
            },
        })
        return was_idle

    def cancel(self):
        if not self.acp_session_id:
            return
        stall_secs = time.time() - self._last_activity
        logger.info(f"[{self.id}] cancel requested, stall={stall_secs:.0f}s, proc_poll={self.proc.poll() if self.proc else 'N/A'}")
        self._send({
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": self.acp_session_id},
        })

    def execute_command(self, command, args=None):
        """Execute a slash command via ACP _kiro.dev/commands/execute extension.
        
        Args:
            command: Command name (with or without leading slash, e.g. "/tangent" or "tangent")
            args: Optional dict of command arguments
        """
        if not self.acp_session_id:
            return {"error": "No ACP session"}
        # Strip leading slash if present
        cmd_name = command.lstrip("/")
        logger.info(f"[{self.id}] execute_command: {cmd_name} args={args}")
        req_id = self._get_id()
        # TuiCommand is an adjacently tagged enum: {command: string, args: object}
        tui_command = {"command": cmd_name, "args": args or {}}
        logger.info(f"[{self.id}] execute_command: req_id={req_id} tui_command={tui_command}")
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "result": None, "error": None}
        self._send({"jsonrpc": "2.0", "id": req_id, "method": "_kiro.dev/commands/execute", "params": {
            "sessionId": self.acp_session_id,
            "command": tui_command,
        }})
        got_response = event.wait(timeout=30)
        with self._lock:
            entry = self._pending.pop(req_id, {})
        if entry.get("error"):
            logger.warning(f"[{self.id}] execute_command error: {entry['error']}")
            return {"error": entry["error"]}
        logger.info(f"[{self.id}] execute_command result: {entry.get('result')}")
        return entry.get("result")

    def stop(self):
        self._alive = False
        self._is_prompting = False
        self._save_history(index_rag=True)
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    @property
    def is_loaded(self):
        return self.proc is not None and self.proc.poll() is None

    def unload(self):
        """Terminate the process but keep session state for later reload."""
        logger.info(f"[{self.id}] unloading (idle teardown)")
        self._alive = False
        self._is_prompting = False
        self._save_history(index_rag=False)
        self.ready = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def get_stall_info(self):
        """Return diagnostic info about current session state."""
        return {
            "alive": self._alive,
            "prompting": self._is_prompting,
            "last_activity_secs_ago": round(time.time() - self._last_activity, 1),
            "proc_alive": self.proc is not None and self.proc.poll() is None,
            "proc_poll": self.proc.poll() if self.proc else None,
        }

    def get_status(self):
        """Return current status: 'working', 'unread', or 'idle'."""
        if self._is_prompting:
            return "working"
        if self._has_unread:
            return "unread"
        return "idle"

    def _notify_status_change(self):
        """Notify listeners that status changed."""
        if self._on_status_change:
            self._on_status_change(self.id, self.get_status())

    def mark_read(self):
        """Clear the unread flag when user views the chat."""
        if self._has_unread:
            self._has_unread = False
            self._notify_status_change()

    def _get_id(self):
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _send(self, msg):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write((json.dumps(msg) + "\n").encode())
                self.proc.stdin.flush()
            except Exception as e:
                logger.error(f"ACP send error: {e}")

    def _request(self, method, params, timeout=30):
        req_id = self._get_id()
        logger.info(f"[{self.id}] _request: method={method} req_id={req_id}")
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "result": None}
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        got_response = event.wait(timeout=timeout)
        logger.info(f"[{self.id}] _request: req_id={req_id} got_response={got_response}")
        with self._lock:
            entry = self._pending.pop(req_id, {})
        return entry.get("result")

    def _record_event(self, msg):
        if not self._recording:
            return
        method = msg.get("method", "")
        if method == "session/update" or msg.get("result", {}).get("stopReason"):
            msg.setdefault("ts", time.time())
            msg.setdefault("model", self.model)
            self.history.append(msg)
            is_turn_end = bool(msg.get("result", {}).get("stopReason"))
            self._save_history(index_rag=is_turn_end)

    def _history_path(self):
        return os.path.join(HISTORY_DIR, f"{self.id}.jsonl")

    def _save_history(self, index_rag=False):
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            path = self._history_path()
            new_entries = self.history[self._flushed:]
            if new_entries:
                with open(path, "a") as f:
                    for entry in new_entries:
                        f.write(json.dumps(entry) + "\n")
                if self._flushed == 0:
                    os.chmod(path, 0o600)
                self._flushed = len(self.history)
        except Exception:
            pass
        if index_rag:
            threading.Thread(
                target=self._index_rag_background, daemon=True
            ).start()

    def _index_rag_background(self):
        try:
            rag.index_session(self.id, self.display_name, list(self.history))
        except Exception as e:
            logger.warning(f"[{self.id}] RAG index error: {e}")

    def _load_history(self):
        self.history = load_history_file(self.id)
        self._flushed = len(self.history)

    def _patch_our_incomplete_mutate(self):
        """If history has a mutate/reboot tool_call without a completed tool_call_update, append one."""
        if not self.history:
            return
        pending_tool_call_id = None
        pending_tool_title = None
        completed_tool_ids = set()
        for evt in self.history:
            params = evt.get("params", {})
            update = params.get("update", {})
            su = update.get("sessionUpdate", "")
            if su == "tool_call":
                tool_name = update.get("title", "")
                if "mutate" in tool_name.lower() or "reboot" in tool_name.lower():
                    pending_tool_call_id = update.get("toolCallId")
                    pending_tool_title = tool_name
            elif su == "tool_call_update":
                if update.get("status") == "completed":
                    completed_tool_ids.add(update.get("toolCallId"))
        if pending_tool_call_id and pending_tool_call_id not in completed_tool_ids:
            synthetic_event = {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": pending_tool_call_id,
                        "status": "completed",
                        "title": pending_tool_title or "mutate",
                        "rawOutput": {"items": [{"Json": {"content": [{"type": "text", "text": json.dumps({
                            "status": "restart_complete",
                            "message": "Fernando restarted successfully. This result was backfilled on session reload.",
                        })}], "isError": False}}]},
                    }
                },
                "ts": time.time(),
            }
            self.history.append(synthetic_event)
            self._save_history()
            logger.info(f"[{self.id}] Patched incomplete mutate tool call {pending_tool_call_id}")

    def _read_loop(self):
        buf = b""
        stall_warned = 0  # last stall warning threshold (seconds)
        while self._alive and self.proc and self.proc.poll() is None:
            try:
                ready, _, _ = select.select([self.proc.stdout], [], [], 0.5)
                if not ready:
                    # Stall detection: log warnings at increasing intervals while prompting
                    if self._is_prompting:
                        elapsed = time.time() - self._last_activity
                        if elapsed > 60 and elapsed > stall_warned + 60:
                            stall_warned = int(elapsed)
                            logger.warning(f"[{self.id}] STALL: no stdout data for {elapsed:.0f}s while prompting, proc_poll={self.proc.poll()}")
                    continue
                chunk = self.proc.stdout.read1(65536)
                if not chunk:
                    logger.warning(f"[{self.id}] stdout EOF, proc_poll={self.proc.poll()}")
                    break
                self._last_activity = time.time()
                stall_warned = 0
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode())
                    except (json.JSONDecodeError, ValueError):
                        logger.warning(f"[{self.id}] non-JSON stdout line: {line[:200]}")
                        continue
                    self._dispatch(msg)
            except Exception as e:
                logger.error(f"[{self.id}] _read_loop exception: {e}", exc_info=True)
                break

        self._alive = False
        self._is_prompting = False
        logger.info(f"[{self.id}] _read_loop exited, proc_poll={self.proc.poll() if self.proc else 'dead'}")
        if self.on_event:
            try:
                self.on_event(self.id, {"type": "session_ended"})
            except Exception:
                pass

    def _stderr_loop(self):
        """Drain stderr to prevent pipe buffer deadlock and log any output."""
        try:
            while self._alive and self.proc and self.proc.poll() is None:
                ready, _, _ = select.select([self.proc.stderr], [], [], 1.0)
                if not ready:
                    continue
                chunk = self.proc.stderr.read1(65536)
                if not chunk:
                    break
                for line in chunk.decode(errors="replace").splitlines():
                    if line.strip():
                        logger.warning(f"[{self.id}] kiro-cli stderr: {line.rstrip()}")
                        if "Transport" in line and "closed" in line:
                            logger.error(f"[{self.id}] MCP transport crash detected, scheduling auto-reload")
                            threading.Thread(target=self._auto_reload, daemon=True).start()
        except Exception:
            pass

    def _auto_reload(self):
        """Reload the session after MCP transport crash."""
        # Wait for current turn to end
        for _ in range(30):
            if not self._is_prompting:
                break
            time.sleep(1)
        acp_id = self.acp_session_id
        if not acp_id or not self._alive:
            return
        logger.info(f"[{self.id}] Auto-reloading session after MCP crash")
        # Notify user
        if self.on_event:
            self.on_event(self.id, {"type": "system_message", "text": "MCP server connection lost. Reloading session..."})
        self.stop()
        try:
            self._recording = True
            self._broadcasting = True
            self._alive = True
            self.load(acp_id)
            self.ready = True
            if self.on_event:
                self.on_event(self.id, {"type": "session_ready"})
                self.on_event(self.id, {"type": "system_message", "text": "Session reloaded. MCP tools restored."})
            # Auto-continue the agent so it resumes work
            self.send_continuation("The MCP server connection was lost and has been automatically restored. All tools are available again. Continue where you left off.")
        except Exception as e:
            logger.error(f"[{self.id}] Auto-reload failed: {e}")
            if self.on_event:
                self.on_event(self.id, {"type": "system_message", "text": f"Auto-reload failed: {e}"})

    def _retry_after_unavailable(self):
        """Auto-retry the last prompt after model unavailability with exponential backoff."""
        self._retry_count += 1
        self._retry_pending = True
        delay = self._retry_backoff_base * (2 ** (self._retry_count - 1))
        logger.info(f"[{self.id}] Model unavailable, retry {self._retry_count}/{self._max_retries} in {delay}s")
        if self.on_event and self._broadcasting:
            self.on_event(self.id, {
                "type": "system_message",
                "text": f"Model unavailable. Retrying ({self._retry_count}/{self._max_retries}) in {delay}s...",
            })
        time.sleep(delay)
        if not self._alive or not self.acp_session_id or not self._retry_pending:
            self._retry_pending = False
            return
        self._retry_pending = False
        self._is_prompting = True
        self._last_activity = time.time()
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/prompt",
            "params": {
                "sessionId": self.acp_session_id,
                "prompt": [{"type": "text", "text": "continue"}],
            },
        })

    def _dispatch(self, msg):
        msg_id = msg.get("id")
        method = msg.get("method", "")
        
        # Log non-chunk messages for debugging
        if method != "session/update" or (msg.get("params", {}).get("update", {}).get("sessionUpdate") != "agent_message_chunk"):
            logger.info(f"[{self.id}] _dispatch: id={msg_id} method={method} keys={list(msg.keys())}")

        if msg_id is not None and "result" in msg:
            with self._lock:
                if msg_id in self._pending:
                    self._pending[msg_id]["result"] = msg["result"]
                    self._pending[msg_id]["event"].set()
                    return
            # Check for stopReason to track prompting state
            stop_reason = msg.get("result", {}).get("stopReason")
            if stop_reason:
                logger.info(f"[{self.id}] turn ended: stopReason={stop_reason}")
                self._is_prompting = False
                self._has_unread = True  # Agent finished, mark as unread
                self._retry_count = 0
                self._notify_status_change()
            self._record_event(msg)
            if self.on_event and self._broadcasting:
                try:
                    self.on_event(self.id, msg)
                except Exception:
                    pass
            return

        if msg_id is not None and "error" in msg:
            with self._lock:
                if msg_id in self._pending:
                    self._pending[msg_id]["result"] = None
                    self._pending[msg_id]["error"] = msg.get("error")
                    self._pending[msg_id]["event"].set()
                    return
            err = msg.get("error", {})
            logger.warning(f"[{self.id}] ACP error: {err}")
            self._is_prompting = False
            self._has_unread = True  # Error is also a turn end
            self._notify_status_change()
            err_text = str(err.get("data") or err.get("message", ""))
            if "is not available" in err_text:
                if self._retry_count < self._max_retries:
                    threading.Thread(target=self._retry_after_unavailable, daemon=True).start()
                else:
                    logger.warning(f"[{self.id}] Model unavailable: max retries ({self._max_retries}) exhausted")
                    self._retry_count = 0
                    if self.on_event and self._broadcasting:
                        self.on_event(self.id, {"type": "system_message", "text": f"Model unavailable after {self._max_retries} retries. Send any message to try again."})
                        self.on_event(self.id, {"type": "acp_error", "error": err_text})
                return
            self._retry_count = 0
            if "Transport" in err_text and "closed" in err_text:
                logger.error(f"[{self.id}] MCP transport crash detected via ACP error, scheduling auto-reload")
                threading.Thread(target=self._auto_reload, daemon=True).start()
            if self.on_event and self._broadcasting:
                try:
                    self.on_event(self.id, {"type": "acp_error", "error": err_text or "Unknown error"})
                except Exception:
                    pass
            return

        # Notification (no id) — log session/update type
        params = msg.get("params", {})
        su = (params.get("update") or {}).get("sessionUpdate", "")
        if su and su != "agent_message_chunk":
            logger.debug(f"[{self.id}] session/update: {su}")

        self._record_event(msg)
        if self.on_event and self._broadcasting:
            try:
                self.on_event(self.id, msg)
            except Exception as e:
                logger.error(f"[{self.id}] ACP event callback error: {e}")


class ACPManager:
    IDLE_TIMEOUT = 5 * 60  # 5 minutes for testing (change to 60 * 60 for production)
    REAPER_INTERVAL = 60  # check every 60 seconds

    def __init__(self):
        self.sessions = {}
        self._lock = threading.Lock()
        self.default_on_event = None  # Set by websocket.py after register_handlers
        self._on_status_change = None  # Callback for session status changes (working/unread/idle)
        self._reaper_thread = threading.Thread(target=self._idle_reaper_loop, daemon=True)
        self._reaper_thread.start()
        self._active_pane_sessions = set()  # sessions currently open in a UI pane
        self._on_sessions_change = None  # callback for live UI updates

    def set_active_pane_sessions(self, session_ids):
        """Update the set of sessions currently open in UI panes."""
        self._active_pane_sessions = set(session_ids)

    def _idle_reaper_loop(self):
        """Periodically check for idle sessions and unload them."""
        while True:
            time.sleep(self.REAPER_INTERVAL)
            self._reap_idle_sessions()

    def _reap_idle_sessions(self):
        """Unload sessions that have been idle longer than IDLE_TIMEOUT."""
        from src.services.settings import get as get_setting
        timeout = get_setting("idle_session_timeout")
        if timeout is None:
            timeout = self.IDLE_TIMEOUT
        if timeout <= 0:
            return  # disabled

        now = time.time()
        with self._lock:
            sessions_snapshot = list(self.sessions.items())

        if not sessions_snapshot:
            return

        for sid, session in sessions_snapshot:
            if sid in self._active_pane_sessions:
                continue  # don't unload sessions open in a pane
            if not session.is_loaded:
                continue
            idle_time = now - session._last_activity
            if idle_time > timeout:
                logger.info(f"[idle-reaper] Unloading session {sid} (idle {idle_time:.0f}s)")
                session.unload()
                self._save()  # Persist the loaded=False state
                self._save_pid_map()
                self._broadcast_sessions_list()

    def create_session(self, on_event=None, model=None, backend="kiro"):
        session_id = str(uuid.uuid4())[:8]
        session = ACPSession(session_id, on_event=on_event, backend=backend)
        if model:
            session.model = model
        self._wire_session_status_callback(session)
        with self._lock:
            self.sessions[session_id] = session
        threading.Thread(target=self._start_new, args=(session_id, session), daemon=True).start()
        return session_id

    def _start_new(self, session_id, session):
        try:
            session._load_history()
            session.start()
            session.ready = True
            self._save()
            self._save_pid_map()
            if session.on_event:
                session.on_event(session_id, {"type": "session_ready"})
        except Exception as e:
            logger.error(f"ACP session start failed: {e}")
            if session.on_event:
                session.on_event(session_id, {"type": "session_error", "error": str(e)})
            self.destroy_session(session_id)

    def restore_sessions(self, on_event_factory):
        """Restore sessions from disk after restart."""
        continuation = _pop_continuation()
        saved = _load_sessions_map()
        for fernando_id, info in saved.items():
            # Support old format (string) and new format (dict)
            if isinstance(info, str):
                acp_id, name, backend = info, "Chat-" + fernando_id, "kiro"
                was_loaded = True  # Old format, assume loaded
            else:
                acp_id, name = info["acp_id"], info.get("name", "Chat-" + fernando_id)
                backend = info.get("backend", "kiro")
                was_loaded = info.get("loaded", True)  # Default to loaded for backwards compat
            # For Kiro, check if session file exists; for OpenCode, always try to load if we have acp_id
            if backend == "kiro":
                session_file = os.path.join(KIRO_SESSIONS_DIR, f"{acp_id}.json")
                can_load = os.path.exists(session_file)
            else:
                # OpenCode stores sessions in its SQLite DB, not as JSON files
                # We'll try to load and fall back to new if it fails
                can_load = bool(acp_id)
            session = ACPSession(fernando_id, on_event=on_event_factory(fernando_id), backend=backend)
            session.display_name = name
            session.model = info.get("model", ACPSession.DEFAULT_MODEL) if isinstance(info, dict) else ACPSession.DEFAULT_MODEL
            self._wire_session_status_callback(session)
            with self._lock:
                self.sessions[fernando_id] = session
            # Only load sessions that were loaded before restart
            if can_load and was_loaded:
                session.acp_session_id = acp_id
                logger.info(f"[restore] Loading session {fernando_id} (was loaded)")
                threading.Thread(
                    target=self._load_existing,
                    args=(fernando_id, session, acp_id, continuation),
                    daemon=True,
                ).start()
            elif can_load:
                # Session exists but wasn't loaded — keep it unloaded
                session.acp_session_id = acp_id
                session._load_history()  # Load history for display but don't spawn process
                logger.info(f"[restore] Keeping session {fernando_id} unloaded")
            else:
                threading.Thread(
                    target=self._start_new,
                    args=(fernando_id, session),
                    daemon=True,
                ).start()
        self._recover_orphans()

    def _recover_orphans(self):
        """Auto-archive history files not in active or archived maps."""
        active = set(self.sessions.keys())
        with _archived_lock:
            archived = _load_archived_map()
            tracked = active | set(archived.keys())
            history_ids = {
                os.path.basename(f)[:-6]  # strip .jsonl
                for f in glob.glob(os.path.join(HISTORY_DIR, "*.jsonl"))
            }
            orphaned = history_ids - tracked
            if not orphaned:
                return
            # Get names from RAG
            rag_names = {}
            try:
                coll = rag._get_collection()
                results = coll.get(include=["metadatas"])
                for meta in results["metadatas"]:
                    sid = meta.get("session_id", "")
                    name = meta.get("session_name", "")
                    if sid and name:
                        rag_names.setdefault(sid, name)
            except Exception:
                pass
            for sid in orphaned:
                fpath = os.path.join(HISTORY_DIR, f"{sid}.jsonl")
                # Extract ACP session ID from history events so restore can reload context
                acp_id = ""
                model = ""
                try:
                    with open(fpath) as hf:
                        for line in hf:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except (json.JSONDecodeError, ValueError):
                                continue
                            # session/update notifications contain sessionId in params
                            sid_val = (obj.get("params") or {}).get("sessionId", "")
                            if not sid_val:
                                # session/prompt results contain sessionId in result
                                sid_val = (obj.get("result") or {}).get("sessionId", "")
                            if sid_val:
                                acp_id = sid_val
                            # Also extract model from history events
                            if not model and obj.get("model"):
                                model = obj["model"]
                            if acp_id and model:
                                break
                except OSError:
                    pass
                # Detect backend from acp_id format: OpenCode uses ses_* format
                backend = "opencode" if acp_id.startswith("ses_") else "kiro"
                archived[sid] = {
                    "acp_id": acp_id,
                    "name": rag_names.get(sid, "Chat-" + sid),
                    "backend": backend,
                    "model": model or ACPSession.DEFAULT_MODEL,
                    "archived_at": os.path.getmtime(fpath),
                }
            _save_archived_map(archived)
            logger.info(f"Recovered {len(orphaned)} orphaned sessions into archive")

    def _load_existing(self, session_id, session, acp_session_id, continuation=None):
        try:
            logger.info(f"_load_existing: starting for {session_id} acp={acp_session_id}")
            session.load(acp_session_id)
            session.ready = True
            session._reloading = False
            logger.info(f"_load_existing: session {session_id} ready, history_len={len(session.history)}")
            self._save()  # Persist the loaded=True state
            self._save_pid_map()
            self._broadcast_sessions_list()  # Update sidebar icons
            if session.on_event:
                session.on_event(session_id, {"type": "session_ready"})
            if continuation and continuation.get("session_id") == session_id:
                session.send_continuation(continuation["message"])
        except Exception as e:
            session._reloading = False
            logger.error(f"ACP session load failed for {session_id}: {e}", exc_info=True)
            if session.on_event:
                session.on_event(session_id, {"type": "session_error", "error": str(e)})
            self.destroy_session(session_id, delete_history=False)

    def get_session(self, session_id):
        with self._lock:
            return self.sessions.get(session_id)

    def reload_session(self, session_id):
        """Reload an unloaded session. Returns True if reload started, False if already loaded or not found."""
        session = self.get_session(session_id)
        if not session:
            return False
        if session.is_loaded:
            return False
        if session._reloading:
            return False  # Already reloading, don't start another
        if not session.acp_session_id:
            return False
        session._reloading = True  # Set BEFORE spawning thread to prevent race
        logger.info(f"[reload] Reloading unloaded session {session_id}")
        threading.Thread(
            target=self._load_existing,
            args=(session_id, session, session.acp_session_id),
            daemon=True,
        ).start()
        return True

    def change_model(self, session_id, new_model):
        """Change the model for a session by restarting the kiro-cli process."""
        logger.info(f"change_model: session={session_id} new_model={new_model}")
        session = self.get_session(session_id)
        if not session or not session.acp_session_id:
            return False
        acp_id = session.acp_session_id
        session.model = new_model
        session.ready = False
        session.stop()
        self._save()
        threading.Thread(
            target=self._change_model_reload,
            args=(session_id, session, acp_id),
            daemon=True,
        ).start()
        return True

    def _change_model_reload(self, session_id, session, acp_id):
        """Try to reload existing session; fall back to new session if load fails."""
        try:
            session.load(acp_id)
        except Exception:
            logger.info(f"change_model: session/load failed for {session_id}, falling back to session/new")
            session.stop()
            session._recording = True
            session._broadcasting = True
            try:
                session.start()
            except Exception as e:
                logger.error(f"change_model: session/new also failed for {session_id}: {e}")
                if session.on_event:
                    session.on_event(session_id, {"type": "session_error", "error": str(e)})
                self.destroy_session(session_id, delete_history=False)
                return
        session.ready = True
        self._save()
        self._save_pid_map()
        if session.on_event:
            session.on_event(session_id, {"type": "session_ready"})

    def destroy_session(self, session_id, delete_history=True):
        with self._lock:
            session = self.sessions.pop(session_id, None)
        if session:
            session.stop()
            if delete_history:
                try:
                    os.remove(session._history_path())
                except OSError:
                    pass
        self._save()

    def archive_session(self, session_id):
        """Stop the process and move session from active to archived. History is preserved."""
        with self._lock:
            session = self.sessions.pop(session_id, None)
        if not session:
            return
        acp_id = session.acp_session_id
        name = session.display_name
        backend = session.backend
        model = session.model
        session.stop()
        self._save()
        if acp_id:
            with _archived_lock:
                archived = _load_archived_map()
                archived[session_id] = {
                    "acp_id": acp_id,
                    "name": name,
                    "backend": backend,
                    "model": model,
                    "archived_at": time.time()
                }
                _save_archived_map(archived)

    def list_archived(self):
        self._recover_orphans()
        items = sorted(
            _load_archived_map().items(),
            key=lambda x: x[1].get("archived_at", 0),
            reverse=True,
        )
        return [{"id": sid, "name": info.get("name", "Chat-" + sid)} for sid, info in items]

    def restore_session(self, session_id, on_event=None):
        """Restore an archived session back to active."""
        # Run orphan recovery first in case this session has a history file but isn't tracked
        self._recover_orphans()
        with _archived_lock:
            archived = _load_archived_map()
            info = archived.get(session_id)
            if not info:
                return False
            acp_id = info["acp_id"]
            backend = info.get("backend", "kiro")
            if backend == "kiro":
                can_load = acp_id and os.path.exists(os.path.join(KIRO_SESSIONS_DIR, f"{acp_id}.json"))
            else:
                can_load = bool(acp_id)
            archived.pop(session_id)
            _save_archived_map(archived)
        session = ACPSession(session_id, on_event=on_event, backend=backend)
        session.display_name = info.get("name", "Chat-" + session_id)
        session.model = info.get("model", ACPSession.DEFAULT_MODEL)
        self._wire_session_status_callback(session)
        with self._lock:
            self.sessions[session_id] = session
        if can_load:
            session.acp_session_id = acp_id
            self._save()
            threading.Thread(
                target=self._load_existing,
                args=(session_id, session, acp_id),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._start_new,
                args=(session_id, session),
                daemon=True,
            ).start()
        return True

    def delete_archived(self, session_id):
        """Permanently delete an archived session and its history."""
        with _archived_lock:
            archived = _load_archived_map()
            archived.pop(session_id, None)
            _save_archived_map(archived)
        try:
            os.remove(os.path.join(HISTORY_DIR, f"{session_id}.jsonl"))
        except OSError:
            pass
        # Delete cached images and files for this session
        import shutil
        cache_dir = os.path.join(DATA_DIR, "image_cache", session_id)
        shutil.rmtree(cache_dir, ignore_errors=True)
        file_cache_dir = os.path.join(DATA_DIR, "file_cache", session_id)
        shutil.rmtree(file_cache_dir, ignore_errors=True)
        try:
            rag.delete_session(session_id)
        except Exception as e:
            logger.warning(f"RAG delete error for {session_id}: {e}")

    def list_sessions(self):
        with self._lock:
            result = []
            for sid, s in self.sessions.items():
                parent = get_parent(sid)
                result.append({
                    "id": sid,
                    "name": s.display_name,
                    "history_count": len(s.history),
                    "loaded": s.is_loaded,
                    "status": s.get_status(),
                    "parent_id": parent,
                })
            return result

    def _broadcast_sessions_list(self):
        """Notify websocket layer to broadcast updated sessions list to all clients."""
        logger.info("[acp] _broadcast_sessions_list called, callback set: %s", self._on_sessions_change is not None)
        if self._on_sessions_change:
            self._on_sessions_change()

    def set_on_sessions_change(self, callback):
        """Set callback to be invoked when session list changes (for live UI updates)."""
        self._on_sessions_change = callback

    def set_on_status_change(self, callback):
        """Set callback for session status changes (working/unread/idle)."""
        self._on_status_change = callback

    def _wire_session_status_callback(self, session):
        """Wire up the session's status change callback to the manager's callback."""
        session._on_status_change = self._on_status_change

    def rename_session(self, session_id, new_name):
        with self._lock:
            session = self.sessions.get(session_id)
        if session:
            session.display_name = new_name
            self._save()

    def clone_session(self, source_session_id, on_event=None):
        """Clone a session using Kiro's /rewind to fork at the latest turn.
        
        This creates a true fork with shared conversation context, not just a UI copy.
        """
        source = self.get_session(source_session_id)
        if not source:
            logger.warning(f"[clone] Source session {source_session_id} not found")
            return None
        if not source.is_loaded:
            logger.warning(f"[clone] Source session {source_session_id} not loaded")
            return None
        
        # Get list of turns to find the latest one
        turns_result = source.execute_command("rewind")
        if not turns_result or not turns_result.get("success"):
            logger.warning(f"[clone] Failed to get turns: {turns_result}")
            return None
        
        turns = turns_result.get("data", {}).get("turns", [])
        if not turns:
            logger.warning(f"[clone] No turns available to fork from")
            return None
        
        # Fork at the latest turn (first in the list, highest logIndex)
        latest_turn = turns[0]
        log_index = str(latest_turn.get("logIndex"))
        logger.info(f"[clone] Forking at turn {log_index}: {latest_turn.get('label', '')[:50]}")
        
        fork_result = source.execute_command("rewind", {"value": log_index})
        if not fork_result or not fork_result.get("success"):
            logger.warning(f"[clone] Failed to fork: {fork_result}")
            return None
        
        new_acp_id = fork_result.get("data", {}).get("sessionId")
        if not new_acp_id:
            logger.warning(f"[clone] No sessionId in fork result")
            return None
        
        logger.info(f"[clone] Kiro created forked session: {new_acp_id}")
        
        # Create a new Fernando session that loads the forked Kiro session
        new_id = str(uuid.uuid4())[:8]
        
        # Copy the Fernando history file so UI shows the conversation
        source_history_path = os.path.join(HISTORY_DIR, f"{source_session_id}.jsonl")
        new_history_path = os.path.join(HISTORY_DIR, f"{new_id}.jsonl")
        if os.path.exists(source_history_path):
            shutil.copy2(source_history_path, new_history_path)
            os.chmod(new_history_path, 0o600)
        
        session = ACPSession(new_id, on_event=on_event, backend=source.backend)
        session.model = source.model
        session.display_name = source.display_name + " (fork)"
        session.acp_session_id = new_acp_id
        self._wire_session_status_callback(session)
        
        with self._lock:
            self.sessions[new_id] = session
        
        # Load the forked session instead of starting fresh
        threading.Thread(target=self._load_existing, args=(new_id, session, new_acp_id), daemon=True).start()
        return new_id

    def fork_at_turn(self, source_session_id, turn_index, on_event=None):
        """Fork a session at a specific turn index.
        
        turn_index is the 0-based user message index (1 = first user message).
        Returns the new Fernando session ID, or None on failure.
        """
        source = self.get_session(source_session_id)
        if not source:
            logger.warning(f"[fork_at_turn] Source session {source_session_id} not found")
            return None
        if not source.is_loaded:
            logger.warning(f"[fork_at_turn] Source session {source_session_id} not loaded")
            return None
        
        # Get list of turns from Kiro
        turns_result = source.execute_command("rewind")
        if not turns_result or not turns_result.get("success"):
            logger.warning(f"[fork_at_turn] Failed to get turns: {turns_result}")
            return None
        
        turns = turns_result.get("data", {}).get("turns", [])
        if not turns:
            logger.warning(f"[fork_at_turn] No turns available to fork from")
            return None
        
        # Find the turn with matching index
        # Kiro turns are ordered by logIndex descending (newest first)
        # turn_index is 1-based from the UI (turn 1 = first user message)
        target_turn = None
        for turn in turns:
            if turn.get("logIndex") == turn_index:
                target_turn = turn
                break
        
        if not target_turn:
            logger.warning(f"[fork_at_turn] Turn {turn_index} not found in {len(turns)} turns")
            # Fall back to using the turn_index directly as logIndex
            log_index = str(turn_index)
        else:
            log_index = str(target_turn.get("logIndex"))
        
        logger.info(f"[fork_at_turn] Forking at turn {log_index}")
        
        fork_result = source.execute_command("rewind", {"value": log_index})
        if not fork_result or not fork_result.get("success"):
            logger.warning(f"[fork_at_turn] Failed to fork: {fork_result}")
            return None
        
        new_acp_id = fork_result.get("data", {}).get("sessionId")
        if not new_acp_id:
            logger.warning(f"[fork_at_turn] No sessionId in fork result")
            return None
        
        logger.info(f"[fork_at_turn] Kiro created forked session: {new_acp_id}")
        
        # Create a new Fernando session that loads the forked Kiro session
        new_id = str(uuid.uuid4())[:8]
        
        # Copy the Fernando history file up to the fork point
        # We need to truncate history to only include events up to the forked turn
        source_history_path = os.path.join(HISTORY_DIR, f"{source_session_id}.jsonl")
        new_history_path = os.path.join(HISTORY_DIR, f"{new_id}.jsonl")
        if os.path.exists(source_history_path):
            self._copy_history_up_to_turn(source_history_path, new_history_path, turn_index)
        
        session = ACPSession(new_id, on_event=on_event, backend=source.backend)
        session.model = source.model
        session.display_name = source.display_name + f" (fork@{turn_index})"
        session.acp_session_id = new_acp_id
        self._wire_session_status_callback(session)
        
        with self._lock:
            self.sessions[new_id] = session
        
        # Load the forked session instead of starting fresh
        threading.Thread(target=self._load_existing, args=(new_id, session, new_acp_id), daemon=True).start()
        return new_id

    def _copy_history_up_to_turn(self, source_path, dest_path, turn_index):
        """Copy history events up to and including the specified turn."""
        user_turn_count = 0
        events_to_copy = []
        
        with open(source_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                evt = json.loads(line)
                evt_type = evt.get("type")
                
                # Track user turns
                if evt_type == "user_prompt":
                    user_turn_count += 1
                
                # Stop after we've passed the target turn
                if user_turn_count > turn_index:
                    break
                
                events_to_copy.append(line)
        
        with open(dest_path, 'w') as f:
            for line in events_to_copy:
                f.write(line + '\n')
        
        os.chmod(dest_path, 0o600)
        logger.info(f"[_copy_history_up_to_turn] Copied {len(events_to_copy)} events up to turn {turn_index}")

    def _save(self):
        with self._lock:
            mapping = {
                sid: {
                    "acp_id": s.acp_session_id,
                    "name": s.display_name,
                    "model": s.model,
                    "backend": s.backend,
                    "loaded": s.is_loaded,
                }
                for sid, s in self.sessions.items()
                if s.acp_session_id
            }
        _save_sessions_map(mapping)

    def _save_pid_map(self):
        with self._lock:
            sessions = dict(self.sessions)
        _save_pid_map(sessions)


acp_manager = ACPManager()
