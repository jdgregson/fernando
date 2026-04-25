"""Tests for inbound automation rule specificity scoring."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.services.automation as automation

# --- Helpers ---

def _make_rule(id, from_filter, subject=None, body=None, channel=None, headers=None, action="dispatch", fire_once=False):
    trigger = {"type": "inbound", "from": from_filter}
    if subject:
        trigger["subject_contains"] = subject
    if body:
        trigger["body_contains"] = body
    if channel:
        trigger["channel"] = channel
    if headers:
        trigger["headers"] = headers
    return {
        "id": id,
        "name": id,
        "enabled": True,
        "trigger": trigger,
        "action": action,
        "fire_once": fire_once,
        "purpose": "test",
    }


def _make_message(from_addr, subject="Hello", body="Body text", channel="email"):
    return {"from": from_addr, "subject": subject, "body": body, "channel": channel, "headers": {}}


def _setup_rules(rules, tmp):
    path = os.path.join(tmp, "automation_rules.json")
    with open(path, "w") as f:
        json.dump(rules, f)
    automation.RULES_FILE = path


# --- Specificity score tests ---

def test_score_exact_email():
    rule = _make_rule("a", "user@example.com")
    assert automation._specificity_score(rule) == 2

def test_score_domain_only():
    rule = _make_rule("a", "example.com")
    assert automation._specificity_score(rule) == 1

def test_score_email_plus_subject():
    rule = _make_rule("a", "user@example.com", subject="test")
    assert automation._specificity_score(rule) == 4

def test_score_email_plus_subject_plus_body():
    rule = _make_rule("a", "user@example.com", subject="test", body="keyword")
    assert automation._specificity_score(rule) == 6

def test_score_with_channel():
    rule = _make_rule("a", "user@example.com", channel="email")
    assert automation._specificity_score(rule) == 3

def test_score_with_headers():
    rule = _make_rule("a", "user@example.com", headers={"X-Custom": "val", "X-Other": "val2"})
    assert automation._specificity_score(rule) == 4


# --- evaluate_inbound specificity ordering tests ---

def test_specific_rule_wins_over_catchall():
    with tempfile.TemporaryDirectory() as tmp:
        catchall = _make_rule("catchall", "jonathan@jdgregson.com")
        specific = _make_rule("specific", "jonathan@jdgregson.com", subject="Walled City")
        _setup_rules([catchall, specific], tmp)

        msg = _make_message("jonathan@jdgregson.com", subject="Walled City status update")
        action, rule, _ = automation.evaluate_inbound(msg)
        assert rule["id"] == "specific"

def test_catchall_fires_when_no_specific_match():
    with tempfile.TemporaryDirectory() as tmp:
        catchall = _make_rule("catchall", "jonathan@jdgregson.com")
        specific = _make_rule("specific", "jonathan@jdgregson.com", subject="Walled City")
        _setup_rules([catchall, specific], tmp)

        msg = _make_message("jonathan@jdgregson.com", subject="Something else entirely")
        action, rule, _ = automation.evaluate_inbound(msg)
        assert rule["id"] == "catchall"

def test_insertion_order_breaks_ties():
    with tempfile.TemporaryDirectory() as tmp:
        rule_a = _make_rule("first", "jonathan@jdgregson.com", subject="test")
        rule_b = _make_rule("second", "jonathan@jdgregson.com", subject="test")
        _setup_rules([rule_a, rule_b], tmp)

        msg = _make_message("jonathan@jdgregson.com", subject="test something")
        action, rule, _ = automation.evaluate_inbound(msg)
        assert rule["id"] == "first"

def test_domain_rule_loses_to_exact_email():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_rule("domain", "jdgregson.com")
        exact = _make_rule("exact", "jonathan@jdgregson.com")
        _setup_rules([domain, exact], tmp)

        msg = _make_message("jonathan@jdgregson.com", subject="Hi")
        action, rule, _ = automation.evaluate_inbound(msg)
        assert rule["id"] == "exact"

def test_no_match_returns_drop():
    with tempfile.TemporaryDirectory() as tmp:
        rule = _make_rule("a", "other@example.com")
        _setup_rules([rule], tmp)

        msg = _make_message("jonathan@jdgregson.com")
        action, rule, _ = automation.evaluate_inbound(msg)
        assert action == "drop"
        assert rule is None

def test_most_specific_of_three():
    with tempfile.TemporaryDirectory() as tmp:
        broad = _make_rule("broad", "jdgregson.com")
        medium = _make_rule("medium", "jonathan@jdgregson.com")
        narrow = _make_rule("narrow", "jonathan@jdgregson.com", subject="Walled City", body="status")
        _setup_rules([broad, medium, narrow], tmp)

        msg = _make_message("jonathan@jdgregson.com", subject="Walled City update", body="status report")
        action, rule, _ = automation.evaluate_inbound(msg)
        assert rule["id"] == "narrow"

def test_summary_action_strips_body():
    with tempfile.TemporaryDirectory() as tmp:
        rule = _make_rule("sum", "user@example.com", action="summary")
        _setup_rules([rule], tmp)

        msg = _make_message("user@example.com", body="secret stuff")
        action, matched, processed = automation.evaluate_inbound(msg)
        assert action == "summary"
        assert "secret" not in processed["body"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
