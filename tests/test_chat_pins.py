import ast
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.services import acp


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(acp, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(acp, 'SESSIONS_FILE', str(tmp_path / 'chat_sessions.json'))
    monkeypatch.setattr(acp, 'ARCHIVED_FILE', str(tmp_path / 'chat_sessions_archived.json'))
    result = acp.ACPManager.__new__(acp.ACPManager)
    result._lock = threading.Lock()
    result.sessions = {}
    result._recover_orphans = Mock()
    result._wire_session_status_callback = Mock()
    for session_id in ['0123abcd', 'abcd0123']:
        session = acp.ACPSession(session_id)
        session.acp_session_id = 'backend-session'
        session.stop = Mock()
        result.sessions[session_id] = session
    return result


def test_pins_are_part_of_existing_chat_metadata(manager):
    assert manager.message_pins('0123abcd') == {'pins': []}
    assert manager.message_pins('0123abcd', 'user:1:0', True) == {'pins': ['user:1:0']}
    manager.message_pins('0123abcd', 'assistant:1:0', True)
    record = acp._load_sessions_map()['0123abcd']
    assert record['pinned_messages'] == ['assistant:1:0', 'user:1:0']
    assert manager.message_pins('abcd0123') == {'pins': []}
    assert 'pinned_messages' not in acp._load_sessions_map()['abcd0123']
    assert set(Path(acp.DATA_DIR).iterdir()) == {Path(acp.SESSIONS_FILE)}
    manager.message_pins('0123abcd', 'user:1:0', False)
    manager.message_pins('0123abcd', 'assistant:1:0', False)
    assert 'pinned_messages' not in acp._load_sessions_map()['0123abcd']


def test_archive_restore_and_archived_pin_changes(manager, monkeypatch):
    manager.message_pins('0123abcd', 'user:1:0', True)
    manager.archive_session('0123abcd')
    assert acp._load_archived_map()['0123abcd']['pinned_messages'] == ['user:1:0']
    assert manager.message_pins('0123abcd', 'assistant:1:0', True) == {'pins': ['assistant:1:0', 'user:1:0']}
    monkeypatch.setattr(acp.ACPSession, '_load_history', lambda self: None)
    assert manager.restore_session('0123abcd', sleeping=True)
    assert manager.message_pins('0123abcd') == {'pins': ['assistant:1:0', 'user:1:0']}
    assert acp._load_sessions_map()['0123abcd']['pinned_messages'] == ['assistant:1:0', 'user:1:0']


def test_restart_restores_pins(manager, monkeypatch):
    manager.message_pins('0123abcd', 'user:1:0', True)
    manager.sessions.clear()
    monkeypatch.setattr(acp, '_pop_continuation', lambda: None)
    monkeypatch.setattr(acp.ACPSession, '_load_history', lambda self: None)
    manager.restore_sessions(lambda sid: None)
    assert manager.message_pins('0123abcd') == {'pins': ['user:1:0']}


def test_concurrent_updates_are_not_lost(manager):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda turn: manager.message_pins('0123abcd', f'user:{turn}:0', True), range(20)))
    assert len(acp._load_sessions_map()['0123abcd']['pinned_messages']) == 20


@pytest.mark.parametrize('session_id', ['../config', '/tmp/test', None, 'a' * 100])
def test_rejects_invalid_session_paths(manager, session_id):
    assert 'error' in manager.message_pins(session_id, 'user:1:0', True)


@pytest.mark.parametrize('key,value', [('other:1:0', True), ('user:1:0', 'true'), ('user:1:0', 1), ('<script>', True)])
def test_rejects_invalid_pins(manager, key, value):
    assert 'error' in manager.message_pins('0123abcd', key, value)


def test_websocket_csrf_and_subscriber_isolation(manager):
    source = Path(__file__).resolve().parents[1] / 'src/routes/websocket.py'
    functions = [node for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.FunctionDef) and node.name in ('acp_get_message_pins', 'acp_set_message_pin')]
    for function in functions:
        function.decorator_list = []
    emitted = []
    namespace = {
        'acp_manager': manager,
        'validate_csrf': lambda data: data.get('csrf_token') == 'valid',
        'socketio': types.SimpleNamespace(emit=lambda event, data, room: emitted.append((event, data, room))),
        'acp_subscribers': {'0123abcd': {'viewer'}, 'abcd0123': {'other-viewer'}},
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    data = {'session_id': '0123abcd', 'message_key': 'user:1:0', 'pinned': True}
    assert namespace['acp_set_message_pin'](data) == {'error': 'Invalid CSRF token'}
    assert namespace['acp_get_message_pins'](data) == {'error': 'Invalid CSRF token'}
    assert not Path(acp.SESSIONS_FILE).exists()
    data['csrf_token'] = 'valid'
    assert namespace['acp_set_message_pin'](data) == {'pins': ['user:1:0']}
    assert namespace['acp_get_message_pins'](data) == {'pins': ['user:1:0']}
    assert emitted == [('acp_message_pins', {'session_id': '0123abcd', 'pins': ['user:1:0']}, 'viewer')]
