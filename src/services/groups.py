import json
import os
import threading
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
GROUPS_FILE = os.path.join(DATA_DIR, "project_groups.json")

_lock = threading.Lock()


def _load():
    try:
        with open(GROUPS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"groups": [], "session_groups": {}}


def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = GROUPS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, GROUPS_FILE)


def list_groups():
    with _lock:
        data = _load()
        return data.get("groups", [])


def get_session_groups():
    with _lock:
        data = _load()
        return data.get("session_groups", {})


def get_all():
    with _lock:
        data = _load()
        return {
            "groups": data.get("groups", []),
            "session_groups": data.get("session_groups", {}),
        }


def create_group(name, color="#7ea8e3"):
    with _lock:
        data = _load()
        group_id = str(uuid.uuid4())[:8]
        group = {"id": group_id, "name": name, "color": color}
        data.setdefault("groups", []).append(group)
        _save(data)
        return group


def rename_group(group_id, new_name):
    with _lock:
        data = _load()
        for g in data.get("groups", []):
            if g["id"] == group_id:
                g["name"] = new_name
                _save(data)
                return g
        return None


def set_group_color(group_id, color):
    with _lock:
        data = _load()
        for g in data.get("groups", []):
            if g["id"] == group_id:
                g["color"] = color
                _save(data)
                return g
        return None


def delete_group(group_id):
    with _lock:
        data = _load()
        data["groups"] = [g for g in data.get("groups", []) if g["id"] != group_id]
        session_groups = data.get("session_groups", {})
        data["session_groups"] = {k: v for k, v in session_groups.items() if v != group_id}
        _save(data)


def move_session_to_group(session_key, group_id):
    with _lock:
        data = _load()
        session_groups = data.setdefault("session_groups", {})
        if group_id is None or group_id == "":
            session_groups.pop(session_key, None)
        else:
            session_groups[session_key] = group_id
        _save(data)


def move_sessions_to_group(session_keys, group_id):
    with _lock:
        data = _load()
        session_groups = data.setdefault("session_groups", {})
        for key in session_keys:
            if group_id is None or group_id == "":
                session_groups.pop(key, None)
            else:
                session_groups[key] = group_id
        _save(data)
