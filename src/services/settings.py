"""Fernando user settings — stored in data/settings.json."""

import json
import os

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "settings.json")

DEFAULTS = {
    "file_browser_view": "list",
    "file_browser_hidden": False,
    "default_model": "claude-opus-4.6",
    "idle_session_timeout": 300,  # 5 minutes (in seconds)
    "subagents_can_spawn": False,  # Whether subagents can spawn their own subagents
    "health_memory_warning": 65,
    "health_memory_critical": 80,
    "health_disk_warning": 65,
    "health_disk_critical": 80,
    "health_cpu_warning": 65,
    "health_cpu_critical": 80,
    "health_load_warning": 1.5,
    "health_load_critical": 2.0,
    "health_defender_enabled": False,
    "health_defender_target_realTimeProtectionEnabled": None,
    "health_defender_target_behaviorMonitoring": None,
    "health_defender_target_networkProtectionStatus": None,
    "health_defender_target_networkProtectionEnforcementLevel": None,
    "health_defender_target_cloudEnabled": None,
    "health_defender_target_tamperProtectionEnforcementLevel": None,
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


def get_provider_settings():
    settings = get_all()
    return {
        "providers_opencode": settings.get("providers_opencode") is True,
        "default_model": settings["default_model"],
        "opencode_model": settings.get("opencode_model", ""),
    }
