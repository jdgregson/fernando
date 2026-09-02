from src import create_app, socketio
from src.config import config
from src.services.pty_service import pty_service
import os
import signal
import logging


class SingleLineFormatter(logging.Formatter):
    """Format log records with newlines escaped so each record is one line."""
    def format(self, record):
        msg = super().format(record)
        msg = msg.replace('\n', '\\n').replace('\r', '\\r')
        # Redact api_key from URLs
        import re
        msg = re.sub(r'api_key=[a-fA-F0-9]+', 'api_key=REDACTED', msg)
        return msg


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# Replace default formatter with single-line formatter on all handlers
for handler in logging.getLogger().handlers:
    handler.setFormatter(SingleLineFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))

logger = logging.getLogger("fernando")

app = create_app()

# Restore any saved terminal sessions from previous run
pty_service.restore_all()


_reaped_pids = []


def _reap_children():
    """Reap any zombie child processes. Signal-safe (no I/O)."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
            _reaped_pids.append(pid)
        except ChildProcessError:
            break


def _log_reaped():
    """Log reaped PIDs outside of signal context. Call from main thread."""
    while _reaped_pids:
        pid = _reaped_pids.pop(0)
        logger.info(f"Reaped zombie child pid={pid}")


def _shutdown(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    pty_service.cleanup_all()
    try:
        from src.services.jupyter import stop as jupyter_stop
        jupyter_stop()
    except Exception:
        pass
    _reap_children()
    raise SystemExit(0)


# Install SIGCHLD handler to auto-reap children
def _sigchld_handler(signum, frame):
    _reap_children()


signal.signal(signal.SIGCHLD, _sigchld_handler)
signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

if __name__ == "__main__":
    env = os.environ.get("FLASK_ENV", "development")
    cfg = config[env]

    # Periodically drain reaped PID log outside signal context
    import threading
    def _reap_logger():
        while True:
            _log_reaped()
            import time
            time.sleep(5)
    threading.Thread(target=_reap_logger, daemon=True).start()

    socketio.run(
        app,
        host=cfg.HOST,
        port=cfg.PORT,
        debug=cfg.DEBUG,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
