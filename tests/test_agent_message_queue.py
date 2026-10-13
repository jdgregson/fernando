import threading
import unittest
from unittest.mock import Mock, patch

from src.services import acp


class DeliveryContextTests(unittest.TestCase):
    def test_current_panes_and_reward_are_resolved_and_disconnected_panes_removed(self):
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager._lock = threading.Lock()
        manager._pane_contexts = {}
        manager.set_pane_context('browser', {'aaaaaaaa'}, {'pane1': 'this chat', 'pane2': 'old terminal'})
        manager.set_pane_context('browser', {'aaaaaaaa'}, {'pane1': 'this chat', 'pane2': 'new terminal'})
        with patch('src.services.groups.get_all', return_value={'session_groups': {}, 'groups': []}), \
                patch('src.services.rewards.get_balance', return_value=42), \
                patch.object(acp, 'count_unread_child_messages', return_value=2):
            context = manager._initial_prompt_context('aaaaaaaa')
            self.assertIn('pane2: new terminal', context)
            self.assertNotIn('old terminal', context)
            self.assertIn('reward balance: 42', context)
            self.assertIn('subagent_messages: 2 unread', context)
            manager.set_pane_context('browser', set(), {})
            self.assertNotIn('pane2:', manager._initial_prompt_context('aaaaaaaa'))


class AgentMessageQueueTests(unittest.TestCase):
    def setUp(self):
        self.session = acp.ACPSession('aaaaaaaa', on_event=Mock())
        self.session.acp_session_id = 'native-session'
        self.session.ready = True
        self.session._alive = True
        self.session._save_history = Mock()
        self.session._notify_status_change = Mock()
        self.session._send = Mock()
        context_patch = patch.object(acp.acp_manager, '_initial_prompt_context', return_value='[Pane context: current]')
        self.context = context_patch.start()
        self.addCleanup(context_patch.stop)

    def finish_turn(self):
        self.session._dispatch({'id': 42, 'result': {'stopReason': 'end_turn'}})

    def test_busy_messages_wait_and_deliver_one_per_turn_in_order(self):
        self.session._is_prompting = True
        self.assertFalse(self.session.send_agent_message('child', 'first'))
        self.assertFalse(self.session.send_agent_message('parent', 'second'))
        self.session._send.assert_not_called()
        self.finish_turn()
        self.assertEqual(self.session._send.call_count, 1)
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[AGENT MESSAGE from child] first')
        self.assertTrue(self.session._is_prompting)
        self.finish_turn()
        self.assertEqual(self.session._send.call_count, 2)
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[AGENT MESSAGE from parent] second')
        self.assertFalse(any(e.get('type') == 'user_prompt' for e in self.session.history))

    def test_idle_message_is_delivered_immediately(self):
        self.assertTrue(self.session.send_agent_message('child', 'hello'))
        self.session._send.assert_called_once()
        self.assertEqual(self.session._agent_message_queue, [])

    def test_user_messages_merge_at_original_queue_position_without_cancelling(self):
        self.session._is_prompting = True
        self.session.cancel = Mock()
        self.assertFalse(self.session.queue_user_message('user first'))
        self.assertFalse(self.session.send_agent_message('child', 'agent second'))
        self.assertFalse(self.session.queue_user_message('user third'))
        self.session._send.assert_not_called()
        for expected in ('[Pane context: current]\nuser first\n\nuser third', '[AGENT MESSAGE from child] agent second'):
            self.finish_turn()
            self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'], expected)
        self.session.cancel.assert_not_called()
        delivered = [e for e in self.session.history if e.get('type') == 'user_prompt']
        self.assertEqual(len(delivered), 1)
        self.assertTrue(all(e.get('queue_id') for e in delivered))

    def test_idle_user_message_never_enters_queue(self):
        self.assertTrue(self.session.queue_user_message('hello'))
        self.session._send.assert_called_once()
        self.assertEqual(self.session._agent_message_queue, [])
        self.assertEqual([e['type'] for e in self.session.history], ['user_prompt'])
        self.assertNotIn('queue_id', self.session.history[0])
        self.assertEqual(self.session.history[0]['text'], '[Pane context: current]\nhello')
        self.assertFalse(any(call.args[1].get('type') == 'user_message_queued'
                             for call in self.session.on_event.call_args_list))

    def test_merged_user_message_restores_and_delivers_once(self):
        self.session._is_prompting = True
        for text in ('first', 'second\nwith another line', 'third'):
            self.assertFalse(self.session.queue_user_message(text))
        updates = [e for e in self.session.history if e['type'] == 'user_message_queued']
        self.assertEqual(len({e['id'] for e in updates}), 1)
        self.assertEqual(updates[0]['text'], 'first')
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.assertEqual(len(self.session._agent_message_queue), 1)
        self.finish_turn()
        self.session._send.assert_called_once()
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[Pane context: current]\nfirst\n\nsecond\nwith another line\n\nthird')
        self.finish_turn()
        self.session._send.assert_called_once()

    def test_merged_user_message_deletes_as_one_message(self):
        self.session._is_prompting = True
        self.session.queue_user_message('first')
        message_id = self.session.history[-1]['id']
        self.session.queue_user_message('second')
        self.assertTrue(self.session.delete_queued_agent_message(message_id))
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.finish_turn()
        self.session._send.assert_not_called()

    def test_context_is_resolved_at_delivery_not_enqueue(self):
        self.session._is_prompting = True
        self.session.queue_user_message('clean message')
        self.context.assert_not_called()
        self.assertEqual(self.session.history[-1]['text'], 'clean message')
        self.context.return_value = '[Pane context: changed while queued]'
        self.finish_turn()
        self.context.assert_called_once_with(self.session.id)
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[Pane context: changed while queued]\nclean message')

    def test_user_queue_restore_and_delete(self):
        self.assertTrue(self.session.queue_user_message('delivered'))
        self.session.queue_user_message('deleted')
        self.session.delete_queued_agent_message(self.session.history[-1]['id'])
        self.session.queue_user_message('pending')
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.assertEqual([e['text'] for e in self.session._agent_message_queue], ['pending'])

    def test_take_for_edit_returns_latest_text_and_prevents_delivery(self):
        self.session._is_prompting = True
        self.session.queue_user_message('first')
        message_id = self.session.history[-1]['id']
        self.session.queue_user_message('second')
        self.assertEqual(self.session.take_queued_message(message_id)['text'], 'first\n\nsecond')
        self.assertIsNone(self.session.take_queued_message(message_id))
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.finish_turn()
        self.session._send.assert_not_called()

    def test_take_cannot_restore_already_delivered_message(self):
        self.session._is_prompting = True
        self.session.queue_user_message('first')
        message_id = self.session.history[-1]['id']
        self.finish_turn()
        self.assertIsNone(self.session.take_queued_message(message_id))

    def test_deleted_message_is_not_delivered_and_cannot_delete_delivered_message(self):
        self.session._is_prompting = True
        self.session.send_agent_message('child', 'delete me')
        message_id = self.session.history[-1]['id']
        self.assertTrue(self.session.delete_queued_agent_message(message_id))
        self.assertFalse(self.session.delete_queued_agent_message(message_id))
        self.finish_turn()
        self.session._send.assert_not_called()
        self.session.send_agent_message('child', 'deliver me')
        self.assertFalse(self.session.delete_queued_agent_message(self.session.history[-1]['id']))

    def test_restore_retains_only_pending_messages(self):
        self.session.send_agent_message('child', 'delivered')
        self.session.send_agent_message('child', 'deleted')
        self.session.delete_queued_agent_message(self.session.history[-1]['id'])
        self.session.send_agent_message('child', 'pending')
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.assertEqual([e['text'] for e in self.session._agent_message_queue], ['pending'])

    def test_concurrent_idle_senders_claim_only_one_turn(self):
        barrier = threading.Barrier(3)

        def send(text):
            barrier.wait()
            self.session.send_agent_message('child', text)

        threads = [threading.Thread(target=send, args=(str(i),)) for i in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.session._send.assert_called_once()
        self.assertEqual(len(self.session._agent_message_queue), 1)

    def test_fork_does_not_inherit_pending_delivery(self):
        self.session._is_prompting = True
        self.session.send_agent_message('child', 'original recipient only')
        self.session.id = 'bbbbbbbb'
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.assertEqual(self.session._agent_message_queue, [])

    def test_restored_queue_waits_until_ready(self):
        self.session._is_prompting = True
        self.session.send_agent_message('child', 'pending')
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.session._is_prompting = False
        self.session.ready = False
        self.session._drain_agent_messages()
        self.session._send.assert_not_called()
        self.session.ready = True
        self.session._drain_agent_messages()
        self.session._send.assert_called_once()

    def test_completion_is_broadcast_before_next_prompt(self):
        self.session._is_prompting = True
        self.session.send_agent_message('child', 'hello')
        self.session.on_event.reset_mock()
        self.finish_turn()
        events = [call.args[1] for call in self.session.on_event.call_args_list]
        self.assertEqual(events[0]['result']['stopReason'], 'end_turn')
        self.assertEqual(events[1]['type'], 'agent_message')

    def test_arrival_during_completion_waits_for_completion_broadcast(self):
        self.session._is_prompting = True

        def on_event(session_id, event):
            if event.get('result', {}).get('stopReason'):
                self.assertFalse(self.session.send_agent_message('child', 'arrived at end'))
                self.session._send.assert_not_called()

        self.session.on_event = on_event
        self.finish_turn()
        self.session._send.assert_called_once()

    def test_genuine_error_keeps_existing_continue_behavior_then_delivers_queue(self):
        self.session._is_prompting = True
        self.session.send_agent_message('child', 'pending')
        self.session._dispatch({'id': 42, 'error': {'message': 'Response failed'}})
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'], 'Coninue')
        self.assertEqual(len(self.session._agent_message_queue), 1)
        self.finish_turn()
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[AGENT MESSAGE from child] pending')
