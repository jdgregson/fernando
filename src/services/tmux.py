import subprocess
import os
import pty
import termios
import struct
import fcntl
import signal
import re
import logging

logger = logging.getLogger("fernando.tmux")


class TmuxSession:
    def __init__(self):
        self.active_sessions = {}

    def _validate_session_name(self, name):
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError("Invalid session name")
        return name

    def list_sessions(self):
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip().split("\n") if result.returncode == 0 else []

    def create_session(self, name):
        name = self._validate_session_name(name)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name], env=env, timeout=5, check=True
        )
        subprocess.run(
            ["tmux", "set-option", "-t", name, "mouse", "on"], timeout=5, check=True
        )
        subprocess.run(
            ["tmux", "set-option", "-t", name, "history-limit", "10000"],
            timeout=5,
            check=True,
        )
        subprocess.run(
            ["tmux", "set-option", "-t", name, "status-style", "bg=blue,fg=white"],
            timeout=5,
            check=True,
        )
        return name

    def create_session_with_type(self, session_type):
        kiro_model = "claude-opus-4.6"

        if session_type == "shell":
            name = "Shell"
            cmd = None
        elif session_type == "kiro":
            name = "Kiro"
            cmd = f"kiro-cli chat --legacy-ui --model {kiro_model}"
        elif session_type == "kiro-unchained":
            name = "Kiro-CLI"
            cmd = f"kiro-cli chat --legacy-ui -a --model {kiro_model}"
        else:
            name = "Shell"
            cmd = None

        # Ensure unique name
        sessions = self.list_sessions()
        if name in sessions:
            i = 2
            while f"{name}-{i}" in sessions:
                i += 1
            name = f"{name}-{i}"

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"

        shell_cmd = ["tmux", "new-session", "-d", "-s", name]
        if cmd:
            shell_cmd += ["bash", "-lc", f"exec {cmd}"]
        else:
            shell_cmd += ["bash", "-l"]

        subprocess.run(shell_cmd, env=env, timeout=5, check=True)

        subprocess.run(
            ["tmux", "set-option", "-t", name, "mouse", "on"], timeout=5, check=True
        )
        subprocess.run(
            ["tmux", "set-option", "-t", name, "history-limit", "10000"],
            timeout=5,
            check=True,
        )
        subprocess.run(
            ["tmux", "set-option", "-t", name, "status-style", "bg=blue,fg=white"],
            timeout=5,
            check=True,
        )
        return name

    def attach_session(self, session_name, sid):
        session_name = self._validate_session_name(session_name)
        # Clean up any existing session for this sid before creating a new one
        if sid in self.active_sessions:
            logger.info(f"Cleaning up stale session before reattach: sid={sid}")
            self.cleanup_session(sid)

        master, slave = pty.openpty()
        cmd = ["tmux", "attach-session", "-t", session_name]
        p = subprocess.Popen(
            cmd,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=os.setsid,
            close_fds=True,
            start_new_session=False,
        )

        self.active_sessions[sid] = {"process": p, "fd": master}
        os.close(slave)
        logger.info(f"Attached session={session_name} sid={sid} pid={p.pid}")
        return master

    def write_input(self, sid, data):
        if sid in self.active_sessions:
            os.write(self.active_sessions[sid]["fd"], data.encode())

    def resize_terminal(self, sid, rows, cols):
        if sid in self.active_sessions:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(
                    self.active_sessions[sid]["fd"], termios.TIOCSWINSZ, winsize
                )
                # Send SIGWINCH to notify the process
                os.kill(self.active_sessions[sid]["process"].pid, signal.SIGWINCH)
            except Exception as e:
                print(f"Resize error: {e}")

    def rename_session(self, old_name, new_name):
        old_name = self._validate_session_name(old_name)
        new_name = self._validate_session_name(new_name)
        subprocess.run(
            ["tmux", "rename-session", "-t", old_name, new_name],
            timeout=5,
            check=True,
        )
        return new_name

    def kill_session(self, session_name):
        session_name = self._validate_session_name(session_name)
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name], timeout=5, check=False
        )

    def cleanup_session(self, sid):
        if sid in self.active_sessions:
            session = self.active_sessions.pop(sid)
            proc = session["process"]
            fd = session["fd"]
            logger.info(f"Cleaning up sid={sid} pid={proc.pid}")
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                    logger.info(f"Process pid={proc.pid} terminated cleanly")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    logger.info(f"Process pid={proc.pid} killed after timeout")
            except Exception as e:
                logger.warning(f"Error cleaning up pid={proc.pid}: {e}")
                # Last resort: reap if possible
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

    def cleanup_all_sessions(self):
        """Clean up all active sessions. Called on shutdown."""
        sids = list(self.active_sessions.keys())
        logger.info(f"Cleaning up all {len(sids)} active sessions")
        for sid in sids:
            self.cleanup_session(sid)

    def has_session(self, sid):
        return sid in self.active_sessions


tmux_service = TmuxSession()
