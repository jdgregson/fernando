import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask, request

from src.services import acp, context_templates, providers, settings
from src.services.providers.base import Capabilities, Launch, Provider
from src.services.providers import kiro_sessions, models


class FixtureProvider(Provider):
    id = 'fixture'
    name = 'Fixture Agent'
    model_setting = 'fixture_model'
    enabled_setting = 'providers_fixture'
    default_model = 'fixture/default'
    capabilities = Capabilities()
    context_paths = ('FIXTURE_CONTEXT',)

    def launch(self, session, context_env, context_args):
        return Launch(['fixture-agent', 'acp'] + context_args, context_env)

    def prepare_context(self, context, directory, instructions, snapshot):
        context._write(directory / 'fixture.json', {'instructions': str(instructions), 'tools': snapshot['servers']})
        return {'FIXTURE_CONTEXT': str(directory)}, []

    def list_models(self):
        return {'models': ['fixture/default', 'fixture/other']}

    def decode_server(self, definition):
        return copy.deepcopy(definition)

    def encode_server(self, definition):
        return copy.deepcopy(definition)

    def global_mcp(self, context):
        path = context.HOME / 'fixture.json'
        return path, context.read_jsonc(path), 'tools'

    def import_servers(self, context):
        return {}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.provider = FixtureProvider()
        for target, key, value in (
            (context_templates, 'HOME', self.root / 'home'),
            (context_templates, 'RUNTIME', self.root / 'runtime'),
            (context_templates, 'STORE', self.root / 'context.json'),
            (settings, '_SETTINGS_FILE', str(self.root / 'settings.json')),
        ):
            patcher = patch.object(target, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(providers._PROVIDERS, {'fixture': self.provider})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_third_provider_drives_settings_and_context_without_backend_branches(self):
        settings.set('fixture_model', 'fixture/other')
        settings.set('providers_fixture', True)
        configured = settings.get_provider_settings()
        self.assertEqual(configured['providers'][-1]['model'], 'fixture/other')
        self.assertTrue(configured['providers'][-1]['enabled'])
        session = acp.ACPSession('fixture1', backend='fixture')
        self.assertEqual(session.model, 'fixture/other')
        server = context_templates.server_config({'kiro': {'command': '/usr/bin/python3', 'args': ['tool.py'], 'env': {'SCOPE': 'chosen'}}}, 'fixture')
        self.assertEqual(server, {'command': ['/usr/bin/python3', 'tool.py'], 'env': {'SCOPE': 'chosen'}})
        snapshot = {'servers': {'tool': server}, 'documents': [{'name': 'rules', 'path': '/rules.md', 'content': 'selected only'}]}
        env, args = context_templates.prepare(session.id, snapshot, 'fixture')
        self.assertEqual(self.provider.launch(session, env, args).command, ['fixture-agent', 'acp'])
        config = json.loads((Path(env['FIXTURE_CONTEXT']) / 'fixture.json').read_text())
        self.assertIn('selected only', Path(config['instructions']).read_text())
        self.assertEqual(config['tools'], snapshot['servers'])
        context_templates.clear_inherited_context(env)
        self.assertEqual(env, {})

    def test_unknown_provider_never_becomes_kiro_even_without_tools(self):
        for value in ('missing', '', None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                acp.ACPSession('unknown1', backend=value)
        with self.assertRaises(ValueError):
            context_templates.resolve(None, 'missing')

    def test_opencode_permissions_override_inherited_approval_rules(self):
        provider = providers.get_provider('opencode')
        snapshot = {'servers': {}, 'documents': []}
        inherited = {'permission': {'edit': 'ask'}, 'agent': {'build': {'permission': {'bash': 'ask'}}}}
        with patch.object(context_templates, 'opencode_base', return_value=inherited):
            env, args = provider.prepare_context(context_templates, self.root, self.root / 'steering.md', snapshot)
        config = json.loads(Path(env['OPENCODE_CONFIG']).read_text())
        self.assertEqual(config['permission'], 'allow')
        self.assertEqual(config['agent']['build']['permission'], 'allow')
        session = acp.ACPSession('allowall', backend='opencode')
        for model in ('', 'provider/model'):
            session.model = model
            launch = provider.launch(session, env, args)
            inline = json.loads(launch.environment['OPENCODE_CONFIG_CONTENT'])
            self.assertEqual(inline['permission'], 'allow')

    def test_kiro_launch_trusts_all_tools(self):
        session = acp.ACPSession('allowall', backend='kiro')
        launch = session.provider.launch(session, {}, [])
        self.assertIn('-a', launch.command)

    def test_shared_acp_launch_handshake_create_and_resume_for_third_provider(self):
        session = acp.ACPSession('fixture1', backend='fixture')
        snapshot = {'servers': {}, 'documents': [], 'initial_prompt': ''}
        with (
            patch.object(context_templates, 'resolve', return_value=snapshot),
            patch('src.services.groups.get_session_groups', return_value={}),
            patch.object(acp.subprocess, 'Popen', return_value=Mock(pid=123)) as popen,
            patch.object(threading.Thread, 'start'),
            patch.object(threading.Timer, 'start'),
            patch.object(session, '_request', side_effect=[{'protocolVersion': 1}, {'sessionId': 'native-fixture'}]) as rpc,
        ):
            session.start()
        self.assertEqual(popen.call_args.args[0], ['fixture-agent', 'acp'])
        self.assertEqual([call.args[0] for call in rpc.call_args_list], ['initialize', 'session/new'])
        self.assertEqual(session.acp_session_id, 'native-fixture')
        with (
            patch.object(session, '_spawn_and_init'),
            patch.object(session, '_load_history'),
            patch.object(session, '_patch_our_incomplete_mutate'),
            patch.object(session, '_check_mcp_startup'),
            patch.object(session, '_request', return_value={'sessionId': 'native-fixture'}) as rpc,
        ):
            session.load('native-fixture')
        self.assertEqual(rpc.call_args.args[0], 'session/load')
        self.assertEqual(rpc.call_args.args[1]['model'], 'fixture/default')

    def test_unsupported_fork_rejected_before_native_operation(self):
        session = acp.ACPSession('fixture1', backend='fixture')
        session.proc = Mock()
        session.proc.poll.return_value = None
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager._lock = threading.Lock()
        manager.sessions = {session.id: session}
        with self.assertRaisesRegex(ValueError, 'does not support'):
            manager.clone_session(session.id)
        with self.assertRaisesRegex(ValueError, 'does not support'):
            manager.fork_at_turn(session.id, 1)

    def test_provider_model_and_registry_api_authentication(self):
        from src.routes import web
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        client = app.test_client()
        with (
            patch.object(web, '_check_api_key', side_effect=lambda: request.headers.get('X-API-Key') == 'test-key'),
            patch.dict(models._cache, {}, clear=True),
        ):
            for endpoint in ('/api/providers', '/api/providers/fixture/models', '/api/models', '/api/opencode_models'):
                self.assertEqual(client.get(endpoint).status_code, 401)
            headers = {'X-API-Key': 'test-key'}
            metadata = client.get('/api/providers', headers=headers).get_json()
            self.assertEqual(metadata['providers'][-1]['id'], 'fixture')
            response = client.get('/api/providers/fixture/models', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['models'][0], {'id': 'fixture/default', 'name': 'fixture/default'})
            self.assertEqual(client.get('/api/providers/missing/models', headers=headers).status_code, 404)
            with patch.object(providers.get_provider('opencode'), 'list_models', side_effect=subprocess.TimeoutExpired('agent', 30)):
                self.assertEqual(client.get('/api/opencode_models', headers=headers).status_code, 502)

    def test_provider_model_cache_is_independent(self):
        kiro = providers.get_provider('kiro')
        with (
            patch.dict(models._cache, {}, clear=True),
            patch.object(kiro, 'list_models', return_value={'models': [{'model_id': 'kiro/model', 'model_name': 'Kiro Model'}]}) as discover,
        ):
            self.assertEqual(models.normalized_models('kiro')['models'], [{'id': 'kiro/model', 'name': 'Kiro Model'}])
            models.normalized_models('fixture')
            models.normalized_models('kiro')
            discover.assert_called_once()

    def test_native_restart_repair_is_idempotent(self):
        path = self.root / 'native.jsonl'
        entry = {'kind': 'AssistantMessage', 'data': {'content': [{'kind': 'toolUse', 'data': {'name': 'mutate', 'toolUseId': 'tool-1'}}]}}
        path.write_text(json.dumps(entry) + '\n')
        with patch.object(kiro_sessions, 'SESSIONS_DIR', str(self.root)):
            kiro_sessions.repair_interrupted_restart('native')
            first = path.read_bytes()
            kiro_sessions.repair_interrupted_restart('native')
            self.assertEqual(path.read_bytes(), first)
        self.assertEqual(json.loads(path.read_text().splitlines()[-1])['data']['content'][0]['data']['toolUseId'], 'tool-1')

    def test_native_restart_repair_handles_text_and_nonmessage_records(self):
        records = [
            {'kind': 'Prompt', 'data': {'content': [{'kind': 'text', 'data': 'user message'}]}},
            {'kind': 'AssistantMessage', 'data': {'content': [
                {'kind': 'text', 'data': 'restarting'},
                {'kind': 'toolUse', 'data': {'name': 'mutate', 'toolUseId': 'restart'}},
                {'kind': 'text', 'data': 'status text'},
            ]}},
            {'kind': 'Metadata', 'data': 'not a message'},
            {'kind': 'AssistantMessage', 'data': {'content': [{'kind': 'text', 'data': 'later text'}]}},
        ]
        path = self.root / 'mixed.jsonl'
        original = ''.join(json.dumps(record) + '\n' for record in records).encode()
        path.write_bytes(original)
        with patch.object(kiro_sessions, 'SESSIONS_DIR', str(self.root)):
            kiro_sessions.repair_interrupted_restart('mixed')
            repaired = path.read_bytes()
            self.assertTrue(repaired.startswith(original))
            added = json.loads(repaired[len(original):])
            self.assertEqual(added['data']['content'][0]['data']['toolUseId'], 'restart')
            kiro_sessions.repair_interrupted_restart('mixed')
            self.assertEqual(path.read_bytes(), repaired)

    def test_completed_restart_with_surrounding_text_keeps_history_unchanged(self):
        records = [
            {'kind': 'AssistantMessage', 'data': {'content': [
                {'kind': 'text', 'data': 'before tool'},
                {'kind': 'toolUse', 'data': {'name': 'mutate', 'toolUseId': 'completed'}},
            ]}},
            {'kind': 'ToolResults', 'data': {'content': [
                {'kind': 'toolResult', 'data': {'toolUseId': 'completed', 'content': [{'kind': 'text', 'data': 'done'}]}},
            ]}},
            {'kind': 'AssistantMessage', 'data': {'content': [{'kind': 'text', 'data': 'all done'}]}},
            {'kind': 'Prompt', 'data': {'content': [{'kind': 'text', 'data': 'next question'}]}},
        ]
        path = self.root / 'completed.jsonl'
        original = ''.join(json.dumps(record) + '\n' for record in records).encode()
        path.write_bytes(original)
        with patch.object(kiro_sessions, 'SESSIONS_DIR', str(self.root)):
            kiro_sessions.repair_interrupted_restart('completed')
        self.assertEqual(path.read_bytes(), original)

    def test_opencode_launch_has_private_authenticated_loopback(self):
        provider = providers.get_provider('opencode')
        first = acp.ACPSession('fixture1', backend='opencode')
        second = acp.ACPSession('fixture2', backend='opencode')
        one = provider.launch(first, {'OPENCODE_CONFIG': '/private/one.json'}, [])
        two = provider.launch(second, {'OPENCODE_CONFIG': '/private/two.json'}, [])
        self.assertIn('127.0.0.1', one.command)
        self.assertIn('--mdns=false', one.command)
        self.assertNotEqual(one.environment['OPENCODE_SERVER_PASSWORD'], two.environment['OPENCODE_SERVER_PASSWORD'])
        self.assertEqual(one.environment['OPENCODE_CONFIG'], '/private/one.json')
        self.assertEqual(two.environment['OPENCODE_CONFIG'], '/private/two.json')

    def test_provider_driven_templates_render_third_backend_and_compile(self):
        from jinja2 import Environment, FileSystemLoader
        from html.parser import HTMLParser
        environment = Environment(loader=FileSystemLoader(str(Path(__file__).parents[1] / 'src/templates')), autoescape=True)
        html = environment.get_template('index.html').render(provider_settings=settings.get_provider_settings())
        self.assertIn('data-provider="fixture"', html)
        self.assertIn('data-provider-model="fixture"', html)
        self.assertIn('Fixture Agent Chat', html)

        class Scripts(HTMLParser):
            def __init__(self):
                super().__init__()
                self.active = False
                self.parts = []

            def handle_starttag(self, tag, attrs):
                self.active = tag == 'script' and 'src' not in dict(attrs)

            def handle_endtag(self, tag):
                if tag == 'script':
                    self.active = False

            def handle_data(self, data):
                if self.active:
                    self.parts.append(data)

        for template in ('index.html', 'chat.html'):
            parser = Scripts()
            parser.feed(environment.get_template(template).render(
                provider_settings=settings.get_provider_settings(), session_id='fixture1', api_key='fixture-key'))
            subprocess.run([os.environ['NODE'], '--check'], input='\n'.join(parser.parts), text=True, check=True, capture_output=True)
