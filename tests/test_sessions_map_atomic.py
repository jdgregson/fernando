import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import acp


class AtomicSessionsMapTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / 'chat_sessions.json'
        for name, value in [('DATA_DIR', str(self.directory)), ('SESSIONS_FILE', str(self.path))]:
            override = patch.object(acp, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.original = {'old': {'context': 'original'}}
        acp._save_sessions_map(self.original)

    def test_reader_sees_old_file_until_complete_replacement(self):
        replacement = {'new': {'context': 'x' * 1000000}}
        started = threading.Event()
        release = threading.Event()
        original_dump = json.dump
        errors = []

        def slow_dump(value, stream, **options):
            stream.write(' ')
            stream.flush()
            started.set()
            if not release.wait(5):
                raise TimeoutError('Reader did not release writer')
            original_dump(value, stream, **options)

        def write():
            try:
                acp._save_sessions_map(replacement)
            except Exception as error:
                errors.append(error)

        with patch.object(acp.json, 'dump', slow_dump):
            worker = threading.Thread(target=write)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                for _ in range(20):
                    self.assertEqual(json.loads(self.path.read_text()), self.original)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(self.path.read_text()), replacement)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_serialization_failure_preserves_original_and_cleans_temporary(self):
        with self.assertRaises(TypeError):
            acp._save_sessions_map({'new': object()})
        self.assertEqual(json.loads(self.path.read_text()), self.original)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_replace_failure_preserves_original_and_cleans_temporary(self):
        with patch.object(acp.os, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                acp._save_sessions_map({'new': 'value'})
        self.assertEqual(json.loads(self.path.read_text()), self.original)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
