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

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SESSIONS_FILE = os.path.join(DATA_DIR, "chat_sessions.json")
ARCHIVED_FILE = os.path.join(DATA_DIR, "chat_sessions_archived.json")
PID_MAP_FILE = os.path.join(DATA_DIR, "acp_pid_map.json")
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

    def __init__(self, session_id, on_event=None):
        self.id = session_id
        self.on_event = on_event
        self.proc = None
        self.acp_session_id = None
        self.display_name = "Chat-" + session_id
        self.model = self.DEFAULT_MODEL
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
        self._flushed = 0  # number of history entries already written to disk

    def _spawn_and_init(self):
        """Spawn kiro-cli acp and run initialize handshake."""
        logger.info(f"[{self.id}] Spawning kiro-cli acp subprocess")
        self.proc = subprocess.Popen(
            [KIRO_CLI, "acp", "-a", "--model", self.model],
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
                    tid = c.get("data", {}).get("toolUseId")
                    if tid:
                        has_result_for.add(tid)
            if kind == "AssistantMessage":
                for c in obj.get("data", {}).get("content", []):
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
        self._patch_incomplete_mutate(acp_session_id)
        self._load_history()
        self._recording = False  # Don't overwrite rich history with kiro's stripped replay
        self._broadcasting = False  # Don't fire on_event for replay events
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
        if not resp:
            raise RuntimeError(f"session/load failed for {acp_session_id}")
        self._recording = True
        self._broadcasting = True

    def send_prompt(self, text):
        if not self.acp_session_id:
            logger.warning(f"[{self.id}] send_prompt called but no acp_session_id")
            return
        logger.info(f"[{self.id}] send_prompt: {len(text)} chars, alive={self._alive}, proc_poll={self.proc.poll() if self.proc else 'N/A'}, was_prompting={self._is_prompting}")
        if self._is_prompting:
            logger.info(f"[{self.id}] cancelling stuck prompt before sending new one")
            self.cancel()
            time.sleep(0.5)
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
            try:
                rag.index_session(self.id, self.display_name, self.history)
            except Exception as e:
                logger.warning(f"[{self.id}] RAG index error: {e}")

    def _load_history(self):
        self.history = load_history_file(self.id)
        self._flushed = len(self.history)

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
                    self._pending[msg_id]["event"].set()
                    return
            err = msg.get("error", {})
            logger.warning(f"[{self.id}] ACP error: {err}")
            self._is_prompting = False
            if self.on_event and self._broadcasting:
                try:
                    self.on_event(self.id, {"type": "acp_error", "error": err.get("data") or err.get("message", "Unknown error")})
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
    def __init__(self):
        self.sessions = {}
        self._lock = threading.Lock()
        self.default_on_event = None  # Set by websocket.py after register_handlers

    def create_session(self, on_event=None):
        session_id = str(uuid.uuid4())[:8]
        session = ACPSession(session_id, on_event=on_event)
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
                acp_id, name = info, "Chat-" + fernando_id
            else:
                acp_id, name = info["acp_id"], info.get("name", "Chat-" + fernando_id)
            session_file = os.path.join(KIRO_SESSIONS_DIR, f"{acp_id}.json")
            can_load = os.path.exists(session_file)
            session = ACPSession(fernando_id, on_event=on_event_factory(fernando_id))
            session.display_name = name
            session.model = info.get("model", ACPSession.DEFAULT_MODEL) if isinstance(info, dict) else ACPSession.DEFAULT_MODEL
            with self._lock:
                self.sessions[fernando_id] = session
            if can_load:
                session.acp_session_id = acp_id
                threading.Thread(
                    target=self._load_existing,
                    args=(fernando_id, session, acp_id, continuation),
                    daemon=True,
                ).start()
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
                archived[sid] = {
                    "acp_id": "",
                    "name": rag_names.get(sid, "Chat-" + sid),
                    "archived_at": os.path.getmtime(fpath),
                }
            _save_archived_map(archived)
            logger.info(f"Recovered {len(orphaned)} orphaned sessions into archive")

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
            self.destroy_session(session_id, delete_history=False)

    def get_session(self, session_id):
        with self._lock:
            return self.sessions.get(session_id)

    def change_model(self, session_id, new_model):
        """Change the model for a session by restarting the kiro-cli process."""
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
            # Clean up the zombie process and flags left by the failed load()
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
        session.stop()
        self._save()
        if acp_id:
            with _archived_lock:
                archived = _load_archived_map()
                archived[session_id] = {"acp_id": acp_id, "name": name, "archived_at": time.time()}
                _save_archived_map(archived)

    def list_archived(self):
        items = sorted(
            _load_archived_map().items(),
            key=lambda x: x[1].get("archived_at", 0),
            reverse=True,
        )
        return [{"id": sid, "name": info.get("name", "Chat-" + sid)} for sid, info in items]

    def restore_session(self, session_id, on_event=None):
        """Restore an archived session back to active."""
        with _archived_lock:
            archived = _load_archived_map()
            info = archived.get(session_id)
            if not info:
                return False
            acp_id = info["acp_id"]
            # For recovered orphans (no acp_id) or missing session files, start fresh
            can_load = acp_id and os.path.exists(os.path.join(KIRO_SESSIONS_DIR, f"{acp_id}.json"))
            archived.pop(session_id)
            _save_archived_map(archived)
        session = ACPSession(session_id, on_event=on_event)
        session.display_name = info.get("name", "Chat-" + session_id)
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
                sid: {"acp_id": s.acp_session_id, "name": s.display_name, "model": s.model}
                for sid, s in self.sessions.items()
                if s.acp_session_id
            }
        _save_sessions_map(mapping)

    def _save_pid_map(self):
        with self._lock:
            sessions = dict(self.sessions)
        _save_pid_map(sessions)


acp_manager = ACPManager()
