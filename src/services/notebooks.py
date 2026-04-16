import json
import os
import re
import subprocess
import threading
import time
import logging

logger = logging.getLogger("fernando.notebooks")

_SILVERBULLET_IMAGE = "zefhemel/silverbullet@sha256:6c36ff15f2230dbe3bca7e5d0c85a59c7dc831ce694517850ed5797775824d71"
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_PATH = os.path.join(_project_root, "data", "notebooks.json")
_NOTEBOOKS_DIR = os.path.join(_project_root, "data", "notebooks")
_BASE_PORT = 3001
_MAX_PORT = 3020
_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

_lock = threading.Lock()
# notebook_name -> {"port": int, "container": str}
_running = {}


def _container_name(notebook):
    return f"fernando-notebook-{notebook}"


def _load_config():
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"notebooks": {}}


def _save_config(config):
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def _used_ports():
    """Get ports used by running notebook containers."""
    ports = set()
    for info in _running.values():
        ports.add(info["port"])
    for port in _get_running_containers().values():
        if port:
            ports.add(port)
    return ports


def _allocate_port():
    used = _used_ports()
    for p in range(_BASE_PORT, _MAX_PORT + 1):
        if p not in used:
            return p
    raise RuntimeError("No available ports for notebook containers")


def _init_notebook_dir(notebook):
    """Create notebook directory with default files if it doesn't exist."""
    nb_dir = os.path.join(_NOTEBOOKS_DIR, notebook)
    os.makedirs(nb_dir, exist_ok=True)
    index = os.path.join(nb_dir, "index.md")
    if not os.path.exists(index):
        with open(index, "w") as f:
            f.write(f"# {notebook}\n\nWelcome to the {notebook} notebook.\n")
    settings = os.path.join(nb_dir, "SETTINGS.md")
    if not os.path.exists(settings):
        repo_settings = os.path.join(_project_root, "silverbullet", "SETTINGS.md")
        if os.path.exists(repo_settings):
            import shutil
            shutil.copy2(repo_settings, settings)
        else:
            with open(settings, "w") as f:
                f.write("```yaml\nindexPage: index\n```\n")
    # Copy Library (Atlas plugin etc.) if not present
    lib_dir = os.path.join(nb_dir, "Library")
    if not os.path.exists(lib_dir):
        repo_lib = os.path.join(_project_root, "silverbullet", "Library")
        if os.path.exists(repo_lib):
            import shutil
            shutil.copytree(repo_lib, lib_dir)


def _get_running_containers():
    """Query Docker for running notebook containers and their ports."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=fernando-notebook-",
             "--format", "{{.Names}} {{.Ports}}"],
            capture_output=True, text=True, timeout=5,
        )
        containers = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            name = parts[0].replace("fernando-notebook-", "")
            port = None
            if len(parts) > 1:
                import re
                m = re.search(r':(\d+)->', parts[1])
                if m:
                    port = int(m.group(1))
            containers[name] = port
        return containers
    except Exception:
        return {}


def list_notebooks():
    config = _load_config()
    running = _get_running_containers()
    result = []
    for name in config.get("notebooks", {}):
        port = running.get(name)
        result.append({
            "name": name,
            "running": port is not None,
            "port": port,
        })
    return result


def create_notebook(name):
    if not _VALID_NAME.match(name):
        return None, "Invalid name: lowercase alphanumeric, hyphens, underscores only"
    config = _load_config()
    if name in config.get("notebooks", {}):
        return None, f"Notebook '{name}' already exists"
    config.setdefault("notebooks", {})[name] = {"name": name}
    _save_config(config)
    _init_notebook_dir(name)
    return {"name": name, "running": False, "port": None}, None


def delete_notebook(name):
    config = _load_config()
    if name not in config.get("notebooks", {}):
        return f"Notebook '{name}' not found"
    if name in _running:
        stop_notebook(name)
    del config["notebooks"][name]
    _save_config(config)
    # Don't delete the data directory — just remove from config
    return None


def start_notebook(name):
    with _lock:
        config = _load_config()
        if name not in config.get("notebooks", {}):
            return None, f"Notebook '{name}' not found"
        if name in _running:
            return _running[name], None

        # Check if container is already running (e.g. survived a Flask restart)
        existing_port = _get_running_containers().get(name)
        if existing_port:
            info = {"port": existing_port, "container": _container_name(name)}
            _running[name] = info
            logger.info(f"Re-registered existing notebook '{name}' on port {existing_port}")
            return info, None

        port = _allocate_port()
        container = _container_name(name)
        nb_dir = os.path.join(_NOTEBOOKS_DIR, name)
        _init_notebook_dir(name)

        # Stop any leftover container with this name
        subprocess.run(["docker", "rm", "-f", container],
                       capture_output=True, timeout=10)

        result = subprocess.run(
            ["docker", "run", "-d",
             "--name", container,
             "-p", f"127.0.0.1:{port}:3000",
             "-v", f"{nb_dir}:/space",
             _SILVERBULLET_IMAGE],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None, f"Failed to start container: {result.stderr.strip()}"

        # Wait for SilverBullet to respond
        ready = False
        for _ in range(30):
            try:
                import requests as req
                resp = req.get(f"http://localhost:{port}/", timeout=1)
                if resp.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.2)

        if not ready:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            return None, "Container started but SilverBullet didn't become ready"

        info = {"port": port, "container": container}
        _running[name] = info
        logger.info(f"Started notebook '{name}' on port {port}")
        return info, None


def stop_notebook(name):
    with _lock:
        info = _running.pop(name, None)
        if not info:
            return
        container = info["container"]
        subprocess.run(["docker", "rm", "-f", container],
                       capture_output=True, timeout=10)
        logger.info(f"Stopped notebook '{name}'")


def get_notebook_port(name):
    info = _running.get(name)
    if info:
        return info["port"]
    # Fallback: check Docker directly (survives Flask restarts)
    port = _get_running_containers().get(name)
    if port:
        _running[name] = {"port": port, "container": _container_name(name)}
    return port


def stop_all():
    """Stop all running notebook containers. Called on shutdown."""
    for name in list(_running.keys()):
        stop_notebook(name)


notebook_service = type("NotebookService", (), {
    "list_notebooks": staticmethod(list_notebooks),
    "create_notebook": staticmethod(create_notebook),
    "delete_notebook": staticmethod(delete_notebook),
    "start_notebook": staticmethod(start_notebook),
    "stop_notebook": staticmethod(stop_notebook),
    "get_notebook_port": staticmethod(get_notebook_port),
    "stop_all": staticmethod(stop_all),
})()
