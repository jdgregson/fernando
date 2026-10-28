import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("desktop_recording", Path(__file__).resolve().parents[1] / "mcp_servers" / "desktop_recording.py")
gameplay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gameplay)


class GameplayTests(unittest.TestCase):
    def test_rejects_unbounded_input_before_posting(self):
        for actions in ([{"kind": "wait", "duration": 61}], [{"kind": "wait", "duration": float("nan")}], [{"kind": "key_down", "key": "--window"}], [{"kind": "move", "x": 0, "y": 0, "relative": "false"}]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                gameplay.validate_actions(actions)

    def test_total_duration_is_bounded(self):
        with self.assertRaises(ValueError):
            gameplay.validate_actions([{"kind": "wait", "duration": 31}] * 2)

    def test_recording_ids_cannot_address_other_files(self):
        with self.assertRaises(ValueError):
            gameplay.recording_path("../../config")

    def test_interruption_releases_held_inputs(self):
        with tempfile.TemporaryFile() as lock, patch("builtins.open", return_value=lock), patch.object(gameplay, "run") as command, patch.object(gameplay.time, "sleep", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                gameplay.sequence([{"kind": "key_down", "key": "w"}, {"kind": "button_down", "button": 1}, {"kind": "wait", "duration": 1}])
            self.assertEqual(command.call_args.args, ("xdotool", "keyup", "--", "w", "mouseup", "1"))

    def test_does_not_release_unowned_inputs(self):
        with tempfile.TemporaryFile() as lock, patch("builtins.open", return_value=lock), patch.object(gameplay, "run") as command:
            with self.assertRaises(ValueError):
                gameplay.sequence([{"kind": "key_up", "key": "Shift_L"}])
            command.assert_not_called()

    def test_relative_movement_preserves_total_delta(self):
        with patch.object(gameplay, "run", return_value="X=500\nY=400\nSCREEN=0\nWINDOW=1") as command, patch.object(gameplay.time, "sleep"):
            gameplay.move({"kind": "move", "x": -17, "y": 11, "duration": 0.1, "relative": True})
            movements = [call.args for call in command.call_args_list if call.args[1] == "mousemove_relative"]
            self.assertEqual(len(movements), 6)
            self.assertEqual(sum(int(args[-2]) for args in movements), -17)
            self.assertEqual(sum(int(args[-1]) for args in movements), 11)


if __name__ == "__main__":
    unittest.main()
