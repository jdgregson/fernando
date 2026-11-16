import unittest
from unittest.mock import Mock, patch

from src.services import acp


class ErrorRetryTests(unittest.TestCase):
    def setUp(self):
        self.session = acp.ACPSession("aaaaaaaa", on_event=Mock())
        self.session.acp_session_id = "native-session"
        self.session.ready = True
        self.session._alive = True
        self.session._save_history = Mock()
        self.session._notify_status_change = Mock()
        self.session._send = Mock()

    def fail_turn(self):
        self.session._is_prompting = True
        self.session._dispatch({"id": 42, "error": {"message": "Response failed"}})

    def test_error_is_recorded_and_broadcast_before_user_retry(self):
        self.fail_turn()
        self.assertEqual([e['type'] for e in self.session.history], ['acp_error', 'user_prompt'])
        self.assertEqual(self.session.history[-1]['text'], 'Continue')
        events = [call.args[1]['type'] for call in self.session.on_event.call_args_list]
        self.assertEqual(events, ['acp_error', 'user_prompt'])
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'], [{'type': 'text', 'text': 'Continue'}])

    def test_rolling_limit_survives_success_and_allows_expired_retry(self):
        with patch.object(acp.time, 'time', return_value=1000):
            for _ in range(4):
                self.fail_turn()
                self.session._dispatch({'id': 43, 'result': {'stopReason': 'end_turn'}})
        self.assertEqual(self.session._send.call_count, 3)
        with patch.object(acp.time, 'time', return_value=1299):
            self.fail_turn()
        self.assertEqual(self.session._send.call_count, 3)
        with patch.object(acp.time, 'time', return_value=1300):
            self.fail_turn()
        self.assertEqual(self.session._send.call_count, 4)

    def test_busy_or_unready_session_is_not_interrupted(self):
        self.session._is_prompting = True
        self.session.send_prompt('Continue', error_retry=True)
        self.session._is_prompting = False
        self.session.ready = False
        self.session.send_prompt('Continue', error_retry=True)
        self.session._send.assert_not_called()

    def test_pending_rpc_error_does_not_retry_prompt(self):
        event = Mock()
        self.session._pending[42] = {'event': event}
        self.fail_turn()
        event.set.assert_called_once()
        self.session._send.assert_not_called()

    def test_transport_error_is_visible_before_reload(self):
        with patch.object(acp.threading, 'Thread') as thread:
            self.session._dispatch({'id': 42, 'error': {'message': 'Transport closed'}})
        self.assertEqual(self.session.history[-1]['type'], 'acp_error')
        self.assertEqual(thread.call_args.kwargs['kwargs'], {'error_retry': True})
        self.session._send.assert_not_called()

    def test_policy_error_retries_only_once(self):
        self.session._dispatch({'id': 42, 'error': {'message':
            'This request was blocked by our safety systems. Reason: Potentially unintended activity.'}})
        self.assertEqual([e['type'] for e in self.session.history], ['acp_error', 'user_prompt'])
        self.assertEqual(self.session.history[-1]['text'], 'Continue')
        self.session._dispatch({'id': 43, 'error': {'message': 'blocked by our safety systems'}})
        self.assertEqual(self.session._send.call_count, 1)

    def test_native_policy_error_cancels_and_preserves_reason(self):
        from src.services.providers.opencode import OpenCodeProvider
        self.session._is_prompting = True
        line = ('level=ERROR message="stream error" session.id=native-session '
                'error.error.error.code=misalignment_policy_violation '
                'error.error.error.message="This request was blocked by our safety systems."')
        OpenCodeProvider().handle_stderr(self.session, line)
        self.assertEqual(self.session._send.call_args.args[0]['method'], 'session/cancel')
        self.assertIn('misalignment_policy_violation', self.session.history[-1]['text'])
        self.assertEqual(self.session.history[-1]['level'], 'error')
        self.session._dispatch({'id': 42, 'result': {'stopReason': 'end_turn'}})
        self.assertEqual(self.session.history[-1]['text'], 'Continue')
        OpenCodeProvider().handle_stderr(self.session, line)
        self.session._dispatch({'id': 43, 'result': {'stopReason': 'end_turn'}})
        prompts = [call for call in self.session._send.call_args_list
                   if call.args[0].get('method') == 'session/prompt']
        self.assertEqual(len(prompts), 1)

    def test_other_native_session_error_is_ignored(self):
        from src.services.providers.opencode import OpenCodeProvider
        OpenCodeProvider().handle_stderr(self.session,
            'message="stream error" session.id=other error.error.error.message="blocked by our safety systems"')
        self.assertEqual(self.session.history, [])
        self.session._send.assert_not_called()

    def test_user_cancel_suppresses_pending_policy_retry(self):
        self.session._policy_retry_pending = True
        self.session.cancel()
        self.session._dispatch({'id': 42, 'result': {'stopReason': 'end_turn'}})
        self.assertFalse(any(e.get('error_retry') for e in self.session.history))

    def test_new_user_prompt_gets_one_new_policy_retry(self):
        self.session.history = [{'type': 'user_prompt', 'error_retry': True, 'ts': 1},
                                {'type': 'user_prompt', 'text': 'New request', 'ts': 2}]
        self.session._dispatch({'id': 42, 'error': {'message': 'blocked by our safety systems'}})
        self.assertEqual(self.session.history[-1]['text'], 'Continue')

    def test_transient_native_error_is_visible_without_cancellation(self):
        from src.services.providers.opencode import OpenCodeProvider
        OpenCodeProvider().handle_stderr(self.session,
            'message="stream error" session.id=native-session error.error.error.message="Server unavailable"')
        self.assertIn('Server unavailable', self.session.history[-1]['text'])
        self.session._send.assert_not_called()
