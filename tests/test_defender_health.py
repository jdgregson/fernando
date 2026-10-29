import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "defender_health_under_test",
    Path(__file__).resolve().parents[1] / "src/services/defender.py",
)
defender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(defender)


class DefenderHealthTests(unittest.TestCase):
    def setUp(self):
        defender._cached = None
        defender._expires = 0

    def test_report_cached_and_refreshed(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"healthy": False, "healthIssues": ["Network failure"]}), "")
        with patch.object(defender.subprocess, "run", return_value=result) as run:
            first = defender.get_health()
            self.assertFalse(first["report"]["healthy"])
            self.assertEqual(first["report"]["healthIssues"], ["Network failure"])
            self.assertIs(first, defender.get_health())
            run.assert_called_once_with(
                ["mdatp", "health", "--output", "json"],
                capture_output=True, text=True, timeout=4, check=True,
            )
            defender._expires = 0
            defender.get_health()
            self.assertEqual(run.call_count, 2)

    def test_command_failures_are_visible(self):
        for error in [FileNotFoundError("mdatp missing"), subprocess.TimeoutExpired("mdatp", 4), subprocess.CalledProcessError(1, "mdatp")]:
            with self.subTest(error=error), patch.object(defender.subprocess, "run", side_effect=error):
                defender._expires = 0
                result = defender.get_health()
                self.assertIn("error", result)
                self.assertNotIn("report", result)

    def test_invalid_reports_are_not_healthy(self):
        for output in ["not JSON", "[]", '{}', '{"healthy":"false"}']:
            with self.subTest(output=output), patch.object(defender.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                defender._expires = 0
                self.assertIn("error", defender.get_health())

    def test_targets_match_wrapped_values_and_report_missing_fields(self):
        report = {'cloudEnabled': {'value': False}, 'behaviorMonitoring': {'value': 'enabled'}}
        targets = {'cloudEnabled': True, 'behaviorMonitoring': 'enabled', 'networkProtectionStatus': 'started'}
        mismatches = defender.evaluate_targets(report, targets)
        self.assertEqual([item['field'] for item in mismatches], ['networkProtectionStatus', 'cloudEnabled'])
        self.assertIsNone(mismatches[0]['actual'])
        self.assertIs(mismatches[1]['actual'], False)
        self.assertEqual(report['cloudEnabled'], {'value': False})

    def test_targets_optional_and_type_strict(self):
        self.assertEqual(defender.evaluate_targets({}, {}), [])
        self.assertEqual(defender.evaluate_targets({}, {'cloudEnabled': None}), [])
        self.assertEqual(defender.evaluate_targets({'cloudEnabled': True}, {'cloudEnabled': True}), [])
        self.assertEqual(len(defender.evaluate_targets({'cloudEnabled': 1}, {'cloudEnabled': True})), 1)
        self.assertTrue(defender.valid_target('cloudEnabled', False))
        self.assertFalse(defender.valid_target('cloudEnabled', 'true'))
        self.assertFalse(defender.valid_target('unknown', None))
        self.assertFalse(defender.valid_target('networkProtectionStatus', ''))
        self.assertTrue(defender.valid_target('networkProtectionStatus', 'started'))

    def test_previously_entered_display_labels_match_raw_status(self):
        report = {'tamperProtectionEnforcementLevel': {'value': 'disabled'},
                  'networkProtectionStatus': 'enablement_failed_due_to_edr_capabilities'}
        targets = {'tamperProtectionEnforcementLevel': 'Disabled',
                   'networkProtectionStatus': 'Enablement failed: EDR capabilities'}
        self.assertEqual(defender.evaluate_targets(report, targets), [])


if __name__ == "__main__":
    unittest.main()
