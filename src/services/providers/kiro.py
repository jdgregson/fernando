import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

from src.services.providers.base import Capabilities, Launch, Provider, ROOT
from src.services.providers import kiro_sessions


class KiroProvider(Provider):
    id = 'kiro'
    name = 'Kiro'
    model_setting = 'default_model'
    enabled_setting = None
    default_model = 'claude-opus-4.6'
    capabilities = Capabilities(fork=True, fork_at_turn=True, effort=True, slash_commands=True)
    context_paths = ('KIRO_HOME',)

    @property
    def executable(self):
        return shutil.which('kiro-cli') or os.path.expanduser('~/.local/bin/kiro-cli')

    def launch(self, session, context_env, context_args):
        return Launch(
            [self.executable, 'acp', '-a', '--model', session.model, '--effort', session.effort] + context_args,
            {**os.environ, **context_env},
        )

    def prepare_context(self, context, directory, instructions, snapshot):
        home = directory / 'kiro'
        settings = context.read_jsonc(context.HOME / '.kiro/settings/cli.json')
        settings['chat.disableInheritingDefaultResources'] = True
        context._write(home / 'settings/cli.json', settings)
        context._write(home / 'agents/fernando.json', {
            'name': 'fernando', 'description': 'Fernando chat context', 'tools': ['*'],
            'includeMcpJson': False, 'mcpServers': snapshot['servers'],
            'resources': ['file://' + str(instructions)],
        })
        sessions = home / 'sessions'
        if not sessions.is_symlink() and not sessions.exists():
            sessions.symlink_to(context.HOME / '.kiro/sessions', target_is_directory=True)
        return {'KIRO_HOME': str(home)}, ['--agent', 'fernando']

    def global_mcp(self, context):
        path = context.HOME / '.kiro/settings/mcp.json'
        return path, context.read_jsonc(path), 'mcpServers'

    def import_servers(self, context):
        result = {}
        for path in (context.HOME / '.kiro/settings/mcp.json', context.ROOT / '.kiro/settings/mcp.json'):
            for name, definition in context.read_jsonc(path).get('mcpServers', {}).items():
                result[name] = (definition, not definition.get('disabled', False))
        return result

    def decode_server(self, definition):
        value = copy.deepcopy(definition)
        if 'command' in value and (not isinstance(value['command'], str) or not value['command']):
            raise ValueError('Kiro MCP command must be a nonempty string')
        args = value.pop('args', [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ValueError('MCP arguments must be a string array')
        if value.get('command'):
            value['command'] = [value['command']] + args
        value.pop('disabled', None)
        return value

    def encode_server(self, definition):
        value = copy.deepcopy(definition)
        value.pop('type', None)
        command = value.get('command')
        if command:
            value['command'], value['args'] = command[0], command[1:]
        if not value.get('url') and not value.get('command'):
            raise ValueError('MCP server needs a command or URL')
        return value

    def list_models(self):
        result = subprocess.run(
            [self.executable, 'chat', '--list-models', '--format', 'json'],
            capture_output=True, text=True, timeout=15, check=True,
        )
        data = json.loads(result.stdout)
        from src.services.context_templates import _write
        _write(ROOT / 'data/available_models.json', data)
        return data

    def normalize_models(self, result):
        return {'models': [{'id': model['model_id'], 'name': model.get('model_name', model['model_id'])}
                           for model in result['models']]}

    def can_resume(self, native_id):
        return Path(kiro_sessions.SESSIONS_DIR, f'{native_id}.json').exists()

    def prepare_resume(self, session, native_id):
        kiro_sessions.repair_interrupted_restart(native_id)

    def session_parameters(self, session, native_id=None):
        if native_id is not None:
            Path(kiro_sessions.SESSIONS_DIR, f'{native_id}.lock').unlink(missing_ok=True)
        return super().session_parameters(session, native_id)

    def handle_notification(self, session, method, params):
        if method not in ('_kiro.dev/mcp/server_init_failure', '_kiro.dev/mcp/server_initialized'):
            return False
        failed = method.endswith('server_init_failure')
        session.mcp_startup.update(
            session.mcp_startup.generation, params.get('serverName'),
            'failed' if failed else 'connected',
            (params.get('error') or 'Backend reported an MCP startup failure.') if failed else '',
        )
        return True

    def fork_session(self, session, history=None, boundary=None, turn_index=None):
        result = session.execute_command('rewind')
        if not result or not result.get('success'):
            return None
        turns = result.get('data', {}).get('turns', [])
        if not turns:
            return None
        if turn_index is None:
            target = turns[0]
        else:
            users = [turn for turn in reversed(turns)
                     if not turn.get('label', '').startswith('[CONTINUATION]')]
            index = turn_index - 2
            if index < 0 or index >= len(users):
                return None
            target = users[index]
        result = session.execute_command('rewind', {'value': str(target.get('logIndex'))})
        if not result or not result.get('success'):
            return None
        return result.get('data', {}).get('sessionId')

    def execute_command(self, session, command, args=None):
        return session._request('_kiro.dev/commands/execute', {
            'sessionId': session.acp_session_id,
            'command': {'command': command.lstrip('/'), 'args': args or {}},
        }, timeout=30, return_error=True)
