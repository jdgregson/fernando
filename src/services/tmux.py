import subprocess
import os
import pty
import termios
import struct
import fcntl
import signal
import re

class TmuxSession:
    def __init__(self):
        self.active_sessions = {}

    def _validate_session_name(self, name):
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            raise ValueError("Invalid session name")
        return name

    def list_sessions(self):
        result = subprocess.run(['tmux', 'list-sessions', '-F', '#{session_name}'],
                               capture_output=True, text=True, timeout=5, check=False)
        return result.stdout.strip().split('\n') if result.returncode == 0 else []

    def create_session(self, name):
        name = self._validate_session_name(name)
        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        subprocess.run(['tmux', 'new-session', '-d', '-s', name], env=env, timeout=5, check=True)
        subprocess.run(['tmux', 'set-option', '-t', name, 'mouse', 'on'], timeout=5, check=True)
        subprocess.run(['tmux', 'set-option', '-t', name, 'history-limit', '10000'], timeout=5, check=True)
        return name

    def create_session_with_type(self, session_type):
        if session_type == 'shell':
            name = 'Shell'
            cmd = None
        elif session_type == 'kiro':
            name = 'Kiro'
            cmd = 'kiro-cli'
        elif session_type == 'kiro-unchained':
            name = 'Kiro-Unchained'
            cmd = 'kiro-cli chat -a'
        else:
            name = 'Shell'
            cmd = None

        # Ensure unique name
        sessions = self.list_sessions()
        if name in sessions:
            i = 2
            while f"{name}-{i}" in sessions:
                i += 1
            name = f"{name}-{i}"

        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        
        if cmd:
            subprocess.run(['tmux', 'new-session', '-d', '-s', name], env=env, timeout=5, check=True)
            subprocess.run(['tmux', 'send-keys', '-t', name, cmd, 'Enter'], timeout=5, check=True)
        else:
            subprocess.run(['tmux', 'new-session', '-d', '-s', name], env=env, timeout=5, check=True)
            
        subprocess.run(['tmux', 'set-option', '-t', name, 'mouse', 'on'], timeout=5, check=True)
        subprocess.run(['tmux', 'set-option', '-t', name, 'history-limit', '10000'], timeout=5, check=True)
        return name

    def attach_session(self, session_name, sid):
        session_name = self._validate_session_name(session_name)
        master, slave = pty.openpty()
        cmd = ['tmux', 'attach-session', '-t', session_name]
        p = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                            preexec_fn=os.setsid, close_fds=True, start_new_session=False)

        self.active_sessions[sid] = {'process': p, 'fd': master}
        os.close(slave)
        return master

    def write_input(self, sid, data):
        if sid in self.active_sessions:
            os.write(self.active_sessions[sid]['fd'], data.encode())

    def resize_terminal(self, sid, rows, cols):
        if sid in self.active_sessions:
            try:
                winsize = struct.pack('HHHH', rows, cols, 0, 0)
                fcntl.ioctl(self.active_sessions[sid]['fd'], termios.TIOCSWINSZ, winsize)
                # Send SIGWINCH to notify the process
                os.kill(self.active_sessions[sid]['process'].pid, signal.SIGWINCH)
            except Exception as e:
                print(f"Resize error: {e}")

    def get_history(self, session_name):
        session_name = self._validate_session_name(session_name)
        result = subprocess.run(['tmux', 'capture-pane', '-t', session_name, '-p', '-S', '-32768'],
                              capture_output=True, text=True, timeout=5, check=False)
        return result.stdout

    def kill_session(self, session_name):
        session_name = self._validate_session_name(session_name)
        subprocess.run(['tmux', 'kill-session', '-t', session_name], timeout=5, check=False)

    def cleanup_session(self, sid):
        if sid in self.active_sessions:
            try:
                os.close(self.active_sessions[sid]['fd'])
                proc = self.active_sessions[sid]['process']
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            except:
                pass
            del self.active_sessions[sid]

    def has_session(self, sid):
        return sid in self.active_sessions

tmux_service = TmuxSession()
