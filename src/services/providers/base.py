from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import threading


ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Capabilities:
    fork: bool = False
    fork_at_turn: bool = False
    model_switch: bool = True
    effort: bool = False
    cost: bool = False
    quota: bool = False
    slash_commands: bool = False


@dataclass(frozen=True)
class Launch:
    command: list
    environment: dict


class Provider(ABC):
    initialize_timeout = 15
    capabilities = Capabilities()
    icon_path = 'M2 3h12v8H6l-4 3V3z'
    context_paths = ()
    context_flags = ()

    def metadata(self):
        return {
            'id': self.id, 'name': self.name,
            'capabilities': asdict(self.capabilities),
            'model_setting': self.model_setting,
            'enabled_setting': self.enabled_setting,
            'default_model': self.default_model,
            'icon_path': self.icon_path,
        }

    def configured_model(self):
        from src.services.settings import get
        return get(self.model_setting) or self.default_model

    def validate_start(self):
        pass

    def normalize_models(self, result):
        return {'models': [{'id': model, 'name': model} for model in result['models']]}

    @abstractmethod
    def launch(self, session, context_env, context_args):
        raise NotImplementedError

    @abstractmethod
    def prepare_context(self, context, directory, instructions, snapshot):
        raise NotImplementedError

    @abstractmethod
    def list_models(self):
        raise NotImplementedError

    @abstractmethod
    def decode_server(self, definition):
        raise NotImplementedError

    @abstractmethod
    def encode_server(self, definition):
        raise NotImplementedError

    @abstractmethod
    def global_mcp(self, context):
        raise NotImplementedError

    @abstractmethod
    def import_servers(self, context):
        raise NotImplementedError

    def clear_inherited_context(self, env, runtime):
        managed = False
        for key in self.context_paths:
            value = env.get(key)
            if value and Path(value).is_relative_to(runtime):
                del env[key]
                managed = True
        if managed:
            for key in self.context_flags:
                env.pop(key, None)

    def prepare_resume(self, session, native_id):
        pass

    def session_parameters(self, session, native_id=None):
        params = {'cwd': os.path.expanduser('~/fernando'), 'mcpServers': []}
        if native_id is not None:
            params['sessionId'] = native_id
            if session.model:
                params['model'] = session.model
        return params

    def can_resume(self, native_id):
        return bool(native_id)

    def matches_native_id(self, native_id):
        return False

    def check_mcp_startup(self, session):
        timer = threading.Timer(10, session.mcp_startup.unconfirmed, args=(
            session.mcp_startup.generation,
            'Backend completed chat loading without confirming this MCP server started.',
        ))
        timer.daemon = True
        timer.start()

    def handle_notification(self, session, method, params):
        return False

    def fork_session(self, session, history=None, boundary=None, turn_index=None):
        raise ValueError(f'{self.name} does not support conversation forks')

    def execute_command(self, session, command, args=None):
        raise ValueError(f'{self.name} does not support ACP slash commands')


def configured_environment():
    env = os.environ.copy()
    config = ROOT / 'config'
    if config.exists():
        for line in config.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                env[key] = value
    return env
