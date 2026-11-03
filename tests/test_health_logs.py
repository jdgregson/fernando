import tempfile
import ast
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta

from src.services import health_logs


class HealthLogsTests(unittest.TestCase):
    def test_error_index_context_age_incremental_and_rotation(self):
        now = datetime(2026, 10, 31, 15, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flask.log'
            path.write_text('2026-10-31 12:00:00,000 app INFO Before\n2026-10-31 12:01:00,000 app ERROR Old\nTraceback old\n2026-10-31 14:30:00,000 app CRITICAL Recent\nTraceback recent\n  failing line\n2026-10-31 14:31:00,000 app INFO After\n')
            index = health_logs.ErrorIndex()
            snapshot = index.snapshot(path, True, now)
            self.assertEqual(snapshot['recent_count'], 1)
            text = '\n'.join(record['text'] for record in snapshot['records'])
            for value in ['Before', 'Old', 'Traceback old', 'Recent', 'Traceback recent', 'failing line']:
                self.assertIn(value, text)
            self.assertNotIn('After', text)
            self.assertEqual(index.snapshot(path, now=now), {'recent_count': 1})
            self.assertEqual(index.snapshot(path, now=now + timedelta(hours=1)), {'recent_count': 0})
            self.assertTrue(index.snapshot(path, True, now + timedelta(hours=1))['records'])
            with path.open('a') as stream:
                stream.write('2026-10-31 15:00:00,000 app ERROR Partial')
            self.assertEqual(index.snapshot(path, now=now)['recent_count'], 0)
            with path.open('a') as stream:
                stream.write('\n')
            self.assertEqual(index.snapshot(path, now=now)['recent_count'], 1)
            path.write_text('2026-10-31 15:00:00,000 app INFO Reset\n')
            self.assertEqual(index.snapshot(path, True, now), {'recent_count': 0, 'records': []})

    def test_redacts_current_and_historical_api_keys(self):
        examples = [
            ('GET /test?api_key=old-secret&session=123', 'GET /test?api_key=REDACTED&session=123'),
            ('X-API-Key: old-secret', 'X-API-Key: REDACTED'),
            ('{"api_key": "old-secret"}', '{"api_key": "REDACTED"}'),
            ('api_key%3Dold-secret%26session%3D123', 'api_key%3DREDACTED%26session%3D123'),
            ('api%5Fkey%3Dold-secret', 'api%5Fkey%3DREDACTED'),
            ('unlabelled current-secret value', 'unlabelled REDACTED value'),
        ]
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(health_logs.redact_text(text, 'current-secret'), expected)

    def test_redacts_all_payload_fields(self):
        payload = {'records': [{'text': 'api_key=old-secret', 'message': 'current-secret'}], 'errors': ['X-API-Key: old-secret'], 'error': 'current-secret', 'sources': [{'label': 'current-secret'}]}
        with patch.object(Path, 'read_text', return_value='current-secret\n'):
            redacted = health_logs.redact_payload(payload)
        self.assertNotIn('old-secret', str(redacted))
        self.assertNotIn('current-secret', str(redacted))
        self.assertIn('REDACTED', str(redacted))

    def test_endpoint_requires_auth_and_does_not_cache(self):
        from flask import Flask, request
        source = Path('src/routes/web.py').read_text()
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'api_health_logs')
        function.decorator_list = []
        namespace = {'request': request, '_check_api_key': lambda: request.headers.get('X-API-Key') == 'test-key'}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<health-route>', 'exec'), namespace)
        app = Flask(__name__)
        app.add_url_rule('/api/health/logs', view_func=namespace['api_health_logs'])
        client = app.test_client()
        with patch.object(health_logs, 'read_logs', return_value=[{'text': 'hello'}]) as read:
            self.assertEqual(client.get('/api/health/logs').status_code, 401)
            read.assert_not_called()
            response = client.get('/api/health/logs?source=nginx-error', headers={'X-API-Key': 'test-key'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.json, {'records': [{'text': 'hello'}]})
            read.assert_called_once_with('nginx-error', '')

    def test_reads_only_selected_file_and_limits_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'log'
            path.write_text('\n'.join(str(value) for value in range(1200)))
            with patch.dict(health_logs.FILES, {'flask': str(path)}):
                records = health_logs.read_logs('flask', '')
            self.assertEqual(len(records), 1000)
            self.assertEqual(records[0]['text'], '200')

    def test_unknown_sources_rejected(self):
        with self.assertRaises(ValueError):
            health_logs.read_logs('/etc/passwd', '')

    def test_subagent_source_removed(self):
        with self.assertRaises(ValueError):
            health_logs.sources('subagent')
        with self.assertRaises(ValueError):
            health_logs.read_logs('subagent', 'task')

    def test_docker_scope_and_no_shell(self):
        with patch.object(health_logs, 'command', return_value='abc fernando-desktop\ndef unrelated\n123 fernando-notebook-default\n') as command:
            self.assertEqual(len(health_logs.sources('docker')), 2)
            with self.assertRaises(ValueError):
                health_logs.read_logs('docker', 'def')
            command.assert_called_with(['docker', 'ps', '-a', '--format', '{{.ID}} {{.Names}}'])
