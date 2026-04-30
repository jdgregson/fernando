import logging
import os
import signal
import subprocess
import time

logger = logging.getLogger("fernando.jupyter")

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_NOTEBOOKS_DIR = os.path.join(_project_root, "data", "jupyter")
_PORT = 9999
_process = None


def _venv_python():
    return os.path.join(_project_root, "venv", "bin", "python")


def _ensure_dir():
    os.makedirs(_NOTEBOOKS_DIR, exist_ok=True)


def notebook_dir(name):
    """Return the directory for a named notebook collection."""
    d = os.path.join(_NOTEBOOKS_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def get_port():
    return _PORT


def is_running():
    global _process
    if _process is None:
        return False
    if _process.poll() is not None:
        _process = None
        return False
    return True


def start():
    global _process
    if is_running():
        return True, None
    _ensure_dir()
    try:
        _process = subprocess.Popen(
            [
                _venv_python(), "-m", "notebook",
                "--no-browser",
                f"--port={_PORT}",
                "--ip=127.0.0.1",
                f"--notebook-dir={_NOTEBOOKS_DIR}",
                "--ServerApp.token=",
                "--ServerApp.password=",
                "--ServerApp.disable_check_xsrf=True",
                "--ServerApp.allow_origin=*",
                "--ServerApp.base_url=/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=open("/tmp/fernando-jupyter.log", "a"),
            start_new_session=True,
        )
        # Wait for it to be ready
        for _ in range(30):
            try:
                import requests
                resp = requests.get(f"http://127.0.0.1:{_PORT}/nbclassic/tree/", timeout=1)
                if resp.status_code == 200:
                    logger.info(f"Jupyter started on port {_PORT} (PID {_process.pid})")
                    return True, None
            except Exception:
                pass
            time.sleep(0.5)
        stop()
        return False, "Jupyter started but didn't become ready"
    except Exception as e:
        return False, str(e)


def stop():
    global _process
    if _process is not None:
        try:
            os.killpg(os.getpgid(_process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        _process = None
        logger.info("Jupyter stopped")
