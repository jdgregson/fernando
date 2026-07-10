#!/usr/bin/env python3
"""Shared plumbing for the Fernando MCP servers.

Importing this module first activates the project venv (so third-party deps
resolve even under a bare system python) and exposes a handful of benign
helpers used across the split servers. Deliberately contains NO process-exec,
network-scraping, or system-control code — those live only in the servers that
need them (fernando_system, fernando_web).
"""
import os
import sys

# Activate the project venv so transitive deps (Flask etc.) are available
# even when launched by a bare system python (e.g. from MCP config).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_venv_site = os.path.join(
    PROJECT_ROOT, "venv", "lib",
    f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages",
)
if os.path.isdir(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import urllib.request


def read_api_key():
    """Read the Fernando API key, or '' if unavailable."""
    try:
        return open("/tmp/fernando-api-key").read().strip()
    except Exception:
        return ""


def find_my_session_id():
    """Walk the PID tree to find which ACP chat session owns this process."""
    try:
        with open(os.path.join(PROJECT_ROOT, "data", "acp_pid_map.json")) as f:
            pid_map = json.load(f)
        pid = os.getpid()
        for _ in range(5):
            pid = os.popen(f"ps -o ppid= -p {pid}").read().strip()
            if not pid:
                break
            session_id = pid_map.get(pid)
            if session_id:
                return session_id
    except Exception:
        pass
    return None


def save_continuation(continuation):
    """Save a continuation message for the calling chat session."""
    if not continuation:
        return
    session_id = find_my_session_id()
    with open(os.path.join(PROJECT_ROOT, "data", "pending_continuation.json"), "w") as f:
        json.dump({"message": continuation, "session_id": session_id}, f)


def get_config(key, default=None):
    """Read a config value from env first, then the Fernando config file."""
    val = os.environ.get(key)
    if val is not None:
        return val
    cfg = os.path.join(PROJECT_ROOT, "config")
    if os.path.exists(cfg):
        with open(cfg) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        return v.strip()
    return default
