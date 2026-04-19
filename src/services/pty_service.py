import os
import pty
import termios
import struct
import fcntl
import signal
import re
import json
import logging
import threading

logger = logging.getLogger("fernando.pty")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sessions")


class PTYSession:
    """Manages direct PTY sessions without tmux."""

    def __init__(self):
        # session_name -> {process, fd, type, cmd, scrollback_buf}
        self.sessions = {}
        # viewer_id -> {session_name, fd}  (browser tab connections)
        self.viewers = {}
        self._lock = threading.Lock()
        os.makedirs(DATA_DIR, exist_ok=True)

    def _validate_name(self, name):
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError("Invalid session name")
        return name

    def list_sessions(self):
        with self._lock:
            return list(self.sessions.keys())

    def create_session(self, session_type):
        """Create a new PTY session. Returns the session name."""
        kiro_model = "claude-opus-4.6"
        if session_type == "shell":
            name = "Shell"
            cmd = ["bash", "-l"]
        elif session_type == "kiro":
            name = "Kiro"
            cmd = ["bash", "-lc", f"exec kiro-cli chat --legacy-ui --model {kiro_model}"]
        elif session_type == "kiro-unchained":
            name = "Kiro-CLI"
            cmd = ["bash", "-lc", f"exec kiro-cli chat --legacy-ui -a --model {kiro_model}"]
        else:
            name = "Shell"
            cmd = ["bash", "-l"]

        with self._lock:
            existing = set(self.sessions.keys())
        if name in existing:
            i = 2
            while f"{name}-{i}" in existing:
                i += 1
            name = f"{name}-{i}"

        self._spawn(name, session_type, cmd)
        return name

    def _spawn(self, name, session_type, cmd):
        """Spawn a process on a new PTY."""
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"

        master, slave = pty.openpty()
        proc = __import__("subprocess").Popen(
            cmd,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=os.setsid,
            close_fds=True,
            start_new_session=False,
            env=env,
        )
        os.close(slave)

        with self._lock:
            self.sessions[name] = {
                "process": proc,
                "fd": master,
                "type": session_type,
                "cmd": cmd,
                "scrollback": bytearray(),
                "ever_attached": False,
            }
        logger.info(f"Spawned session={name} type={session_type} pid={proc.pid}")

        # Start background reader that buffers scrollback
        t = threading.Thread(target=self._reader_loop, args=(name,), daemon=True)
        t.start()

    def _reader_loop(self, name):
        """Read PTY output, buffer scrollback, forward to viewers."""
        MAX_SCROLLBACK = 512 * 1024  # 512KB
        while True:
            with self._lock:
                session = self.sessions.get(name)
                if not session:
                    break
                fd = session["fd"]

            try:
                import select
                r, _, _ = select.select([fd], [], [], 0.1)
                if not r:
                    # Check if process is still alive
                    with self._lock:
                        session = self.sessions.get(name)
                        if session and session["process"].poll() is not None:
                            break
                    continue
                data = os.read(fd, 65536)
                if not data:
                    break
            except (OSError, ValueError):
                break

            with self._lock:
                session = self.sessions.get(name)
                if not session:
                    break
                buf = session["scrollback"]
                buf.extend(data)
                if len(buf) > MAX_SCROLLBACK:
                    del buf[: len(buf) - MAX_SCROLLBACK]

            # Forward to all viewers of this session
            self._broadcast(name, data)

        logger.info(f"Reader loop ended for session={name}")

    def _broadcast(self, session_name, data):
        """Send data to all viewers attached to a session."""
        with self._lock:
            viewers = [
                (vid, v) for vid, v in self.viewers.items()
                if v["session_name"] == session_name
            ]
        for vid, v in viewers:
            cb = v.get("callback")
            if cb:
                try:
                    cb(data)
                except Exception as e:
                    logger.warning(f"Broadcast error to viewer {vid}: {e}")

    def attach_viewer(self, viewer_id, session_name, callback):
        """Attach a browser tab to a session. callback(bytes) is called for output.
        Returns the current scrollback buffer to replay."""
        session_name = self._validate_name(session_name)
        self.detach_viewer(viewer_id)

        with self._lock:
            session = self.sessions.get(session_name)
            if not session:
                raise ValueError(f"Session {session_name} not found")
            self.viewers[viewer_id] = {
                "session_name": session_name,
                "callback": callback,
            }
            # Return current scrollback for replay
            session["ever_attached"] = True
            scrollback = bytes(session["scrollback"])
        return scrollback

    def detach_viewer(self, viewer_id):
        with self._lock:
            self.viewers.pop(viewer_id, None)

    def write_input(self, viewer_id, data):
        with self._lock:
            viewer = self.viewers.get(viewer_id)
            if not viewer:
                return
            session = self.sessions.get(viewer["session_name"])
            if not session:
                return
            fd = session["fd"]
        try:
            os.write(fd, data.encode() if isinstance(data, str) else data)
        except OSError as e:
            logger.warning(f"Write error for viewer {viewer_id}: {e}")

    def resize(self, viewer_id, rows, cols):
        with self._lock:
            viewer = self.viewers.get(viewer_id)
            if not viewer:
                return
            session = self.sessions.get(viewer["session_name"])
            if not session:
                return
            fd = session["fd"]
            pid = session["process"].pid
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            os.kill(pid, signal.SIGWINCH)
        except Exception as e:
            logger.warning(f"Resize error: {e}")

    def rename_session(self, old_name, new_name):
        old_name = self._validate_name(old_name)
        new_name = self._validate_name(new_name)
        with self._lock:
            if old_name not in self.sessions:
                raise ValueError(f"Session {old_name} not found")
            if new_name in self.sessions:
                raise ValueError(f"Session {new_name} already exists")
            self.sessions[new_name] = self.sessions.pop(old_name)
            for v in self.viewers.values():
                if v["session_name"] == old_name:
                    v["session_name"] = new_name
        return new_name

    def kill_session(self, name):
        name = self._validate_name(name)
        with self._lock:
            session = self.sessions.pop(name, None)
            # Remove viewers for this session
            to_remove = [vid for vid, v in self.viewers.items() if v["session_name"] == name]
            for vid in to_remove:
                self.viewers.pop(vid, None)
        if session:
            try:
                os.close(session["fd"])
            except OSError:
                pass
            try:
                session["process"].terminate()
                try:
                    session["process"].wait(timeout=2)
                except __import__("subprocess").TimeoutExpired:
                    session["process"].kill()
                    session["process"].wait(timeout=5)
            except Exception as e:
                logger.warning(f"Error killing session {name}: {e}")

    def has_viewer(self, viewer_id):
        with self._lock:
            return viewer_id in self.viewers

    def save_all(self):
        """Save scrollback and metadata for all sessions (called on shutdown)."""
        with self._lock:
            sessions_copy = {
                name: {
                    "type": s["type"],
                    "cmd": s["cmd"],
                    "scrollback": bytes(s["scrollback"]),
                    "cwd": self._get_cwd(s["process"].pid),
                }
                for name, s in self.sessions.items()
            }

        for name, info in sessions_copy.items():
            session_dir = os.path.join(DATA_DIR, name)
            os.makedirs(session_dir, exist_ok=True)
            # Save scrollback
            with open(os.path.join(session_dir, "scrollback.raw"), "wb") as f:
                f.write(info["scrollback"])
            # Save metadata
            meta = {"type": info["type"], "cmd": info["cmd"], "cwd": info["cwd"]}
            with open(os.path.join(session_dir, "meta.json"), "w") as f:
                json.dump(meta, f)
            logger.info(f"Saved session {name}: {len(info['scrollback'])} bytes scrollback")

    def _get_cwd(self, pid):
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except (OSError, FileNotFoundError):
            return os.path.expanduser("~")

    def restore_all(self):
        """Restore sessions from saved state (called on startup)."""
        if not os.path.isdir(DATA_DIR):
            return
        for name in os.listdir(DATA_DIR):
            session_dir = os.path.join(DATA_DIR, name)
            meta_file = os.path.join(session_dir, "meta.json")
            scrollback_file = os.path.join(session_dir, "scrollback.raw")
            if not os.path.isfile(meta_file):
                continue
            try:
                with open(meta_file) as f:
                    meta = json.load(f)
                scrollback = b""
                if os.path.isfile(scrollback_file):
                    with open(scrollback_file, "rb") as f:
                        scrollback = f.read()

                session_type = meta.get("type", "shell")
                cmd = meta.get("cmd", ["bash", "-l"])
                cwd = meta.get("cwd", os.path.expanduser("~"))

                # Build a command that cd's to the saved cwd first
                if session_type == "shell":
                    cmd = ["bash", "-lc", f"cd {_shell_quote(cwd)} 2>/dev/null; exec bash -l"]
                elif cmd and len(cmd) >= 3:
                    # For kiro sessions, prepend cd
                    inner = cmd[-1] if cmd[-1].startswith("exec ") else f"exec {cmd[-1]}"
                    cmd = ["bash", "-lc", f"cd {_shell_quote(cwd)} 2>/dev/null; {inner}"]

                self._spawn(name, session_type, cmd)

                # Don't replay saved scrollback — raw escape sequences from a
                # different terminal size cause blank lines and data corruption.
                # Session restores in the right directory with bash history intact.

                logger.info(f"Restored session {name} ({len(scrollback)} bytes scrollback)")
            except Exception as e:
                logger.error(f"Failed to restore session {name}: {e}")
            finally:
                # Clean up saved state
                try:
                    os.remove(meta_file)
                except OSError:
                    pass
                try:
                    os.remove(scrollback_file)
                except OSError:
                    pass
                try:
                    os.rmdir(session_dir)
                except OSError:
                    pass

    def cleanup_all(self):
        """Save state and kill all sessions (called on shutdown)."""
        self.save_all()
        with self._lock:
            names = list(self.sessions.keys())
        for name in names:
            self.kill_session(name)


def _shell_quote(s):
    """Shell-escape a string."""
    return "'" + s.replace("'", "'\\''") + "'"


pty_service = PTYSession()
