import tempfile
import threading
import unittest
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from src.services import acp
from src.services import chat_history


class RestartContinuationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(acp, 'RESTART_SESSIONS_FILE', str(Path(self.directory.name) / 'restart.json'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = acp.ACPManager.__new__(acp.ACPManager)
        self.manager._lock = threading.Lock()
        self.manager.sessions = {}
        for method in ['_save', '_save_pid_map', '_broadcast_sessions_list', '_wire_session_status_callback', '_recover_orphans']:
            setattr(self.manager, method, Mock())

    def test_checkpoint_only_working_agents_and_preserves_pending_recovery(self):
        self.manager.sessions = {name: Mock(get_status=Mock(return_value=status)) for name, status in [('busy', 'working'), ('idle', 'idle'), ('unread', 'unread'), ('recovering', 'idle')]}
        acp._write_restart_sessions(['recovering', 'deleted'])
        self.manager.checkpoint_restart()
        self.assertEqual(acp._restart_sessions(), ['busy', 'recovering'])
        self.manager._save.assert_called_once()

    def test_restoration_uses_custom_continuation_without_duplicate_generic(self):
        saved = {name: {'acp_id': name, 'backend': 'opencode', 'loaded': name != 'sleeping'} for name in ['requester', 'other', 'idle', 'sleeping']}
        acp._write_restart_sessions(['requester', 'other'])
        continuation = {'session_id': 'requester', 'message': 'Specific task context'}
        with patch.object(acp, '_pop_continuation', return_value=continuation), patch.object(acp, '_load_sessions_map', return_value=saved), patch.object(acp, 'ACPSession') as session_class, patch.object(acp.threading, 'Thread') as thread:
            self.manager.restore_sessions(lambda sid: Mock())
            calls = {call.kwargs['args'][0]: call.kwargs['args'][3] for call in thread.call_args_list}
            self.assertEqual(calls['requester'], continuation)
            self.assertEqual(calls['other'], {'session_id': 'other', 'message': 'Service restarted, continue what you were doing.'})
            self.assertNotIn('sleeping', calls)
            self.assertTrue(all(call.kwargs['kwargs']['recover_interrupted'] for call in thread.call_args_list))
            self.assertNotEqual(calls['idle']['session_id'], 'idle')
            session_class.return_value._load_history.assert_called_once()

    def test_load_delivers_once_and_failed_load_keeps_pending(self):
        acp._write_restart_sessions(['busy'])
        session = Mock(history=[])
        session.load.side_effect = RuntimeError('fixture load failure')
        with self.assertLogs(acp.logger, level='ERROR'):
            self.manager._load_existing('busy', session, 'existing-id')
        session.send_continuation.assert_not_called()
        self.assertEqual(acp._restart_sessions(), ['busy'])
        session.load.side_effect = None
        self.manager._load_existing('busy', session, 'existing-id')
        session.send_continuation.assert_called_once_with('Service restarted, continue what you were doing.')
        self.assertEqual(acp._restart_sessions(), [])
        self.manager._load_existing('busy', session, 'existing-id')
        session.send_continuation.assert_called_once()

    def test_explicit_continuation_overrides_pending_generic(self):
        acp._write_restart_sessions(['busy'])
        session = Mock(history=[])
        self.manager._load_existing('busy', session, 'existing-id', {'session_id': 'busy', 'message': 'Specific task context'})
        session.send_continuation.assert_called_once_with('Specific task context')
        self.assertEqual(acp._restart_sessions(), [])

    def test_initiator_without_custom_message_never_gets_generic(self):
        acp._write_restart_sessions(['caller', 'other'])
        caller = Mock(history=[])
        other = Mock(history=[])
        self.manager._load_existing('caller', caller, 'caller-id', {'session_id': 'caller', 'message': None})
        caller.send_continuation.assert_not_called()
        self.manager._load_existing('other', other, 'other-id')
        other.send_continuation.assert_called_once_with('Service restarted, continue what you were doing.')
        self.assertEqual(acp._restart_sessions(), [])

    def test_custom_message_is_not_delivered_to_other_sessions(self):
        other = Mock(history=[])
        self.manager._load_existing('other', other, 'other-id', {'session_id': 'caller', 'message': 'Caller-only next steps'})
        other.send_continuation.assert_not_called()

    def test_abrupt_kill_recovers_unfinished_turn_without_shutdown_checkpoint(self):
        root = Path(self.directory.name)
        history = root / 'history'
        history.mkdir()
        code = '''
import os, signal, sys
from pathlib import Path
from src.services import acp, chat_history
root = Path(sys.argv[1])
chat_history.ROOT = root
chat_history.HISTORY_DIR = root / 'history'
acp.HISTORY_DIR = str(root / 'history')
session = acp.ACPSession('deadbeef', backend='opencode')
session.history = [
    {'type': 'user_prompt', 'text': 'Finish the task'},
    {'method': 'session/update', 'params': {'update': {'sessionUpdate': 'agent_thought_chunk', 'content': {'type': 'text', 'text': 'Still working'}}}},
]
session._save_history()
os.kill(os.getpid(), signal.SIGKILL)
'''
        result = subprocess.run([sys.executable, '-c', code, str(root)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr.decode())
        self.assertEqual(acp._restart_sessions(), [])
        with (
            patch.object(chat_history, 'ROOT', root),
            patch.object(chat_history, 'HISTORY_DIR', history),
            patch.object(acp, 'HISTORY_DIR', str(history)),
            patch.object(acp.ACPSession, '_index_rag_background'),
        ):
            session = acp.ACPSession('deadbeef', backend='opencode', on_event=Mock())
            session.acp_session_id = 'same-native-conversation'
            session.load = lambda native_id: session._load_history()
            session._send = Mock()
            self.manager._load_existing(session.id, session, session.acp_session_id, recover_interrupted=True)
            sent = session._send.call_args.args[0]
            self.assertEqual(sent['method'], 'session/prompt')
            self.assertIn('runtime restarted while this turn was unfinished', sent['params']['prompt'][0]['text'])
            self.assertEqual(session.get_status(), 'working')
            statuses = [call.args[1]['type'] for call in session.on_event.call_args_list]
            self.assertIn('continuation', statuses)
            self.assertIn('session_working', statuses)
            self.assertNotIn('session_ready', statuses)
            session._dispatch({'id': sent['id'], 'result': {'stopReason': 'end_turn'}})
            session._send.reset_mock()
            self.manager._load_existing(session.id, session, session.acp_session_id, recover_interrupted=True)
            session._send.assert_not_called()
            self.assertEqual(session.on_event.call_args.args[1]['type'], 'session_ready')

    def test_completed_failed_cancelled_and_queued_turns_stay_stopped(self):
        session = acp.ACPSession('deadbeef')
        prompt = {'type': 'user_prompt', 'text': 'work'}
        for history in (
            [],
            [prompt, {'result': {'stopReason': 'end_turn'}}],
            [prompt, {'type': 'acp_error', 'error': 'failed'}],
            [prompt, {'type': 'prompt_cancelled'}],
            [prompt, {'result': {'stopReason': 'end_turn'}}, {'type': 'user_message_queued', 'text': 'not submitted'}],
        ):
            with self.subTest(history=history):
                session.history = history
                self.assertFalse(session.has_unfinished_turn())
        session.history = [prompt, {'result': 'ordinary non-RPC content'}]
        self.assertTrue(session.has_unfinished_turn())

    def test_explicit_cancel_records_intent_before_contacting_backend(self):
        session = acp.ACPSession('deadbeef')
        session.acp_session_id = 'native'
        session._is_prompting = True
        session.history = [{'type': 'user_prompt', 'text': 'work'}]
        with patch.object(session, '_save_history') as save, patch.object(session, '_send') as send:
            send.side_effect = lambda message: self.assertFalse(session.has_unfinished_turn())
            session.cancel()
        save.assert_called_once()
        self.assertEqual(session.history[-1]['type'], 'prompt_cancelled')


if __name__ == '__main__':
    unittest.main()
