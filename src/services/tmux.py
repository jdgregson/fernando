import subprocess
import os
import pty
import termios
import struct
import fcntl
import signal

class TmuxSession:
    def __init__(self):
        self.active_sessions = {}
    
    def list_sessions(self):
        result = subprocess.run(['tmux', 'list-sessions', '-F', '#{session_name}'], 
                               capture_output=True, text=True)
        return result.stdout.strip().split('\n') if result.returncode == 0 else []
    
    def create_session(self, name):
        subprocess.run(['tmux', 'new-session', '-d', '-s', name])
        return name
    
    def attach_session(self, session_name, sid):
        master, slave = pty.openpty()
        cmd = ['tmux', 'attach-session', '-t', session_name]
        p = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, 
                            preexec_fn=os.setsid, close_fds=True)
        
        self.active_sessions[sid] = {'process': p, 'fd': master}
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
        result = subprocess.run(['tmux', 'capture-pane', '-t', session_name, '-p', '-S', '-32768'],
                              capture_output=True, text=True)
        return result.stdout
    
    def kill_session(self, session_name):
        subprocess.run(['tmux', 'kill-session', '-t', session_name])
    
    def cleanup_session(self, sid):
        if sid in self.active_sessions:
            try:
                os.close(self.active_sessions[sid]['fd'])
                self.active_sessions[sid]['process'].terminate()
            except:
                pass
            del self.active_sessions[sid]
    
    def has_session(self, sid):
        return sid in self.active_sessions

tmux_service = TmuxSession()
