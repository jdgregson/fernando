"""ACP (Agent Client Protocol) service for managing kiro-cli acp subprocesses."""

import json
import logging
import os
import select
import subprocess
import threading
import time
import uuid

logger = logging.getLogger(__name__)


class ACPSession:
    """Manages a single kiro-cli acp subprocess and its ACP session."""

    def __init__(self, session_id, on_event=None):
        self.id = session_id
        self.on_event = on_event  # callback(session_id, event_dict)
        self.proc = None
        self.acp_session_id = None
        self._reader_thread = None
        self._next_id = 0
        self._pending = {}  # id -> threading.Event, result
        self._lock = threading.Lock()
        self._alive = False
        self.history = []  # list of events to replay on reconnect
        self.ready = False

    def start(self):
        """Spawn kiro-cli acp subprocess and initialize the ACP connection."""
        self.proc = subprocess.Popen(
            ["kiro-cli", "acp", "-a"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.expanduser("~/fernando"),
        )
        self._alive = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        # Initialize ACP connection
        resp = self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "fernando-chat", "version": "1.0.0"},
        }, timeout=15)
        if not resp:
            raise RuntimeError("ACP initialize failed")

        # Create ACP session (this can take a while as MCP servers start)
        resp = self._request("session/new", {
            "cwd": os.path.expanduser("~/fernando"),
            "mcpServers": [],
        }, timeout=120)
        if resp and "sessionId" in resp:
            self.acp_session_id = resp["sessionId"]
        else:
            raise RuntimeError("ACP session/new failed")

    def send_prompt(self, text):
        """Send a prompt (fire-and-forget, responses come via notifications)."""
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
        """Cancel the current operation."""
        if not self.acp_session_id:
            return
        self._send({
            "jsonrpc": "2.0",
            "id": self._get_id(),
            "method": "session/cancel",
            "params": {"sessionId": self.acp_session_id},
        })

    def stop(self):
        """Kill the subprocess."""
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
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                logger.error(f"ACP send error: {e}")

    def _request(self, method, params, timeout=30):
        """Send a request and wait for the response by id."""
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
        """Record conversation-relevant events for history replay."""
        method = msg.get("method", "")
        # Record session/update (message chunks, tool calls) and stop responses
        if method == "session/update" or msg.get("result", {}).get("stopReason"):
            self.history.append(msg)

    def _read_loop(self):
        """Read lines from stdout and dispatch."""
        while self._alive and self.proc and self.proc.poll() is None:
            try:
                ready, _, _ = select.select([self.proc.stdout], [], [], 0.5)
                if not ready:
                    continue
                line = self.proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            except Exception:
                break

            # If it's a response to a pending request
            msg_id = msg.get("id")
            if msg_id is not None and "result" in msg:
                with self._lock:
                    if msg_id in self._pending:
                        self._pending[msg_id]["result"] = msg["result"]
                        self._pending[msg_id]["event"].set()
                        continue
                # Not a pending request — forward it (e.g. prompt stopReason)
                self._record_event(msg)
                if self.on_event:
                    try:
                        self.on_event(self.id, msg)
                    except Exception:
                        pass
                continue

            # If it's an error response
            if msg_id is not None and "error" in msg:
                with self._lock:
                    if msg_id in self._pending:
                        self._pending[msg_id]["result"] = None
                        self._pending[msg_id]["event"].set()
                        continue
                logger.warning(f"ACP error: {msg.get('error')}")
                continue

            # Otherwise it's a notification — forward to callback
            self._record_event(msg)
            if self.on_event:
                try:
                    self.on_event(self.id, msg)
                except Exception as e:
                    logger.error(f"ACP event callback error: {e}")

        self._alive = False
        # Notify that session ended
        if self.on_event:
            try:
                self.on_event(self.id, {"type": "session_ended"})
            except Exception:
                pass


class ACPManager:
    """Manages multiple ACP chat sessions."""

    def __init__(self):
        self.sessions = {}  # session_id -> ACPSession
        self._lock = threading.Lock()

    def create_session(self, on_event=None):
        """Create and start a new ACP session. Returns session_id."""
        session_id = str(uuid.uuid4())[:8]
        session = ACPSession(session_id, on_event=on_event)
        with self._lock:
            self.sessions[session_id] = session
        # Start in background thread since it takes a while
        threading.Thread(target=self._start_session, args=(session_id, session), daemon=True).start()
        return session_id

    def _start_session(self, session_id, session):
        try:
            session.start()
            session.ready = True
            if session.on_event:
                session.on_event(session_id, {"type": "session_ready"})
        except Exception as e:
            logger.error(f"ACP session start failed: {e}")
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

    def list_sessions(self):
        with self._lock:
            return list(self.sessions.keys())


acp_manager = ACPManager()
