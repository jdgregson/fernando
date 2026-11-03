import ast
import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests
from flask import Flask

from src.services.service_boundary import RUNTIME_ENDPOINTS, install_boundary


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BoundaryTests(unittest.TestCase):
    def test_runtime_state_routes_have_single_owner(self):
        tree = ast.parse((ROOT / 'src/routes/web.py').read_text())
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.decorator_list or node.name == 'index':
                continue
            names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
            if names & {'acp_manager', '_pending_auth_requests', 'pty_service'}:
                self.assertIn('web.' + node.name, RUNTIME_ENDPOINTS, node.name)

    def test_routing_does_not_retry_mutations_or_follow_redirects(self):
        app = Flask(__name__)
        app.add_url_rule('/api/acp/cancel', endpoint='web.api_acp_cancel', view_func=lambda: {'wrong_owner': True}, methods=['POST'])
        install_boundary(app, 'web')
        with patch('src.services.service_boundary.requests.Session') as factory:
            forward = factory.return_value.request
            forward.return_value.status_code = 302
            forward.return_value.headers = {}
            forward.return_value.raw.headers = {'Location': '/next'}
            forward.return_value.raw.stream.return_value = iter([b'body'])
            response = app.test_client().post('/api/acp/cancel', headers={'X-API-Key': 'secret'}, json={'session_id': 'fixture'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.data, b'body')
            forward.assert_called_once()
            self.assertFalse(forward.call_args.kwargs['allow_redirects'])
            self.assertEqual(forward.call_args.kwargs['headers']['X-Api-Key'], 'secret')
            self.assertFalse(factory.return_value.trust_env)

    def test_full_restart_requires_explicit_force(self):
        restart = load_script('restart_service')
        with patch.object(restart.subprocess, 'run') as run, patch.object(restart.subprocess, 'Popen') as spawn:
            with self.assertRaises(SystemExit):
                restart.restart('all', False)
            run.assert_not_called()
            spawn.assert_not_called()

    def test_web_restart_targets_only_web_unit(self):
        restart = load_script('restart_service')
        with patch.object(restart.subprocess, 'run') as run, patch.object(restart, 'wait_web_ready') as ready, patch.object(restart, 'notify_web_mutation') as notify:
            run.return_value.stdout = 'loaded\n'
            result = restart.restart('flask', False)
            self.assertEqual(result['status'], 'restarted')
            ready.assert_called_once()
            self.assertEqual([call.args[0] for call in notify.call_args_list], ['starting', 'ready'])
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(['sudo', '-n', '/bin/systemctl', 'restart', 'fernando-web'], commands)
            self.assertFalse(any('fernando-runtime' in ' '.join(command) for command in commands))

    def test_web_restart_refuses_monolithic_installation(self):
        restart = load_script('restart_service')
        with patch.object(restart.subprocess, 'run') as run:
            run.return_value.stdout = 'not-found\n'
            with self.assertRaisesRegex(SystemExit, 'No services were restarted'):
                restart.restart('flask', True)
            self.assertEqual(run.call_count, 1)

    def test_unit_dependencies_preserve_runtime_on_web_restart(self):
        installer = load_script('install_service_split')
        units = installer.unit_files(ROOT, 'fernando', '/home/fernando')
        self.assertNotIn('fernando-web', units['fernando-runtime.service'])
        self.assertIn('PartOf=fernando.service', units['fernando-runtime.service'])
        self.assertIn('PartOf=fernando.service', units['fernando-web.service'])
        self.assertNotIn('ExecStop=', units['fernando-web.service'])
        self.assertIn('--infrastructure', units['fernando.service'])

    def test_real_web_restart_preserves_socket_stream_child_and_inflight_tool(self):
        import socketio
        sockets = [socket.socket() for _ in range(2)]
        for listener in sockets:
            listener.bind(('127.0.0.1', 0))
        runtime_port, web_port = [listener.getsockname()[1] for listener in sockets]
        for listener in sockets:
            listener.close()
        env = {**os.environ, 'PYTHONPATH': str(ROOT), 'FLASK_PORT': str(runtime_port), 'WEB_PORT': str(web_port)}
        env.pop('FERNANDO_ROLE', None)
        processes = []
        client = socketio.Client(reconnection=False)
        received = []
        client.on('fixture_event', lambda data: received.append(data['value']))
        with tempfile.TemporaryFile() as output:
            def launch(script):
                process = subprocess.Popen([sys.executable, '-u', str(ROOT / script)], cwd=ROOT, env=env, stdout=output, stderr=output, start_new_session=True)
                processes.append(process)
                return process

            def wait_ready(url, process):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        output.seek(0)
                        self.fail(output.read().decode())
                    try:
                        response = requests.get(url, timeout=0.5)
                    except requests.ConnectionError:
                        time.sleep(0.05)
                        continue
                    if response.status_code == 200:
                        return response
                    time.sleep(0.05)
                self.fail('Isolated service did not become ready')

            runtime = launch('tests/runtime_boundary_fixture.py')
            web = launch('run_web.py')
            base = f'http://127.0.0.1:{runtime_port}'
            status_url = base + '/api/agents/working'
            try:
                before = wait_ready(status_url, runtime).json()
                wait_ready(f'http://127.0.0.1:{web_port}/static/css/index.css', web)
                self.assertEqual(Path(f'/proc/{web.pid}/task/{web.pid}/children').read_text().strip(), '')
                client.connect(base, transports=['websocket'])
                result = []
                thread = threading.Thread(target=lambda: result.append(requests.post(base + '/api/step_progress', json={'marker': 123}, timeout=10).json()))
                thread.start()
                time.sleep(0.2)
                web.terminate()
                web.wait(timeout=5)
                time.sleep(0.3)
                web = launch('run_web.py')
                wait_ready(f'http://127.0.0.1:{web_port}/static/css/index.css', web)
                thread.join(timeout=10)
                self.assertEqual(result, [{'completed': True, 'value': {'marker': 123}}])
                after = requests.get(status_url, timeout=5).json()
                self.assertEqual(before['pid'], after['pid'])
                self.assertEqual(before['child_pid'], after['child_pid'])
                self.assertGreater(len(after['events']), len(before['events']))
                self.assertTrue(client.connected)
                self.assertGreater(len(received), 5)
                self.assertEqual(received, list(range(received[0], received[-1] + 1)))
                page = requests.get(base + '/static/css/index.css', timeout=5)
                self.assertEqual(page.status_code, 200)
            finally:
                client.disconnect()
                for process in processes:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
