import copy
import json
import os
import secrets
import subprocess
from urllib.parse import quote

import psutil
import requests

from src.services.providers.base import Capabilities, Launch, Provider, configured_environment


class OpenCodeProvider(Provider):
    id = 'opencode'
    name = 'OpenCode'
    model_setting = 'opencode_model'
    enabled_setting = 'providers_opencode'
    default_model = 'amazon-bedrock/us.anthropic.claude-opus-4-6-v1'
    initialize_timeout = 30
    icon_path = 'M4 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z M5 6l3 2-3 2 M9 10h3'
    capabilities = Capabilities(fork=True, fork_at_turn=True, cost=True, quota=True)
    context_paths = ('XDG_CONFIG_HOME', 'OPENCODE_CONFIG', 'OPENCODE_CONFIG_DIR')
    context_flags = ('OPENCODE_DISABLE_PROJECT_CONFIG', 'OPENCODE_DISABLE_CLAUDE_CODE_PROMPT')

    @property
    def executable(self):
        return os.path.expanduser('~/.opencode/bin/opencode')

    def launch(self, session, context_env, context_args):
        env = configured_environment()
        env.update(context_env)
        if session.model:
            env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'model': session.model})
        session._opencode_password = secrets.token_urlsafe(32)
        env['OPENCODE_SERVER_USERNAME'] = 'fernando'
        env['OPENCODE_SERVER_PASSWORD'] = session._opencode_password
        return Launch([self.executable, 'acp', '--hostname', '127.0.0.1', '--port', '0', '--mdns=false'], env)

    def prepare_context(self, context, directory, instructions, snapshot):
        config = context.opencode_base()
        config['mcp'] = copy.deepcopy(snapshot['servers'])
        config['instructions'] = [str(instructions)]
        config['$schema'] = 'https://opencode.ai/config.json'
        path = directory / 'config/opencode/opencode.json'
        context._write(path, config)
        return {
            'XDG_CONFIG_HOME': str(directory / 'config'),
            'OPENCODE_CONFIG': str(path), 'OPENCODE_CONFIG_DIR': str(path.parent),
            'OPENCODE_DISABLE_PROJECT_CONFIG': '1', 'OPENCODE_DISABLE_CLAUDE_CODE_PROMPT': '1',
        }, []

    def global_mcp(self, context):
        path = context.HOME / '.config/opencode/opencode.jsonc'
        return path, context.read_jsonc(path), 'mcp'

    def import_servers(self, context):
        return {name: (definition, definition.get('enabled', True))
                for name, definition in context.opencode_base().get('mcp', {}).items()}

    def decode_server(self, definition):
        value = copy.deepcopy(definition)
        command = value.get('command')
        if 'command' in value and (not isinstance(command, list) or not command
                                  or any(not isinstance(arg, str) or not arg for arg in command)):
            raise ValueError('OpenCode MCP command must be a nonempty string array')
        if 'type' in value and value['type'] not in ('local', 'remote'):
            raise ValueError('OpenCode MCP type must be local or remote')
        value.pop('enabled', None)
        if 'environment' in value:
            value['env'] = value.pop('environment')
        return value

    def encode_server(self, definition):
        value = copy.deepcopy(definition)
        value.setdefault('type', 'remote' if value.get('url') else 'local')
        command = value.get('command')
        if not value.get('url') and (not isinstance(command, list) or not command
                                    or any(not isinstance(arg, str) or not arg for arg in command)):
            raise ValueError('OpenCode MCP command must be a nonempty string array')
        if 'env' in value:
            value['environment'] = value.pop('env')
        value['enabled'] = True
        return value

    def list_models(self):
        result = subprocess.run([self.executable, 'models'], capture_output=True, text=True,
                                timeout=30, env=configured_environment(), check=True)
        return {'models': [line.strip() for line in result.stdout.splitlines() if line.strip()]}

    def matches_native_id(self, native_id):
        return native_id.startswith('ses_')

    def request(self, session, method, path, body=None):
        process = session.proc
        if process is None or process.poll() is not None:
            raise RuntimeError('OpenCode session is not loaded')
        listeners = [c.laddr.port for c in psutil.Process(process.pid).net_connections(kind='tcp')
                     if c.status == psutil.CONN_LISTEN and c.laddr.ip == '127.0.0.1']
        if len(listeners) != 1:
            raise RuntimeError('Expected one OpenCode loopback HTTP listener')
        with requests.Session() as client:
            client.trust_env = False
            response = client.request(
                method, f'http://127.0.0.1:{listeners[0]}{path}',
                auth=('fernando', session._opencode_password), json=body,
                params={'directory': os.path.expanduser('~/fernando')},
                timeout=(5, 60), allow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f'OpenCode HTTP request failed ({response.status_code})')
            return response.json()

    def check_mcp_startup(self, session):
        generation = session.mcp_startup.generation
        try:
            statuses = self.request(session, 'GET', '/mcp')
        except (requests.RequestException, RuntimeError, ValueError) as error:
            session.mcp_startup.unconfirmed(generation, f'Unable to verify MCP startup: {error}')
        else:
            session.mcp_startup.opencode(generation, statuses)

    def fork_session(self, session, history=None, boundary=None, turn_index=None):
        path = '/session/' + quote(session.acp_session_id, safe='')
        body = {}
        if boundary is not None:
            messages = self.request(session, 'GET', path + '/message')
            users = [m for m in messages if m['info']['role'] == 'user'
                     and any(p['type'] == 'text' and not p.get('synthetic') for p in m['parts'])]
            prompts = [e for e in history[:boundary + 1]
                       if e.get('type') in ('user_prompt', 'continuation', 'agent_message')]
            if len(users) < len(prompts):
                raise ValueError('Selected turn has not reached OpenCode yet; retry once it has been accepted')
            for event, message in zip(prompts, users):
                text = event['text']
                if event['type'] == 'agent_message':
                    text = f"[AGENT MESSAGE from {event['from']}] {text}"
                native = ''.join(p['text'] for p in message['parts']
                                 if p['type'] == 'text' and not p.get('synthetic'))
                if text != native:
                    raise ValueError('Fernando and OpenCode prompt histories do not match; refusing an ambiguous fork')
            body['messageID'] = users[len(prompts) - 1]['info']['id']
        return self.request(session, 'POST', path + '/fork', body)['id']
