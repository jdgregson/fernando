import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from src.services.chat_images import project_images


class ReconnectTests(unittest.TestCase):
    def subscribe(self, **changes):
        session = SimpleNamespace(history=[{'type': 'user_prompt', 'text': 'run command'}], mark_read=Mock(), is_loaded=True, ready=True, _reloading=False, _is_prompting=False, _retry_pending=False, model='test', backend='kiro', acp_session_id='native', _start_on_open=False, proc=Mock(), _last_activity=0)
        session.proc.poll.return_value = None
        for key, value in changes.items():
            setattr(session, key, value)
        manager = Mock()
        manager.get_session.return_value = session
        emitted = Mock()
        scope = {'validate_csrf': lambda data: True, 'logger': Mock(), 'request': SimpleNamespace(sid='socket'), 'acp_manager': manager, 'acp_subscribers': {}, 'acp_event_seq': {}, 'emit': emitted, 'replay_events': lambda sid, events, offset, indexed: iter(events), 'project_images': project_images, 'time': SimpleNamespace(time=lambda: 9999)}
        tree = ast.parse(Path('src/routes/websocket.py').read_text())
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'acp_subscribe')
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<handler>', 'exec'), scope)
        scope['acp_subscribe']({'session_id': 'aaaaaaaa'})
        return [call.args[1]['event'] for call in emitted.call_args_list if call.args[0] == 'acp_event']

    def test_initialized_agent_with_active_prompt_is_working(self):
        self.assertEqual(self.subscribe(_is_prompting=True), [{'type': 'session_working', 'retrying': False}])

    def test_pending_retry_is_not_ready(self):
        self.assertEqual(self.subscribe(_retry_pending=True), [{'type': 'session_working', 'retrying': True}])

    def test_silent_uninitialized_process_is_not_assumed_ready(self):
        self.assertEqual(self.subscribe(ready=False), [{'type': 'session_loading'}])

    def test_dead_process_is_not_ready(self):
        self.assertEqual(self.subscribe(is_loaded=False, proc=None, acp_session_id=None), [{'type': 'session_ended'}])

    def test_initialized_idle_process_is_ready(self):
        self.assertEqual(self.subscribe(), [{'type': 'session_ready'}])

    def test_reload_takes_precedence(self):
        self.assertEqual(self.subscribe(_reloading=True, _is_prompting=True), [{'type': 'session_loading', 'waking': True}])


if __name__ == '__main__':
    unittest.main()
