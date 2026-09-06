"""Reward/Debit ledger for tracking agent performance incentives."""

import json
import os
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
REWARDS_FILE = os.path.join(DATA_DIR, "rewards.json")

_lock = threading.Lock()


def _load():
    try:
        with open(REWARDS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"ledger": [], "balance": 0}


def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = REWARDS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, REWARDS_FILE)


def add_entry(amount, note="", session_id=None):
    """Add a reward (positive) or debit (negative) entry."""
    with _lock:
        data = _load()
        entry = {
            "amount": amount,
            "note": note,
            "session_id": session_id,
            "timestamp": time.time(),
        }
        data["ledger"].append(entry)
        data["balance"] = data.get("balance", 0) + amount
        _save(data)
        return entry, data["balance"]


def get_balance():
    """Get current balance."""
    with _lock:
        data = _load()
        return data.get("balance", 0)


def get_ledger(limit=50):
    """Get recent ledger entries (most recent first)."""
    with _lock:
        data = _load()
        ledger = data.get("ledger", [])
        return list(reversed(ledger[-limit:]))
