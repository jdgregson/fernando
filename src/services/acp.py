"""ACP (Agent Client Protocol) service for managing kiro-cli acp subprocesses."""

import json
import logging
import os
import select
import subprocess
import threading
import time
import shutil
import uuid

logger = logging.getLogger(__name__)

KIRO_CLI = shutil.which("kiro-cli") or os.path.expanduser("~/.local/bin/kiro-cli")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SESSIONS_FILE = os.path.join(DATA_DIR, "chat_sessions.json")
PID_MAP_FILE = os.path.join(DATA_DIR, "acp_pid_map.json")
HISTORY_DIR = os.path.join(DATA_DIR, "chat_history")
KIRO_SESSIONS_DIR = os.path.expanduser("~/.kiro/sessions/cli")


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

    def __init__(self, session_id, on_event=None):
        self.id = session_id
        self.on_event = on_event
        self.proc = None
        self.acp_session_id = None
        self.display_name = "Chat-" + session_id
        self._reader_thread = None
        self._stderr_thread = None
        self._next_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._alive = False
        self.history = []
        self.ready = False
        self._recording = True  # gate for _record_event
        self._last_activity = time.time()  # track last stdout data for stall detection
        self._is_prompting = False  # True while waiting for agent response

    def _spawn_and_init(self):
        """Spawn kiro-cli acp and run initialize handshake."""
        logger.info(f"[{self.id}] Spawning kiro-cli acp subprocess")
        self.proc = subprocess.Popen(
            [KIRO_CLI, "acp", "-a", "--model", "claude-opus-4.6"],
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
        }, timeout=15)
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
        if resp and "sessionId" in resp:
            self.acp_session_id = resp["sessionId"]
        else:
            raise RuntimeError("ACP session/new failed")

    def load(self, acp_session_id):
        """Load an existing ACP session (resume after restart)."""
        self._load_history()
        self._recording = False  # Don't overwrite rich history with kiro's stripped replay
        self._spawn_and_init()
        self.acp_session_id = acp_session_id

        # Remove stale lock file
        lock_file = os.path.join(KIRO_SESSIONS_DIR, f"{acp_session_id}.lock")
        try:
            os.remove(lock_file)
        except OSError:
            pass

        resp = self._request("session/load", {
            "sessionId": acp_session_id,
            "cwd": os.path.expanduser("~/fernando"),
            "mcpServers": [],
        }, timeout=120)
        self._recording = True

    def send_prompt(self, text):
        if not self.acp_session_id:
            logger.warning(f"[{self.id}] send_prompt called but no acp_session_id")
            return
        logger.info(f"[{self.id}] send_prompt: {len(text)} chars, alive={self._alive}, proc_poll={self.proc.poll() if self.proc else 'N/A'}")
        self._is_prompting = True
        self._last_activity = time.time()
        self.history.append({"type": "user_prompt", "text": text})
        self._save_history()
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
        """Send a prompt that displays as a system message, not a user message."""
        if not self.acp_session_id:
            return
        logger.info(f"[{self.id}] send_continuation: {len(text)} chars")
        self._is_prompting = True
        self._last_activity = time.time()
        prefixed = "[CONTINUATION] " + text
        evt = {"type": "continuation", "text": prefixed}
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

    def stop(self):
        self._alive = False
        self._is_prompting = False
        self._save_history()
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
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "result": None}
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        event.wait(timeout=timeout)
        with self._lock:
            entry = self._pending.pop(req_id, {})
        return entry.get("result")

    def _record_event(self, msg):
        if not self._recording:
            return
        method = msg.get("method", "")
        if method == "session/update" or msg.get("result", {}).get("stopReason"):
            self.history.append(msg)
            self._save_history()

    def _history_path(self):
        return os.path.join(HISTORY_DIR, f"{self.id}.json")

    def _save_history(self):
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            tmp = self._history_path() + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.history, f)
            os.replace(tmp, self._history_path())
        except Exception:
            pass

    def _load_history(self):
        try:
            with open(self._history_path()) as f:
                self.history = json.load(f)
        except Exception:
            pass

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
        except Exception:
            pass

    def _dispatch(self, msg):
        msg_id = msg.get("id")

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
            self._record_event(msg)
            if self.on_event:
                try:
                    self.on_event(self.id, msg)
                except Exception:
                    pass
            return

        if msg_id is not None and "error" in msg:
            with self._lock:
                if msg_id in self._pending:
                    self._pending[msg_id]["result"] = None
                    self._pending[msg_id]["event"].set()
                    return
            logger.warning(f"[{self.id}] ACP error: {msg.get('error')}")
            self._is_prompting = False
            return

        # Notification (no id) — log session/update type
        params = msg.get("params", {})
        su = (params.get("update") or {}).get("sessionUpdate", "")
        if su and su != "agent_message_chunk":
            logger.debug(f"[{self.id}] session/update: {su}")

        self._record_event(msg)
        if self.on_event:
            try:
                self.on_event(self.id, msg)
            except Exception as e:
                logger.error(f"[{self.id}] ACP event callback error: {e}")


class ACPManager:
    def __init__(self):
        self.sessions = {}
        self._lock = threading.Lock()

    def create_session(self, on_event=None):
        session_id = str(uuid.uuid4())[:8]
        session = ACPSession(session_id, on_event=on_event)
        with self._lock:
            self.sessions[session_id] = session
        threading.Thread(target=self._start_new, args=(session_id, session), daemon=True).start()
        return session_id

    def _start_new(self, session_id, session):
        try:
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
                acp_id, name = info, "Chat-" + fernando_id
            else:
                acp_id, name = info["acp_id"], info.get("name", "Chat-" + fernando_id)
            session_file = os.path.join(KIRO_SESSIONS_DIR, f"{acp_id}.json")
            if not os.path.exists(session_file):
                continue
            session = ACPSession(fernando_id, on_event=on_event_factory(fernando_id))
            session.display_name = name
            session.acp_session_id = acp_id  # Set before thread so _save() won't drop it
            with self._lock:
                self.sessions[fernando_id] = session
            threading.Thread(
                target=self._load_existing,
                args=(fernando_id, session, acp_id, continuation),
                daemon=True,
            ).start()

    def _load_existing(self, session_id, session, acp_session_id, continuation=None):
        try:
            logger.info(f"_load_existing: starting for {session_id} acp={acp_session_id}")
            session.load(acp_session_id)
            session.ready = True
            logger.info(f"_load_existing: session {session_id} ready, history_len={len(session.history)}")
            self._save_pid_map()
            if session.on_event:
                session.on_event(session_id, {"type": "session_ready"})
            if continuation and continuation.get("session_id") == session_id:
                session.send_continuation(continuation["message"])
        except Exception as e:
            logger.error(f"ACP session load failed for {session_id}: {e}", exc_info=True)
            if session.on_event:
                session.on_event(session_id, {"type": "session_error", "error": str(e)})
            self.destroy_session(session_id)

    def get_session(self, session_id):
        with self._lock:
            return self.sessions.get(session_id)

    def destroy_session(self, session_id):
        with self._lock:
            session = self.sessions.pop(session_id, None)
        if session:
            session.stop()
            try:
                os.remove(session._history_path())
            except OSError:
                pass
        self._save()

    def list_sessions(self):
        with self._lock:
            return [{"id": sid, "name": s.display_name} for sid, s in self.sessions.items()]

    def rename_session(self, session_id, new_name):
        with self._lock:
            session = self.sessions.get(session_id)
        if session:
            session.display_name = new_name
            self._save()

    def _save(self):
        with self._lock:
            mapping = {
                sid: {"acp_id": s.acp_session_id, "name": s.display_name}
                for sid, s in self.sessions.items()
                if s.acp_session_id
            }
        _save_sessions_map(mapping)

    def _save_pid_map(self):
        with self._lock:
            sessions = dict(self.sessions)
        _save_pid_map(sessions)


acp_manager = ACPManager()
