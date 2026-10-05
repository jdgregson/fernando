import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, request
from src.routes import file_browser as routes
from src.services import file_browser, groups, settings


class FileBrowserTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        app = Flask(__name__, template_folder=str(Path('src/templates').resolve()))
        app.config['TESTING'] = True
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        self.headers = {'X-API-Key': 'fixture-key'}
        for target in [
            patch.object(routes, '_check_api_key', lambda: (request.headers.get('X-API-Key') or request.args.get('api_key')) == 'fixture-key'),
            patch.object(file_browser, 'SESSIONS_FILE', self.root / 'sessions.json'),
            patch.object(groups, 'GROUPS_FILE', str(self.root / 'groups.json')),
            patch.object(settings, '_SETTINGS_FILE', str(self.root / 'settings.json')),
        ]:
            target.start()
            self.addCleanup(target.stop)

    def post(self, endpoint, data):
        return self.client.post('/api/file-browser/' + endpoint, json=data, headers=self.headers)

    def test_auth_and_absolute_paths(self):
        self.assertEqual(self.client.get('/api/file-browser/list').status_code, 401)
        result = self.client.post('/api/file-browser/operation?api_key=fixture-key', json={'action': 'create', 'path': str(self.root / 'bad')})
        self.assertEqual(result.status_code, 401)
        self.assertFalse((self.root / 'bad').exists())
        self.assertEqual(self.client.get('/api/file-browser/list?path=relative', headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get('/api/file-browser/list?path=/', headers=self.headers).status_code, 200)

    def test_properties_auth_metadata_and_special_files(self):
        path = self.root / 'example.txt'
        path.write_text('hello')
        path.chmod(0o640)
        for endpoint in ('properties', 'directory-totals'):
            self.assertEqual(self.client.get('/api/file-browser/' + endpoint).status_code, 401)
            self.assertEqual(self.client.get('/api/file-browser/' + endpoint, query_string={'path': 'relative'}, headers=self.headers).status_code, 400)
        response = self.client.get('/api/file-browser/properties', query_string={'path': str(path)}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(data['size'], 5)
        self.assertEqual(data['permissions'], '0640')
        self.assertEqual(data['uid'], os.getuid())
        self.assertEqual(data['modified'], path.stat().st_mtime)
        self.assertEqual(data['mime'], 'text/plain')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        link = self.root / 'broken-link'
        link.symlink_to('missing')
        data = self.client.get('/api/file-browser/properties', query_string={'path': str(link)}, headers=self.headers).json
        self.assertEqual(data['type'], 'Symbolic link')
        self.assertEqual(data['target'], 'missing')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        data = self.client.get('/api/file-browser/properties', query_string={'path': str(fifo)}, headers=self.headers).json
        self.assertEqual(data['type'], 'Named pipe')

    def test_folder_totals_do_not_follow_links_or_double_count_hard_links(self):
        folder = self.root / 'folder'
        folder.mkdir()
        source = folder / '.hidden'
        source.write_bytes(b'12345')
        os.link(source, folder / 'hard-link')
        (folder / 'child').mkdir()
        link = folder / 'cycle'
        link.symlink_to(folder)
        response = self.client.get('/api/file-browser/directory-totals', query_string={'path': str(folder)}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['files'], 2)
        self.assertEqual(response.json['directories'], 1)
        self.assertEqual(response.json['symlinks'], 1)
        self.assertEqual(response.json['size'], 5 + link.lstat().st_size)
        self.assertTrue(response.json['complete'])
        with patch.object(file_browser.time, 'monotonic', side_effect=[0, 4]):
            self.assertFalse(file_browser.directory_totals(str(folder))['complete'])

    def test_session_identity_and_group_membership(self):
        first = self.post('sessions', {'group_id': 'test-group'}).json
        second = self.post('sessions', {}).json
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(groups.get_session_groups()['files:' + first['id']], 'test-group')
        self.post('sessions', {'id': first['id'], 'name': 'Project files'})
        self.assertEqual(file_browser.list_sessions()[0]['name'], 'Project files')
        self.post('sessions', {'id': first['id']})
        self.assertNotIn('files:' + first['id'], groups.get_session_groups())
        self.assertEqual(len(file_browser.list_sessions()), 1)

    def test_text_save_conflict_and_modes(self):
        path = self.root / 'example.py'
        path.write_text('print("original")\n')
        path.chmod(0o640)
        original = self.client.get('/api/file-browser/text', query_string={'path': str(path)}, headers=self.headers).json
        saved = self.post('save', {'path': str(path), 'revision': original['revision'], 'text': 'updated\n'})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(path.read_text(), 'updated\n')
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        path.write_text('agent changed this\n')
        conflict = self.post('save', {'path': str(path), 'revision': saved.json['revision'], 'text': 'stale edit'})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(path.read_text(), 'agent changed this\n')

    def test_operations_do_not_overwrite_and_symlinks_use_os_permissions(self):
        source = self.root / 'source.txt'
        source.write_text('source')
        destination = self.root / 'target.txt'
        destination.write_text('target')
        for action in ['move', 'copy']:
            self.assertEqual(self.post('operation', {'action': action, 'path': str(source), 'destination': str(destination)}).status_code, 409)
            self.assertEqual(destination.read_text(), 'target')
        moved = self.root / 'moved.txt'
        self.assertEqual(self.post('operation', {'action': 'move', 'path': str(source), 'destination': str(moved)}).status_code, 200)
        link = self.root / 'link'
        link.symlink_to(moved)
        data = self.client.get('/api/file-browser/text', query_string={'path': str(link)}, headers=self.headers).json
        self.assertEqual(data['text'], 'source')
        self.assertEqual(self.post('operation', {'action': 'delete', 'path': str(link)}).status_code, 200)
        self.assertTrue(moved.exists())

    def test_upload_and_download_safety(self):
        result = self.client.post('/api/file-browser/upload', headers=self.headers, data={'path': str(self.root), 'file': (io.BytesIO(b'hello'), '../escape')})
        self.assertEqual(result.status_code, 400)
        result = self.client.post('/api/file-browser/upload', headers=self.headers, data={'path': str(self.root), 'file': (io.BytesIO(b'<script>bad()</script>'), 'test.html')})
        self.assertEqual(result.status_code, 200)
        response = self.client.get('/api/file-browser/content', headers=self.headers, query_string={'path': str(self.root / 'test.html'), 'inline': '1'})
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('sandbox', response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        response.close()

    def test_special_and_binary_files_are_not_opened_as_text(self):
        binary = self.root / 'binary'
        binary.write_bytes(b'\x00\xff')
        self.assertEqual(self.client.get('/api/file-browser/text', headers=self.headers, query_string={'path': str(binary)}).status_code, 415)
        fifo = self.root / 'pipe'
        os.mkfifo(fifo)
        self.assertEqual(self.client.get('/api/file-browser/text', headers=self.headers, query_string={'path': str(fifo)}).status_code, 400)

    def test_defaults_are_rendered_and_recursive_folder_transfers_are_rejected(self):
        session = self.post('sessions', {}).json
        settings.set('file_browser_view', 'tile')
        settings.set('file_browser_hidden', True)
        with patch.object(routes, 'Path') as key_path:
            key_path.return_value.read_text.return_value = 'fixture-key'
            response = self.client.get('/files/' + session['id'], headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-default-view="tile"', response.data)
        self.assertIn(b'data-default-hidden="true"', response.data)
        folder = self.root / 'folder'
        folder.mkdir()
        for action in ('copy', 'move'):
            response = self.post('operation', {'action':action, 'path':str(folder), 'destination':str(folder / 'nested')})
            self.assertEqual(response.status_code, 400)
        self.assertFalse((folder / 'nested').exists())


if __name__ == '__main__':
    unittest.main()
