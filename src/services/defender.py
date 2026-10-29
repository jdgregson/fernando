import json
import subprocess
import threading
import time


_lock = threading.Lock()
_cached = None
_expires = 0

TARGET_FIELDS = {
    'realTimeProtectionEnabled': 'Real-time protection',
    'behaviorMonitoring': 'Behavior monitoring',
    'networkProtectionStatus': 'Network protection',
    'networkProtectionEnforcementLevel': 'Network enforcement',
    'cloudEnabled': 'Cloud protection',
    'tamperProtectionEnforcementLevel': 'Tamper protection',
}


def valid_target(field, value):
    if field not in TARGET_FIELDS:
        return False
    if value is None:
        return True
    if field in ('realTimeProtectionEnabled', 'cloudEnabled'):
        return type(value) is bool
    return isinstance(value, str) and 0 < len(value) <= 128 and value == value.strip()


def normalize_target(value):
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower()
    return {
        'enablement failed: edr capabilities': 'enablement_failed_due_to_edr_capabilities',
        'failed to start': 'failed_to_start',
    }.get(normalized, normalized)


def evaluate_targets(report, targets):
    mismatches = []
    for field, label in TARGET_FIELDS.items():
        expected = normalize_target(targets.get(field))
        if expected is None:
            continue
        actual = report.get(field)
        if isinstance(actual, dict):
            actual = actual.get('value')
        if type(actual) is not type(expected) or actual != expected:
            mismatches.append({'field': field, 'label': label, 'expected': expected, 'actual': actual})
    return mismatches


def get_health():
    global _cached, _expires
    with _lock:
        if _cached is not None and time.monotonic() < _expires:
            return _cached
        try:
            result = subprocess.run(
                ["mdatp", "health", "--output", "json"],
                capture_output=True, text=True, timeout=4, check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            _cached = {"error": str(error)}
        else:
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                _cached = {"error": f"Invalid Defender health JSON: {error}"}
            else:
                if not isinstance(report, dict) or type(report.get("healthy")) is not bool:
                    _cached = {"error": "Defender health response has no boolean healthy field"}
                else:
                    _cached = {"report": report}
        _cached["checked_at"] = time.time()
        _expires = time.monotonic() + 30
        return _cached
