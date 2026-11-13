import copy
import json
import os
from pathlib import Path
import subprocess

from src.services.providers.base import Capabilities, Launch, Provider, ROOT
from src.services.providers.probe import probe


INSTALL_ROOT = Path.home() / '.local/share/fernando/claude-code'


class ClaudeCodeProvider(Provider):
    id = 'claude-code'
    name = 'Claude Code'
    model_setting = 'claude_code_model'
    enabled_setting = 'providers_claude_code'
    default_model = 'default'
    initialize_timeout = 30
    capabilities = Capabilities(fork=True, cost=True, effort=True)

    @property
    def executable(self):
        return INSTALL_ROOT / 'current/claude-agent-acp'

    @property
    def login_command(self):
        return str(INSTALL_ROOT / 'current/claude') + ' auth login'

    def metadata(self):
        return {
            **super().metadata(),
            'installed': self.executable.is_file(),
            'setup_instructions': 'Enable, run scripts/update-backends.sh to install or update, then sign in from a terminal:',
            'login_command': self.login_command,
        }

    def require_installation(self):
        if not self.executable.is_file() or not (INSTALL_ROOT / 'current/claude').is_file():
            raise ValueError('Claude Code is not installed. Enable Claude Code in Providers and run scripts/update-backends.sh.')

    def environment(self):
        from src.services.context_templates import clear_inherited_context
        env = dict(os.environ)
        clear_inherited_context(env)
        env.pop('CLAUDE_CODE_EXECUTABLE', None)
        env['CLAUDE_AGENT_ACP_PROTOCOL'] = 'v1'
        env['DISABLE_AUTOUPDATER'] = '1'
        return env

    def validate_start(self):
        self.require_installation()
        result = subprocess.run(
            [str((INSTALL_ROOT / 'current/claude').resolve()), '--setting-sources', '', 'auth', 'status', '--json'],
            capture_output=True, text=True, timeout=15, env=self.environment(), cwd=ROOT,
        )
        if result.returncode not in (0, 1):
            raise ValueError('Claude Code authentication status could not be read. Run ' + self.login_command)
        status = json.loads(result.stdout)
        if not isinstance(status, dict) or not isinstance(status.get('loggedIn'), bool):
            raise ValueError('Claude Code returned an invalid authentication status')
        external = status.get('apiProvider') and status['apiProvider'] != 'firstParty'
        if not status['loggedIn'] and not status.get('apiKeySource') and not external:
            raise ValueError('Claude Code is not signed in. Run ' + self.login_command + ', then create or reopen the chat.')

    def launch(self, session, context_env, context_args):
        self.validate_start()
        return Launch([str(self.executable.resolve())], {**self.environment(), **context_env})

    def prepare_context(self, context, directory, instructions, snapshot):
        return {}, []

    def session_parameters(self, session, native_id=None):
        from src.services.context_templates import RUNTIME
        snapshot = session.context_snapshot
        options = {
            'model': session.model,
            'effort': session.effort,
            'settingSources': [],
            'strictMcpConfig': True,
            'allowDangerouslySkipPermissions': True,
            'mcpServers': copy.deepcopy(snapshot['servers']),
            'systemPrompt': {'type': 'preset', 'preset': 'claude_code'},
            'extraArgs': {
                'system-prompt-snapshot': 'off',
                'append-system-prompt-file': str(RUNTIME / session.id / 'steering.md'),
            },
        }
        params = {
            'cwd': str(ROOT), 'mcpServers': [],
            'configOptions': [{'configId': 'mode', 'value': 'bypassPermissions'}],
            '_meta': {'claudeCode': {'options': options, 'emitRawSDKMessages': [{'type': 'system', 'subtype': 'init'}]}},
        }
        if native_id is not None:
            params['sessionId'] = native_id
        return params

    def validate_session(self, response):
        if response.get('modes', {}).get('currentModeId') != 'bypassPermissions':
            raise RuntimeError('Claude Code did not enable bypassPermissions; refusing to start a chat with hidden approval requirements.')

    def list_models(self):
        self.require_installation()
        result = probe(Launch([str(self.executable.resolve())], self.environment()), 'session/options', {
            'cwd': str(ROOT), '_meta': {'claudeCode': {'options': {
                'settingSources': [], 'strictMcpConfig': True,
                'systemPrompt': {'type': 'preset', 'preset': 'claude_code', 'append': ''},
            }}},
        })
        model_option, = [option for option in result['configOptions'] if option.get('category') == 'model']
        return {'models': [{'id': option['value'], 'name': option['name']} for option in model_option['options']]}

    def normalize_models(self, result):
        return result

    def global_mcp(self, context):
        path = context.HOME / '.claude.json'
        return path, context.read_jsonc(path), 'mcpServers'

    def import_servers(self, context):
        result = {}
        for path in (context.HOME / '.claude.json', context.ROOT / '.mcp.json'):
            for name, definition in context.read_jsonc(path).get('mcpServers', {}).items():
                result[name] = (definition, True)
        return result

    def decode_server(self, definition):
        value = copy.deepcopy(definition)
        if 'command' in value:
            if not isinstance(value['command'], str) or not value['command']:
                raise ValueError('Claude Code MCP command must be a nonempty string')
            args = value.pop('args', [])
            if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                raise ValueError('Claude Code MCP arguments must be a string array')
            value['command'] = [value['command']] + args
        return value

    def encode_server(self, definition):
        value = copy.deepcopy(definition)
        if value.get('command'):
            command = value['command']
            value['command'], value['args'] = command[0], command[1:]
            value['type'] = 'stdio'
        elif value.get('url'):
            value.setdefault('type', 'http')
            if value['type'] not in ('http', 'sse'):
                raise ValueError('Claude Code remote MCP type must be http or sse')
        else:
            raise ValueError('MCP server needs a command or URL')
        return value

    def check_mcp_startup(self, session):
        pass

    def handle_notification(self, session, method, params):
        if method != '_claude/sdkMessage':
            return False
        message = params.get('message', {})
        if message.get('type') == 'system' and message.get('subtype') == 'init':
            for server in message.get('mcp_servers', []):
                status = server.get('status')
                session.mcp_startup.update(session.mcp_startup.generation, server['name'],
                                          'connected' if status == 'connected' else 'failed',
                                          '' if status == 'connected' else f'Claude Code MCP status: {status}')
        return True

    def fork_session(self, session, history=None, boundary=None, turn_index=None):
        if boundary is not None:
            raise ValueError('Claude Code does not support selected-turn forks in Fernando')
        result = session._request('session/fork', self.session_parameters(session, session.acp_session_id), timeout=120, return_error=True)
        if not result or 'error' in result:
            raise ValueError('Claude Code could not fork this conversation')
        self.validate_session(result)
        return result['sessionId']
