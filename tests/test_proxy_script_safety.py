import unittest
import json
import subprocess
from contextlib import ExitStack
from unittest.mock import Mock, mock_open, patch

from flask import Flask
from jinja2.utils import htmlsafe_json_dumps
from werkzeug.middleware.proxy_fix import ProxyFix

from src.routes import web


class ProxyScriptSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.key = 'fixture-key'
        self.stack.enter_context(patch.object(web, 'open', mock_open(read_data=self.key), create=True))
        self.upstream = Mock(content=b'<html><head></head><body></body></html>', status_code=200, headers={'content-type': 'text/html'})
        self.stack.enter_context(patch.object(web.requests, 'request', return_value=self.upstream))
        self.stack.enter_context(patch.object(web.docker_service, 'is_kasm_running', return_value=True))
        self.stack.enter_context(patch('src.services.jupyter.is_running', return_value=True))
        self.stack.enter_context(patch('src.services.jupyter.get_port', return_value=8888))
        self.stack.enter_context(patch('src.services.notebooks.get_notebook_port', return_value=3000))
        self.app = Flask(__name__)
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_host=1)
        self.app.register_blueprint(web.bp)
        self.client = self.app.test_client()

    def test_cookie_auth_does_not_embed_untrusted_query_key(self):
        payload = '</script><script>window.__probe=1</script>'
        for route, cookie in [('/kasm/', 'kasm_auth'), ('/jupyter/', 'jupyter_auth')]:
            with self.subTest(route=route):
                self.client.set_cookie(cookie, self.key)
                response = self.client.get(route, query_string={'api_key': payload})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(payload, response.text)
                self.assertIn(self.key, response.text)

    def test_query_and_header_auth_preserve_credentials(self):
        for route in ['/kasm/', '/jupyter/']:
            for headers, query in [({}, {'api_key': self.key}), ({'X-API-Key': self.key}, {'api_key': 'invalid'})]:
                with self.subTest(route=route, headers=headers):
                    response = self.client.get(route, headers=headers, query_string=query)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(self.key, response.text)
                    self.assertIn(self.key, response.headers['Set-Cookie'])

    def test_unauthenticated_requests_still_fail(self):
        for route in ['/kasm/', '/jupyter/', '/notes/default/']:
            self.assertEqual(self.client.get(route, query_string={'api_key': 'invalid'}).status_code, 401)

    def test_script_serialization_handles_html_delimiters(self):
        key = '</script><script>probe()</script>\'"&'
        with patch.object(web, 'open', mock_open(read_data=key), create=True):
            for route in ['/kasm/', '/jupyter/']:
                response = self.client.get(route, headers={'X-API-Key': key})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(key, response.text)
                self.assertIn(str(htmlsafe_json_dumps(key)), response.text)

    def test_notebook_scripts_ignore_forwarded_host(self):
        response = self.client.get('/notes/default/', headers={'X-API-Key': self.key, 'X-Forwarded-Host': 'attacker.example'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('attacker.example', response.text)
        for name in ['fake-idb-bundle.js', 'fake-idb-persist.js']:
            self.assertIn(f'<script src="/static/js/{name}"></script>', response.text)

    def test_jupyter_redirect_sets_authenticated_key(self):
        self.upstream.status_code = 302
        self.upstream.headers = {'Location': '/nbclassic/tree/'}
        response = self.client.get('/jupyter/', headers={'X-API-Key': self.key}, query_string={'api_key': 'invalid'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/jupyter/tree/')
        self.assertIn(self.key, response.headers['Set-Cookie'])

    def test_jupyter_injection_remains_html_not_escaped_text(self):
        response = self.client.get('/jupyter/', headers={'X-API-Key': self.key})
        self.assertEqual(response.status_code, 200)
        self.assertIn('<script src="/static/js/pane-bridge.js"', response.text)
        self.assertIn('<script>sessionStorage.setItem(', response.text)
        self.assertNotIn('&lt;script', response.text)
        self.assertIn('</head>', response.text)

    def test_autosave_waits_for_notebook_loading(self):
        response = self.client.get('/jupyter/', headers={'X-API-Key': self.key})
        start = response.text.index('(function(){var nb=window.Jupyter&&Jupyter.notebook;')
        end = response.text.index('})();', start) + len('})();')
        script = response.text[start:end]
        harness = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
let retry;
let interval;
const nb = {_fully_loaded:false, writable:false, events:{off(){}}, set_autosave_interval(value){interval=this.writable?value:0;}};
const context = {window:{Jupyter:{notebook:nb}}, Jupyter:{notebook:nb}, setTimeout(fn){retry=fn;}};
vm.runInNewContext(SCRIPT, context);
assert.equal(interval, undefined);
assert.equal(typeof retry, 'function');
nb._fully_loaded=true;
nb.writable=true;
retry();
assert.equal(interval, 10000);
""".replace('SCRIPT', json.dumps(script))
        subprocess.run(['node', '-e', harness], check=True)
