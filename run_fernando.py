from src import create_app, socketio
from src.config import config
from src.services.pty_service import pty_service
import os
import signal
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("fernando")

app = create_app()

# Restore any saved terminal sessions from previous run
pty_service.restore_all()


def _reap_children():
    """Reap any zombie child processes."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
            logger.info(f"Reaped zombie child pid={pid}")
        except ChildProcessError:
            break


def _shutdown(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    pty_service.cleanup_all()
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
    socketio.run(
        app,
        host=cfg.HOST,
        port=cfg.PORT,
        debug=cfg.DEBUG,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
