import ast
import base64
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, request
from src.services import chat_images


class ChatImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(chat_images, 'CACHE_ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_native_image_bytes_become_persistent_authenticated_file(self):
        data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        original = {'items': [{'Image': {'format': 'png', 'source': {'kind': 'bytes', 'data': list(data)}}}]}
        saved = copy.deepcopy(original)
        result = chat_images.project_images('3001fb5a', original)
        image = result['items'][0]['Image']
        path = Path(image['path'])
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(image['mimeType'], 'image/png')
        self.assertEqual(original, saved)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(chat_images.project_images('3001fb5a', original), result)
        self.assertEqual(len(list(path.parent.iterdir())), 1)
        app = Flask(__name__)
        tree = ast.parse(Path('src/routes/web.py').read_text())
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'serve_file')
        route.decorator_list = []
        scope = {'os': os, '__file__': str(Path('src/routes/web.py').resolve()), '_check_api_key': lambda: request.headers.get('X-API-Key') == 'test'}
        exec(compile(ast.Module(body=[route], type_ignores=[]), '<route>', 'exec'), scope)
        app.add_url_rule('/api/files/<path:filepath>', view_func=scope['serve_file'])
        with app.test_client() as client:
            url = '/api/files/' + str(path).lstrip('/')
            self.assertEqual(client.get(url).status_code, 401)
            response = client.get(url, headers={'X-API-Key': 'test'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, data)
            self.assertEqual(response.mimetype, 'image/png')
            response.close()

    def test_mcp_and_acp_images_are_references_without_mutating_history(self):
        block = {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(b'image bytes').decode()}
        event = {'params': {'update': {'content': [{'type': 'content', 'content': block}], 'rawOutput': {'items': [{'Json': {'content': [block]}}]}}}}
        result = chat_images.project_images('aaaaaaaa', event)
        update = result['params']['update']
        first = update['content'][0]['content']
        second = update['rawOutput']['items'][0]['Json']['content'][0]
        self.assertEqual(first, second)
        self.assertNotIn('data', first)
        self.assertIn('data', block)
        self.assertEqual(chat_images.project_images('aaaaaaaa', result), result)

    def test_invalid_session_cannot_escape_cache(self):
        with self.assertRaises(ValueError):
            chat_images.cache_image('../escape', b'image', 'image/png')

    def test_authenticated_files_outside_previous_allowlist(self):
        app = Flask(__name__)
        tree = ast.parse(Path('src/routes/web.py').read_text())
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'serve_file')
        route.decorator_list = []
        scope = {'os': os, '__file__': str(self.root / 'src' / 'routes' / 'web.py'), '_check_api_key': lambda: request.headers.get('X-API-Key') == 'test'}
        exec(compile(ast.Module(body=[route], type_ignores=[]), '<route>', 'exec'), scope)
        app.add_url_rule('/api/files/<path:filepath>', view_func=scope['serve_file'])
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory, app.test_client() as client:
            for name in ('screenshot.png', 'output.txt'):
                path = Path(directory) / name
                path.write_bytes(b'file contents')
                url = '/api/files/' + str(path.relative_to(Path.home()))
                for suffix in ('', '?session=3001fb5a'):
                    self.assertEqual(client.get(url + suffix).status_code, 401)
                    response = client.get(url + suffix, headers={'X-API-Key': 'test'})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, b'file contents')
                    response.close()
            self.assertEqual(client.get(url + '.missing', headers={'X-API-Key': 'test'}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
