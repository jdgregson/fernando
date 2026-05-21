"""Fernando user settings — stored in data/settings.json."""

import json
import os

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "settings.json")

DEFAULTS = {
    "default_model": "claude-opus-4.6",
}


def _load():
    if os.path.exists(_SETTINGS_FILE):
        with open(_SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def _save(data):
    os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
    with open(_SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get(key):
    data = _load()
    return data.get(key, DEFAULTS.get(key))


def set(key, value):
    data = _load()
    data[key] = value
    _save(data)


def get_all():
    merged = dict(DEFAULTS)
    merged.update(_load())
    return merged
