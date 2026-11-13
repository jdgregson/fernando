from contextlib import ExitStack
import ast
import asyncio
import __future__
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, mock_open, patch

from flask import Flask, request

from src.services import desktop_update as desktop


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'state'
        self.directory.mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for signum in (signal.SIGTERM, signal.SIGINT):
            self.addCleanup(signal.signal, signum, signal.getsignal(signum))
        for name, value in (
            ('ROOT', self.root), ('DIRECTORY', self.directory),
            ('CALLBACK_FILE', self.root / 'callback.conf'),
            ('PASSWORD_FILE', self.root / 'password'),
        ):
            self.stack.enter_context(patch.object(desktop, name, value))
        desktop.PASSWORD_FILE.write_text('fixture-password')
        (self.directory / 'build.log').write_text('')

    def state(self, **values):
        state = {'id': 'fixture', 'phase': 'queued', 'message': 'Queued', 'action': 'update',
                 'compose_file': 'docker-compose.mac.yml', 'nginx_bin': '/fixture/nginx', **values}
        desktop.write_private(self.directory / 'state.json', json.dumps(state))
        return state

    def test_cached_build_checks_upstream_and_only_starts_after_success(self):
        self.state()
        with patch.object(desktop, 'execute') as execute, patch.object(desktop, 'wait_ready') as ready:
            desktop.run('fixture')
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertEqual(commands[0][-4:], ['stop', '--timeout', '20', 'fernando-desktop'])
        self.assertIn('--pull', commands[1])
        self.assertNotIn('--no-cache', commands[1])
        self.assertIn('--no-build', commands[2])
        self.assertIn('--force-recreate', commands[2])
        self.assertTrue(all(call.args[1]['FERNANDO_COMPOSE_FILE'] == 'docker-compose.mac.yml' for call in execute.call_args_list))
        ready.assert_called_once()
        self.assertEqual(desktop.read_state()['phase'], 'ready')

    def test_failed_build_never_launches_old_image_and_preserves_error(self):
        self.state()
        with patch.object(desktop, 'execute', side_effect=[None, RuntimeError('fixture build failed')]) as execute, patch.object(desktop, 'wait_ready') as ready:
            with self.assertRaisesRegex(RuntimeError, 'fixture build failed'):
                desktop.run('fixture')
        self.assertEqual(execute.call_count, 2)
        ready.assert_not_called()
        self.assertEqual(desktop.status()['phase'], 'failed')
        self.assertIn('fixture build failed', desktop.status()['log'])
        with self.assertRaisesRegex(RuntimeError, 'Desktop unavailable'):
            desktop.require_ready()

    def test_explicit_manual_update_retains_uncached_build(self):
        self.state(no_cache=True)
        with patch.object(desktop, 'execute') as execute, patch.object(desktop, 'wait_ready'):
            desktop.run('fixture')
        self.assertIn('--no-cache', execute.call_args_list[1].args[0])

    def test_mcp_gate_does_not_dispatch_tools_while_unavailable(self):
        source = Path(__file__).parents[1] / 'mcp_servers/desktop_mcp.py'
        tree = ast.parse(source.read_text())
        function, = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'call_tool']
        function.decorator_list = []
        dispatch = AsyncMock(return_value=[])
        namespace = {'require_ready': desktop.require_ready, 'dispatch_tool': dispatch, 'OBSERVED_ACTIONS': set()}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec', flags=__future__.annotations.compiler_flag), namespace)
        self.state(phase='failed', message='Fixture failure')
        with self.assertRaisesRegex(RuntimeError, 'Desktop unavailable'):
            asyncio.run(namespace['call_tool']('desktop_exec', {'command': 'must not execute'}))
        dispatch.assert_not_called()
        self.state(phase='ready')
        asyncio.run(namespace['call_tool']('desktop_exec', {'command': 'fixture'}))
        dispatch.assert_awaited_once()

    def test_missing_password_records_failure(self):
        self.state()
        desktop.PASSWORD_FILE.unlink()
        with self.assertRaises(FileNotFoundError):
            desktop.run('fixture')
        self.assertEqual(desktop.status()['phase'], 'failed')

    def test_stale_worker_cannot_publish_ready(self):
        self.state(id='new-generation')
        with self.assertRaises(InterruptedError):
            desktop.change('old-generation', 'ready', 'Old ready')
        self.assertEqual(desktop.read_state()['phase'], 'queued')

    def test_dead_worker_is_failed_instead_of_waiting_forever(self):
        self.state(pid=os.getpid(), process_started=0, phase='building')
        result = desktop.status()
        self.assertEqual(result['phase'], 'failed')
        self.assertFalse(result['ready'])
        self.assertIn('stopped before completing', result['message'])

    def test_prepare_defers_linux_bridge_listener_and_omits_it_on_mac(self):
        (self.root / 'nginx.desktop-callback.conf.template').write_text('listen 172.25.0.1:{{NGINX_PORT}}; upstream {{FLASK_PORT}};')
        for platform, expected in (('linux', 'listen 172.25.0.1:8088; upstream 5008;'), ('darwin', '')):
            with self.subTest(platform=platform), patch.object(desktop.sys, 'platform', platform):
                desktop.prepare(8088, 5008)
            self.assertEqual(desktop.CALLBACK_FILE.read_text(), '')
            self.assertEqual((self.directory / 'callback-ready.conf').read_text(), expected)

    def test_callback_activated_only_after_authenticated_readiness(self):
        self.state()
        (self.directory / 'callback-ready.conf').write_text('fixture callback')
        events = []
        with patch.object(desktop, 'execute', side_effect=lambda *args: events.append(args[0])), patch.object(desktop, 'wait_ready', side_effect=lambda env: events.append('ready')):
            desktop.run('fixture')
        self.assertEqual(events[3], 'ready')
        self.assertEqual(events[4][1], '-t')
        self.assertEqual(events[5][-2:], ['-s', 'reload'])
        self.assertEqual(desktop.CALLBACK_FILE.read_text(), 'fixture callback')

    def test_readiness_requires_authenticated_200_and_x_server(self):
        with patch.object(desktop.requests, 'Session') as factory, patch.object(desktop, 'execute', side_effect=[1, 0]) as execute, patch.object(desktop.time, 'sleep'):
            client = factory.return_value.__enter__.return_value
            client.get.side_effect = [Mock(status_code=401), Mock(status_code=503), Mock(status_code=200), Mock(status_code=200)]
            desktop.wait_ready({'VNC_PW': 'fixture-password'})
        self.assertEqual(client.get.call_count, 4)
        self.assertFalse(client.trust_env)
        self.assertEqual(client.get.call_args.kwargs['auth'], ('kasm_user', 'fixture-password'))
        self.assertEqual(execute.call_args.args[0][-1], 'xdpyinfo')

    def test_api_auth_waiting_page_and_tools_remain_gated(self):
        from src.routes import web
        self.state(phase='failed', message='build failed')
        (self.directory / 'build.log').write_text('</script><img src=x onerror=alert(1)>')
        app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / 'src/templates'))
        app.register_blueprint(web.bp)
        app.add_url_rule('/fixture-health', view_func=lambda: {'ready': True})
        with patch.object(web, '_check_api_key', side_effect=lambda: request.headers.get('X-API-Key') == 'fixture-key'), patch.object(web, 'open', mock_open(read_data='fixture-key'), create=True), patch.object(desktop, 'launch', return_value={'phase': 'queued', 'ready': False}) as launch:
            client = app.test_client()
            for url in ('/api/desktop/status', '/kasm/'):
                self.assertEqual(client.get(url).status_code, 401)
            self.assertEqual(client.post('/api/desktop/update').status_code, 401)
            launch.assert_not_called()
            headers = {'X-API-Key': 'fixture-key'}
            self.assertEqual(client.get('/api/desktop/status', headers=headers).get_json()['phase'], 'failed')
            page = client.get('/kasm/', headers=headers)
            self.assertEqual(page.status_code, 200)
            self.assertIn('desktopUpdatePage', page.text)
            self.assertNotIn('<img src=x', page.text)
            self.assertEqual(client.get('/kasm/resource.js', headers=headers).status_code, 503)
            self.assertEqual(client.get('/fixture-health').status_code, 200)
            self.assertEqual(client.post('/api/desktop/update', headers=headers).status_code, 202)
            launch.assert_called_once()

    def test_background_worker_does_not_block_and_cancel_prevents_late_start(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/desktop-compose.sh').write_text(
            'case " $* " in *" build "*) echo building; sleep 4;; *" up "*) touch "$PWD/started";; esac\n'
        )
        original = subprocess.Popen
        source_root = Path(__file__).parents[1]

        def spawn(argv, **kwargs):
            code = f'''
import sys
sys.path.insert(0, {str(source_root)!r})
from pathlib import Path
from src.services import desktop_update as d
d.ROOT = Path({str(self.root)!r})
d.DIRECTORY = Path({str(self.directory)!r})
d.PASSWORD_FILE = Path({str(desktop.PASSWORD_FILE)!r})
d.CALLBACK_FILE = Path({str(desktop.CALLBACK_FILE)!r})
d.wait_ready = lambda env: None
d.run(sys.argv[1])
'''
            return original([sys.executable, '-c', code, argv[-1]], **kwargs)

        with patch.object(desktop.subprocess, 'Popen', side_effect=spawn), patch.dict(os.environ, {'FERNANDO_NGINX_BIN': '/fixture/nginx'}):
            started = time.monotonic()
            state = desktop.launch()
            self.addCleanup(desktop.cancel)
            self.assertLess(time.monotonic() - started, 2)
            self.assertFalse(state['ready'])
            deadline = time.monotonic() + 10
            while desktop.status()['phase'] != 'building' and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(desktop.status()['phase'], 'building')
            with self.assertRaisesRegex(ValueError, 'already running'):
                desktop.launch()
            desktop.cancel()
            self.assertEqual(desktop.status()['phase'], 'stopped')
            self.assertFalse((self.root / 'started').exists())
            desktop.launch()
            deadline = time.monotonic() + 12
            while not desktop.status()['ready'] and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual(desktop.status()['phase'], 'ready', (self.directory / 'worker.log').read_text())
            self.assertTrue((self.root / 'started').exists())

    def test_startup_and_mac_wrapper_keep_build_off_foreground_path(self):
        root = Path(__file__).parents[1]
        start = (root / 'scripts/start.sh').read_text()
        self.assertNotIn('up -d --build', start)
        self.assertNotIn('Waiting for Kasm', start)
        self.assertLess(start.index('desktop-update.py" start'), start.index('python run_fernando.py'))
        self.assertLess(start.index('"$NGINX_BIN" -c "$REPO_DIR/nginx.conf" || exit 1'), start.index('desktop-update.py" start'))
        mac = (root / 'mac-start.sh').read_text()
        self.assertLess(mac.index('export FERNANDO_COMPOSE_FILE=docker-compose.mac.yml'), mac.index('scripts/stop.sh'))
        self.assertIn('exec bash "$REPO_DIR/scripts/start.sh" -f', mac)
