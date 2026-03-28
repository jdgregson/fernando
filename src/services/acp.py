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


CONTINUATION_FILE = os.path.join(DATA_DIR, "pending_continuation.json")


def _pop_continuation():
    """Read and delete the pending continuation file, if any."""
    try:
        with open(CONTINUATION_FILE) as f:
            data = json.load(f)
        os.remove(CONTINUATION_FILE)
        return data.get("message")
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
        self._next_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._alive = False
        self.history = []
        self.ready = False
        self._recording = True  # gate for _record_event

    def _spawn_and_init(self):
        """Spawn kiro-cli acp and run initialize handshake."""
        self.proc = subprocess.Popen(
            [KIRO_CLI, "acp", "-a"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.expanduser("~/fernando"),
        )
        self._alive = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        resp = self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "fernando-chat", "version": "1.0.0"},
        }, timeout=15)
        if not resp:
            raise RuntimeError("ACP initialize failed")

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

        # Send intro file as initial context (not recorded as user message)
        intro_file = os.path.join(os.path.dirname(__file__), "..", "..", "data", "intro.md")
        if os.path.exists(intro_file):
            with open(intro_file) as f:
                intro = f.read().strip()
            if intro:
                self._recording = False
                self._request("session/prompt", {
                    "sessionId": self.acp_session_id,
                    "prompt": [{"type": "text", "text": intro}],
                }, timeout=60)
                self._recording = True

    def load(self, acp_session_id):
        """Load an existing ACP session (resume after restart)."""
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
        # session/load returns result (possibly null) on success, error on failure
        # The conversation is replayed as notifications before the response
        if resp is None:
            # Check if it was an error (resp would be None from timeout too)
            # If we got here, the request completed — history was replayed
            pass

    def send_prompt(self, text):
        if not self.acp_session_id:
            return
        self.history.append({"type": "user_prompt", "text": text})
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/prompt",
            "params": {
                "sessionId": self.acp_session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        })

    def cancel(self):
        if not self.acp_session_id:
            return
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/cancel",
            "params": {"sessionId": self.acp_session_id},
        })

    def stop(self):
        self._alive = False
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

    def _read_loop(self):
        buf = b""
        while self._alive and self.proc and self.proc.poll() is None:
            try:
                ready, _, _ = select.select([self.proc.stdout], [], [], 0.5)
                if not ready:
                    continue
                chunk = self.proc.stdout.read1(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    self._dispatch(msg)
            except Exception:
                break

        self._alive = False
        if self.on_event:
            try:
                self.on_event(self.id, {"type": "session_ended"})
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
            logger.warning(f"ACP error: {msg.get('error')}")
            return

        self._record_event(msg)
        if self.on_event:
            try:
                self.on_event(self.id, msg)
            except Exception as e:
                logger.error(f"ACP event callback error: {e}")


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
            session.load(acp_session_id)
            session.ready = True
            if session.on_event:
                session.on_event(session_id, {"type": "session_ready"})
            if continuation:
                session.send_prompt(continuation)
        except Exception as e:
            logger.error(f"ACP session load failed for {session_id}: {e}")
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


acp_manager = ACPManager()
