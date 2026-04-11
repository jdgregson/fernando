"""Inbound message workflow engine.

Rules are evaluated top-to-bottom; first match wins. Default is drop.
Agent-created rules are constrained by a meta-policy file that the owner
maintains. The engine enforces those constraints — the agent never sees
messages that don't pass.
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("fernando.workflows")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
RULES_FILE = os.path.join(DATA_DIR, "workflow_rules.json")
HISTORY_FILE = os.path.join(DATA_DIR, "workflow_history.json")
META_POLICY_FILE = os.path.join(DATA_DIR, "workflow_meta_policy.json")

# Graph API helpers — imported lazily to avoid circular deps
_graph_request = None


def _get_graph_request():
    global _graph_request
    if _graph_request is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "microsoft_mcp",
            os.path.join(os.path.dirname(__file__), "..", "..", "mcp_servers", "microsoft_mcp.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _graph_request = mod.graph_request
    return _graph_request


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Meta-policy (owner-controlled constraints on agent-created rules)
# ---------------------------------------------------------------------------

DEFAULT_META_POLICY = {
    "allowed_actions": ["dispatch", "summary"],
    "allowed_domains": [],
    "max_ttl_hours": 72,
    "max_active_agent_rules": 10,
    "require_fire_once_for_agent": True,
}


def load_meta_policy():
    return _load_json(META_POLICY_FILE, DEFAULT_META_POLICY)


def save_meta_policy(policy):
    _save_json(META_POLICY_FILE, policy)


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------

def _load_rules():
    return _load_json(RULES_FILE, [])


def _save_rules(rules):
    _save_json(RULES_FILE, rules)


def validate_rule(rule, meta_policy=None):
    """Return (ok, error_message). Enforces meta-policy for agent-created rules."""
    if not rule.get("match"):
        return False, "Rule must have a 'match' block"
    if rule.get("action") not in ("dispatch", "summary", "drop"):
        return False, "action must be dispatch, summary, or drop"
    if rule.get("created_by") == "agent":
        mp = meta_policy or load_meta_policy()
        if rule["action"] not in mp.get("allowed_actions", []):
            return False, f"Agent cannot create rules with action '{rule['action']}'"
        domain = (rule.get("match", {}).get("from") or "").split("@")[-1].lower()
        allowed = [d.lower() for d in mp.get("allowed_domains", [])]
        if allowed and domain not in allowed:
            return False, f"Agent cannot create rules for domain '{domain}'"
        if mp.get("require_fire_once_for_agent") and not rule.get("fire_once"):
            return False, "Agent-created rules must be fire_once"
        agent_count = sum(1 for r in _load_rules() if r.get("created_by") == "agent" and r.get("id") != rule.get("id"))
        if agent_count >= mp.get("max_active_agent_rules", 10):
            return False, "Agent rule limit reached"
        ttl = rule.get("ttl_hours")
        max_ttl = mp.get("max_ttl_hours", 72)
        if not ttl or ttl > max_ttl:
            rule["ttl_hours"] = max_ttl
    return True, None


def create_rule(rule):
    """Add a rule. Returns (rule, error)."""
    rules = _load_rules()
    rule.setdefault("id", str(uuid.uuid4())[:8])
    rule.setdefault("enabled", True)
    rule.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    ok, err = validate_rule(rule)
    if not ok:
        return None, err
    if rule.get("ttl_hours"):
        from datetime import timedelta
        created = datetime.fromisoformat(rule["created_at"])
        rule["expires_at"] = (created + timedelta(hours=rule["ttl_hours"])).isoformat()
    rules.append(rule)
    _save_rules(rules)
    return rule, None


def update_rule(rule_id, updates):
    rules = _load_rules()
    for r in rules:
        if r["id"] == rule_id:
            r.update(updates)
            ok, err = validate_rule(r)
            if not ok:
                return None, err
            _save_rules(rules)
            return r, None
    return None, "Rule not found"


def delete_rule(rule_id):
    rules = _load_rules()
    rules = [r for r in rules if r["id"] != rule_id]
    _save_rules(rules)


def list_rules():
    return _load_rules()


def get_rule(rule_id):
    for r in _load_rules():
        if r["id"] == rule_id:
            return r
    return None


# ---------------------------------------------------------------------------
# Match history
# ---------------------------------------------------------------------------

MAX_HISTORY = 200


def _load_history():
    return _load_json(HISTORY_FILE, [])


def _save_history(history):
    _save_json(HISTORY_FILE, history[-MAX_HISTORY:])


def record_match(rule, message, action_taken):
    history = _load_history()
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rule_id": rule["id"],
        "rule_name": rule.get("name", ""),
        "action": action_taken,
        "message_from": message.get("from", ""),
        "message_subject": message.get("subject", "")[:120],
        "channel": message.get("channel", "email"),
    })
    _save_history(history)


def get_history(limit=50):
    return _load_history()[-limit:]


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

def _match_rule(rule, message):
    """Check if a message matches a rule's criteria."""
    if not rule.get("enabled", True):
        return False
    # Check expiry
    if rule.get("expires_at"):
        try:
            exp = datetime.fromisoformat(rule["expires_at"])
            if datetime.now(timezone.utc) > exp:
                return False
        except (ValueError, TypeError):
            pass
    match = rule.get("match", {})
    # Channel
    if match.get("channel") and message.get("channel") != match["channel"]:
        return False
    # From (exact or domain)
    if match.get("from"):
        msg_from = (message.get("from") or "").lower()
        rule_from = match["from"].lower()
        if "@" in rule_from:
            if msg_from != rule_from:
                return False
        else:
            if not msg_from.endswith("@" + rule_from) and msg_from != rule_from:
                return False
    # Subject contains
    if match.get("subject_contains"):
        subject = (message.get("subject") or "").lower()
        if match["subject_contains"].lower() not in subject:
            return False
    # Headers (for email)
    if match.get("headers"):
        msg_headers = message.get("headers", {})
        for key, allowed in match["headers"].items():
            val = msg_headers.get(key, "")
            if isinstance(allowed, list):
                if val not in allowed:
                    return False
            elif val != allowed:
                return False
    return True


def evaluate(message):
    """Evaluate a message against all rules. Returns (action, rule, stripped_message).

    action is 'dispatch', 'summary', or 'drop'.
    For 'summary', the message body is stripped — only metadata remains.
    """
    rules = _load_rules()
    for rule in rules:
        if _match_rule(rule, message):
            action = rule.get("action", "drop")
            # Fire-once: delete rule after match
            if rule.get("fire_once"):
                delete_rule(rule["id"])
            record_match(rule, message, action)
            if action == "summary":
                stripped = {k: v for k, v in message.items() if k != "body"}
                stripped["body"] = "(body stripped by workflow rule)"
                return action, rule, stripped
            return action, rule, message
    # Default deny
    record_match({"id": "default", "name": "default-deny"}, message, "drop")
    return "drop", None, message


# ---------------------------------------------------------------------------
# Email polling adapter
# ---------------------------------------------------------------------------

class EmailPoller:
    """Polls Microsoft 365 inbox for new messages and runs them through the rule engine."""

    def __init__(self, on_dispatch=None, interval=60):
        self.on_dispatch = on_dispatch  # callback(action, rule, message)
        self.interval = interval
        self._thread = None
        self._stop = threading.Event()
        self._last_seen_id = None
        self._seen_file = os.path.join(DATA_DIR, "workflow_last_seen.txt")
        self._load_last_seen()

    def _load_last_seen(self):
        try:
            with open(self._seen_file) as f:
                self._last_seen_id = f.read().strip() or None
        except OSError:
            pass

    def _save_last_seen(self, msg_id):
        self._last_seen_id = msg_id
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(self._seen_file, "w") as f:
            f.write(msg_id)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Email poller started (interval=%ds)", self.interval)

    def stop(self):
        self._stop.set()

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                if _load_rules():
                    self._poll_once()
            except Exception as e:
                logger.error("Email poll error: %s", e, exc_info=True)
            self._stop.wait(self.interval)

    def _poll_once(self):
        try:
            graph = _get_graph_request()
        except Exception:
            return  # Microsoft 365 not configured
        params = "$top=10&$select=id,subject,from,receivedDateTime,isRead,bodyPreview,internetMessageHeaders&$orderby=receivedDateTime desc&$filter=isRead eq false"
        data = graph("GET", f"/me/mailFolders/Inbox/messages?{params}")
        if "error" in data:
            logger.warning("Graph API error: %s", data["error"])
            return
        messages = data.get("value", [])
        if not messages:
            return
        # Process newest-first, stop at last_seen
        new_msgs = []
        for msg in messages:
            if msg["id"] == self._last_seen_id:
                break
            new_msgs.append(msg)
        if not new_msgs:
            return
        # Save the newest as last_seen
        self._save_last_seen(new_msgs[0]["id"])
        # Process oldest-first
        for msg in reversed(new_msgs):
            message = {
                "channel": "email",
                "id": msg["id"],
                "from": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                "subject": msg.get("subject", ""),
                "body": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "headers": {
                    h["name"]: h["value"]
                    for h in msg.get("internetMessageHeaders", [])
                },
            }
            action, rule, processed_msg = evaluate(message)
            if action != "drop" and self.on_dispatch:
                # Fetch full body for dispatched messages
                if action == "dispatch":
                    full = graph("GET", f"/me/messages/{msg['id']}?$select=body")
                    if "error" not in full:
                        processed_msg["body"] = full.get("body", {}).get("content", processed_msg["body"])
                self.on_dispatch(action, rule, processed_msg)
            # Mark as read
            graph("PATCH", f"/me/messages/{msg['id']}", json={"isRead": True})


# ---------------------------------------------------------------------------
# Singleton manager
# ---------------------------------------------------------------------------

class WorkflowManager:
    def __init__(self):
        self.poller = None
        self._dispatch_callback = None

    def start(self, on_dispatch=None):
        self._dispatch_callback = on_dispatch
        self.poller = EmailPoller(on_dispatch=on_dispatch, interval=60)
        # Purge expired rules on start
        self._purge_expired()
        self.poller.start()

    def stop(self):
        if self.poller:
            self.poller.stop()

    def _purge_expired(self):
        rules = _load_rules()
        now = datetime.now(timezone.utc)
        active = []
        for r in rules:
            if r.get("expires_at"):
                try:
                    if now > datetime.fromisoformat(r["expires_at"]):
                        continue
                except (ValueError, TypeError):
                    pass
            active.append(r)
        if len(active) != len(rules):
            _save_rules(active)


workflow_manager = WorkflowManager()
