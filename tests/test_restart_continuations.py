import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.services import acp


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


if __name__ == '__main__':
    unittest.main()
