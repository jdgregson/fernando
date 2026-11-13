from contextlib import ExitStack
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from src.services import acp, chat_history, context_templates
from src.services.providers import get_provider
from src.services.providers import claude_code


spec = importlib.util.spec_from_file_location('claude_updater', Path(__file__).parents[1] / 'scripts/update-claude-code.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class ClaudeProviderTests(unittest.TestCase):
    def test_registry_and_disabled_default(self):
        from src.services import settings
        provider = get_provider('claude-code')
        with patch.object(settings, 'get_all', return_value={}):
            entries = settings.get_provider_settings()['providers']
        entry, = [entry for entry in entries if entry['id'] == 'claude-code']
        self.assertFalse(entry['enabled'])
        self.assertEqual(entry['name'], 'Claude Code')
        self.assertIn('auth login', entry['login_command'])
        self.assertFalse(provider.capabilities.fork_at_turn)

    def test_enable_toggle_persists_and_drives_chat_card(self):
        from jinja2 import Environment, FileSystemLoader
        from src.services import settings
        environment = Environment(loader=FileSystemLoader(str(Path(__file__).parents[1] / 'src/templates')), autoescape=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, '_SETTINGS_FILE', str(Path(directory) / 'settings.json')):
            settings.set('providers_claude_code', True)
            settings.set('claude_code_model', 'sonnet')
            data = settings.get_provider_settings()
            entry, = [entry for entry in data['providers'] if entry['id'] == 'claude-code']
            self.assertTrue(entry['enabled'])
            self.assertEqual(entry['model'], 'sonnet')
            html = environment.get_template('index.html').render(provider_settings=data)
            self.assertIn('data-provider="claude-code" onclick="createChatSession(this.dataset.provider)">', html)
            self.assertIn('data-setting="providers_claude_code"', html)
            self.assertNotIn('<code>' + entry['login_command'] + '</code>', html)
            settings.set('providers_claude_code', False)
            html = environment.get_template('index.html').render(provider_settings=settings.get_provider_settings())
            self.assertIn('data-provider="claude-code" onclick="createChatSession(this.dataset.provider)" style="display: none">', html)
            self.assertRegex(html, r'<select id="provider-model-claude-code"[^>]* disabled>')

    def test_missing_installation_and_signed_out_fail_explicitly(self):
        provider = get_provider('claude-code')
        with tempfile.TemporaryDirectory() as directory, patch.object(claude_code, 'INSTALL_ROOT', Path(directory)):
            with self.assertRaisesRegex(ValueError, 'not installed'):
                provider.validate_start()
            (Path(directory) / 'current').mkdir()
            for name in ('claude-agent-acp', 'claude'):
                (Path(directory) / 'current' / name).touch()
            result = subprocess.CompletedProcess([], 1, json.dumps({'loggedIn': False, 'authMethod': 'none', 'apiProvider': 'firstParty'}), '')
            with patch.object(claude_code.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'not signed in.*auth login'):
                    provider.validate_start()
                manager = acp.ACPManager.__new__(acp.ACPManager)
                with self.assertRaisesRegex(ValueError, 'not signed in'):
                    manager.create_session(backend='claude-code')

    def test_selected_context_and_resume_are_passed_explicitly(self):
        provider = get_provider('claude-code')
        session = acp.ACPSession('claude01', backend='claude-code')
        server = context_templates.server_config({'kiro': {'command': '/python', 'args': ['selected.py'], 'env': {'SCOPE': 'selected'}}}, 'claude-code')
        session.context_snapshot = {'servers': {'selected': server}, 'documents': [{'name': 'rules', 'path': '/rules.md', 'content': 'selected steering'}]}
        session.model = 'sonnet'
        for native in (None, 'native-session'):
            params = provider.session_parameters(session, native)
            options = params['_meta']['claudeCode']['options']
            self.assertEqual(options['settingSources'], [])
            self.assertTrue(options['strictMcpConfig'])
            self.assertTrue(options['allowDangerouslySkipPermissions'])
            self.assertEqual(params['configOptions'], [{'configId': 'mode', 'value': 'bypassPermissions'}])
            self.assertEqual(options['mcpServers']['selected']['args'], ['selected.py'])
            self.assertTrue(options['extraArgs']['append-system-prompt-file'].endswith('/claude01/steering.md'))
            self.assertEqual(options['model'], 'sonnet')
            self.assertEqual(options['effort'], session.effort)
            self.assertEqual(options['extraArgs']['system-prompt-snapshot'], 'off')
            self.assertEqual(params.get('sessionId'), native)

    def test_native_model_catalog_preserves_labels_without_static_models(self):
        provider = get_provider('claude-code')
        with patch.object(provider, 'require_installation'), patch.object(claude_code, 'probe', return_value={
            'configOptions': [{'category': 'model', 'options': [{'value': 'new-model', 'name': 'New Model'}]}],
        }):
            self.assertEqual(provider.list_models(), {'models': [{'id': 'new-model', 'name': 'New Model'}]})

    def test_permission_mode_must_be_confirmed_by_adapter(self):
        provider = get_provider('claude-code')
        provider.validate_session({'modes': {'currentModeId': 'bypassPermissions'}})
        for response in ({}, {'modes': {'currentModeId': 'auto'}}, {'modes': {'currentModeId': 'default'}}):
            with self.assertRaisesRegex(RuntimeError, 'hidden approval'):
                provider.validate_session(response)

    def test_mcp_status_from_native_init_is_not_recorded_as_conversation(self):
        session = acp.ACPSession('claude01', backend='claude-code')
        session.mcp_startup.begin(['selected', 'broken'])
        session._dispatch({'method': '_claude/sdkMessage', 'params': {'message': {
            'type': 'system', 'subtype': 'init', 'mcp_servers': [
                {'name': 'selected', 'status': 'connected'}, {'name': 'broken', 'status': 'failed'},
            ],
        }}})
        self.assertEqual(session.history, [])
        self.assertEqual([item['name'] for item in session.mcp_startup.snapshot()['failures']], ['broken'])


class ClaudeUpdaterTests(unittest.TestCase):
    def test_disabled_provider_does_not_fetch_or_create_installation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updater, 'fetch') as fetch:
            root = Path(directory)
            updater.update(root / 'missing-settings.json', root / 'install')
            fetch.assert_not_called()
            self.assertFalse((root / 'install').exists())

    def archive(self, symlink=False):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for name in ('claude-agent-acp', 'claude'):
                member = tarfile.TarInfo(name)
                if symlink and name == 'claude':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/outside'
                    archive.addfile(member)
                else:
                    payload = b'fixture binary'
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        return output.getvalue()

    def test_install_and_checksum_failure_preserve_atomic_pair(self):
        archive = self.archive()
        name = 'claude-agent-acp-linux-x64.tar.gz'
        asset = {'name': name, 'digest': 'sha256:' + hashlib.sha256(archive).hexdigest(),
                 'size': len(archive), 'browser_download_url': f'https://github.com/{updater.REPOSITORY}/releases/download/v1.2.3/{name}'}
        release = {'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False, 'assets': [asset]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'settings.json'
            settings.write_text('{"providers_claude_code":true}')
            install = root / 'install'
            with (
                patch.object(updater.platform, 'machine', return_value='x86_64'),
                patch.object(updater, 'fetch', side_effect=[io.BytesIO(json.dumps(release).encode()), io.BytesIO(archive)]),
                patch.object(updater, 'binary_version', side_effect=lambda path: '9.8.7 (Claude Code)' if path.name == 'claude' else '1.2.3'),
            ):
                updater.update(settings, install)
            current = install / 'current'
            self.assertEqual(current.resolve().name, '1.2.3')
            self.assertTrue((current / 'claude').is_file())
            self.assertTrue((current / 'claude-agent-acp').is_file())
            release['tag_name'] = 'v1.2.4'
            asset['browser_download_url'] = asset['browser_download_url'].replace('v1.2.3', 'v1.2.4')
            before = current.resolve()
            with (
                patch.object(updater.platform, 'machine', return_value='x86_64'),
                patch.object(updater, 'fetch', side_effect=[io.BytesIO(json.dumps(release).encode()), io.BytesIO(b'tampered')]),
                patch.object(updater, 'binary_version', return_value='1.2.3'),
            ):
                with self.assertRaisesRegex(ValueError, 'checksum or size mismatch'):
                    updater.update(settings, install)
            self.assertEqual(current.resolve(), before)
            self.assertFalse((install / 'versions/1.2.4').exists())

    def test_archive_rejects_symlink_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'release.tar.gz'
            archive.write_bytes(self.archive(symlink=True))
            with self.assertRaisesRegex(ValueError, 'not a regular file'):
                updater.extract_binaries(archive, root)


@unittest.skipUnless(os.environ.get('RUN_CLAUDE_INTEGRATION') == '1', 'Explicit native Claude ACP integration')
class ClaudeNativeTests(unittest.TestCase):
    def test_populated_native_conversation_mcp_steering_resume_and_fork(self):
        received = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if 'count_tokens' in self.path:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":100}')
                    return
                received.append(body)
                latest = body['messages'][-1]['content']
                tool = next((item['name'] for item in body.get('tools', []) if 'alpha_probe' in item['name']), None)
                completed = any(isinstance(message['content'], list) and any(
                    item.get('type') == 'tool_result' and item.get('tool_use_id') == 'toolu_fixture'
                    for item in message['content']) for message in body['messages'])
                wants_tool = 'fixture-tool-request' in json.dumps(body['messages']) and not completed
                file_request = 'fixture-write:' in json.dumps(body['messages'])
                file_completed = any(isinstance(message['content'], list) and any(
                    item.get('type') == 'tool_result' and item.get('tool_use_id') == 'toolu_write'
                    for item in message['content']) for message in body['messages'])
                if file_request and not file_completed:
                    content = {'type': 'tool_use', 'id': 'toolu_write', 'name': 'Write',
                               'input': {'file_path': str(target), 'content': 'native permission check passed\n'}}
                    stop = 'tool_use'
                elif wants_tool and tool:
                    content = {'type': 'tool_use', 'id': 'toolu_fixture', 'name': tool, 'input': {}}
                    stop = 'tool_use'
                elif wants_tool and any(item['name'] == 'ToolSearch' for item in body.get('tools', [])):
                    content = {'type': 'tool_use', 'id': 'toolu_search', 'name': 'ToolSearch',
                               'input': {'query': 'select:mcp__selected__alpha_probe', 'max_results': 1}}
                    stop = 'tool_use'
                else:
                    content = {'type': 'text', 'text': 'fixture response'}
                    stop = 'end_turn'
                message = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': body.get('model', 'fixture'),
                           'content': [content], 'stop_reason': stop, 'stop_sequence': None,
                           'usage': {'input_tokens': 100, 'output_tokens': 10}}
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
                self.end_headers()
                if body.get('stream'):
                    initial = {**message, 'content': [], 'stop_reason': None}
                    block = {**content, 'text': ''} if content['type'] == 'text' else {**content, 'input': {}}
                    delta = {'type': 'text_delta', 'text': content['text']} if content['type'] == 'text' else {'type': 'input_json_delta', 'partial_json': json.dumps(content['input'])}
                    events = [
                        ('message_start', {'message': initial}),
                        ('content_block_start', {'index': 0, 'content_block': block}),
                        ('content_block_delta', {'index': 0, 'delta': delta}),
                        ('content_block_stop', {'index': 0}),
                        ('message_delta', {'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': 10}}),
                        ('message_stop', {}),
                    ]
                    for kind, payload in events:
                        self.wfile.write(('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **payload}) + '\n\n').encode())
                    self.wfile.flush()
                else:
                    self.wfile.write(json.dumps(message).encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch.dict(os.environ, {
                'CLAUDE_CONFIG_DIR': str(root / 'claude'),
                'ANTHROPIC_API_KEY': 'fixture-not-a-real-key',
                'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{server.server_port}',
                'DISABLE_NONESSENTIAL_TRAFFIC': '1',
            }))
            stack.enter_context(patch.object(context_templates, 'RUNTIME', root / 'context'))
            stack.enter_context(patch.object(chat_history, 'ROOT', root))
            stack.enter_context(patch.object(chat_history, 'HISTORY_DIR', root / 'history'))
            stack.enter_context(patch.object(acp, 'HISTORY_DIR', str(root / 'history')))
            stack.enter_context(patch.object(acp.ACPSession, '_index_rag_background'))
            snapshot = {
                'servers': {'selected': {'command': sys.executable, 'args': [str(Path(__file__).with_name('context_mcp_fixture.py')), 'alpha'], 'type': 'stdio'}},
                'documents': [{'name': 'test rules', 'path': '/fixture.md', 'content': 'CLAUDE_SELECTED_STEERING'}],
                'initial_prompt': '',
            }
            stack.enter_context(patch.object(context_templates, 'resolve', side_effect=lambda *args: json.loads(json.dumps(snapshot))))
            events = []
            session = acp.ACPSession('c1a0de01', backend='claude-code', on_event=lambda sid, event: events.append(event))
            stack.callback(session.unload)
            session.start()
            session.ready = True
            native_id = session.acp_session_id
            session.send_prompt('fixture-tool-request')
            self.wait_for(lambda: not session._is_prompting)
            self.assertTrue(any('CLAUDE_SELECTED_STEERING' in json.dumps(body.get('system')) for body in received))
            self.assertTrue(any('alpha_probe' in json.dumps(body.get('tools')) for body in received), {
                'tools': [[tool.get('name') for tool in body.get('tools', [])] for body in received],
                'mcp': session.mcp_startup.snapshot(),
                'methods': sorted({event.get('method', '') for event in events}),
            })
            self.assertTrue(any('tool_result' in json.dumps(body['messages']) and 'alpha' in json.dumps(body['messages']) for body in received))
            self.assertTrue(any('fixture response' in json.dumps(event) for event in events))
            self.assertEqual(session.mcp_startup.snapshot()['failures'], [])
            target = root / 'native-write.txt'
            session.send_prompt('fixture-write:' + str(target))
            self.wait_for(lambda: not session._is_prompting)
            self.assertEqual(target.read_text(), 'native permission check passed\n')
            session.unload()
            snapshot['documents'][0]['content'] = 'CLAUDE_CHANGED_STEERING'
            session.load(native_id)
            session.ready = True
            session.send_prompt('second native turn')
            self.wait_for(lambda: not session._is_prompting)
            self.assertEqual(session.acp_session_id, native_id)
            self.assertTrue(any('CLAUDE_CHANGED_STEERING' in json.dumps(body.get('system')) for body in received))
            self.assertTrue(any('fixture-tool-request' in json.dumps(body['messages']) and 'second native turn' in json.dumps(body['messages']) for body in received))
            fork_id = session.provider.fork_session(session)
            self.assertNotEqual(fork_id, native_id)
            child = acp.ACPSession('c1a0de02', backend='claude-code')
            stack.callback(child.unload)
            child.load(fork_id)
            child.ready = True
            child.send_prompt('fork native turn')
            self.wait_for(lambda: not child._is_prompting)
            self.assertTrue(any('second native turn' in json.dumps(body['messages']) and 'fork native turn' in json.dumps(body['messages']) for body in received))
            self.assertFalse(any(event.get('type') == 'session_error' for event in events))

    def wait_for(self, condition):
        deadline = time.monotonic() + 60
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(condition(), 'Timed out waiting for native Claude ACP')
