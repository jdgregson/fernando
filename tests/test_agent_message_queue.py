import threading
import tempfile
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, request

from src.services import acp


class DeliveryContextTests(unittest.TestCase):
    def test_current_panes_are_resolved_and_disconnected_panes_removed(self):
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager._lock = threading.Lock()
        manager._pane_contexts = {}
        manager.set_pane_context('browser', {'aaaaaaaa'}, {'pane1': 'this chat', 'pane2': 'old terminal'})
        manager.set_pane_context('browser', {'aaaaaaaa'}, {'pane1': 'this chat', 'pane2': 'new terminal'})
        with patch('src.services.groups.get_all', return_value={'session_groups': {}, 'groups': []}), \
                patch.object(acp, 'count_unread_child_messages', return_value=2):
            context = manager._initial_prompt_context('aaaaaaaa')
            self.assertIn('pane2: new terminal', context)
            self.assertNotIn('old terminal', context)
            self.assertIn('subagent_messages: 2 unread', context)
            manager.set_pane_context('browser', set(), {})
            self.assertNotIn('pane2:', manager._initial_prompt_context('aaaaaaaa'))


class ChildMessageRouteTests(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        self.app = Flask(__name__)
        source = Path(__file__).resolve().parents[1] / 'src/routes/web.py'
        names = {'api_acp_child_messages': '/api/acp/child_messages',
                 'api_acp_read_child_messages': '/api/acp/child_messages/read'}
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        namespace = {'request': request, 'acp_manager': self.manager,
                     '_check_api_key': lambda: request.headers.get('X-API-Key') == 'test-key'}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
        for name, route in names.items():
            self.app.add_url_rule(route, view_func=namespace[name], methods=['POST'])
        self.client = self.app.test_client()

    def test_both_routes_require_api_key(self):
        for route in ('/api/acp/child_messages', '/api/acp/child_messages/read'):
            self.assertEqual(self.client.post(route, json={'session_id': 'child'}).status_code, 401)
        self.manager.get_session.assert_not_called()

    def test_parent_is_derived_from_lineage_and_sleeping_parent_accepts_message(self):
        parent = self.manager.get_session.return_value
        parent.ready = False
        parent.receive_child_message.return_value = 'message-id'
        with patch.object(acp, 'get_parent', return_value='actual-parent'):
            response = self.client.post('/api/acp/child_messages',
                                        json={'session_id': 'child', 'parent_session': 'other', 'message': 'hello'},
                                        headers={'X-API-Key': 'test-key'})
        self.assertEqual(response.status_code, 200)
        self.manager.get_session.assert_called_once_with('actual-parent')
        parent.receive_child_message.assert_called_once_with('child', 'hello')
        self.assertEqual(response.json['message_id'], 'message-id')

    def test_unrelated_sender_is_rejected(self):
        with patch.object(acp, 'get_parent', return_value=None):
            response = self.client.post('/api/acp/child_messages',
                                        json={'session_id': 'child', 'message': 'hello'},
                                        headers={'X-API-Key': 'test-key'})
        self.assertEqual(response.status_code, 403)
        self.manager.get_session.assert_not_called()

    def test_read_uses_session_coordinator(self):
        self.manager.get_session.return_value.read_child_messages.return_value = [{'id': 'one'}]
        response = self.client.post('/api/acp/child_messages/read', json={'session_id': 'parent'},
                                    headers={'X-API-Key': 'test-key'})
        self.assertEqual(response.json, {'messages': [{'id': 'one'}], 'count': 1})
        self.manager.get_session.assert_called_once_with('parent')


class AgentMessageQueueTests(unittest.TestCase):
    def setUp(self):
        inbox = tempfile.TemporaryDirectory()
        self.addCleanup(inbox.cleanup)
        inbox_patch = patch.object(acp, 'CHILD_MESSAGES_DIR', inbox.name)
        inbox_patch.start()
        self.addCleanup(inbox_patch.stop)
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

    def test_bulk_read_removes_only_consumed_pokes_and_persists_removals(self):
        self.session._is_prompting = True
        first = self.session.receive_child_message('child1', 'first')
        self.session.queue_user_message('user message')
        self.session.send_agent_message('parent', 'actual message')
        second = self.session.receive_child_message('child2', 'second')
        messages = self.session.read_child_messages()
        self.assertEqual([message['id'] for message in messages], [first, second])
        self.assertEqual(self.session.read_child_messages(), [])
        with patch.object(acp, 'load_history_file', return_value=list(self.session.history)):
            self.session._load_history()
        self.assertEqual([entry['text'] for entry in self.session._agent_message_queue],
                         ['user message', 'actual message'])
        self.assertEqual(len([event for event in self.session.history
                              if event['type'] == 'agent_message_removed']), 2)

    def test_same_child_new_message_after_read_keeps_its_poke(self):
        self.session._is_prompting = True
        self.session.receive_child_message('child', 'first')
        self.session.read_child_messages()
        second = self.session.receive_child_message('child', 'second')
        self.assertEqual(self.session._agent_message_queue[0]['child_message_id'], second)
        self.finish_turn()
        self.session._send.assert_called_once()
        self.assertEqual([message['id'] for message in self.session.read_child_messages()], [second])

    def test_delayed_stale_poke_is_skipped_without_blocking_real_message(self):
        self.session._is_prompting = True
        message_id = self.session.receive_child_message('child', 'first')
        self.session.read_child_messages()
        self.session.send_agent_message('child', 'stale poke', child_message_id=message_id)
        self.session.send_agent_message('parent', 'real message')
        self.finish_turn()
        self.session._send.assert_called_once()
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[AGENT MESSAGE from parent] real message')

    def test_restart_discards_stale_pokes_even_if_removal_was_not_saved(self):
        self.session._is_prompting = True
        self.session.receive_child_message('child', 'first')
        queued_history = list(self.session.history)
        self.session.read_child_messages()
        with patch.object(acp, 'load_history_file', return_value=queued_history):
            self.session._load_history()
        self.finish_turn()
        self.session._send.assert_not_called()
        self.assertEqual(self.session._agent_message_queue, [])

    def test_sleeping_parent_keeps_inbox_and_poke_until_ready(self):
        self.session.ready = False
        message_id = self.session.receive_child_message('child', 'first')
        self.session._send.assert_not_called()
        self.session.ready = True
        self.session._drain_agent_messages()
        self.session._send.assert_called_once()
        self.assertEqual(self.session.read_child_messages()[0]['id'], message_id)

    def test_concurrent_read_and_arrival_do_not_lose_message_or_leave_stale_poke(self):
        self.session._is_prompting = True
        self.session.receive_child_message('child', 'first')
        barrier = threading.Barrier(3)
        consumed = []

        def read():
            barrier.wait()
            consumed.extend(self.session.read_child_messages())

        def send():
            barrier.wait()
            self.session.receive_child_message('child', 'second')

        threads = [threading.Thread(target=read), threading.Thread(target=send)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        unread = acp.get_unread_child_messages(self.session.id, mark_read=False)
        self.assertEqual({message['message'] for message in consumed + unread}, {'first', 'second'})
        self.assertEqual({entry['child_message_id'] for entry in self.session._agent_message_queue},
                         {message['id'] for message in unread})

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
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'], 'Continue')
        self.assertEqual(len(self.session._agent_message_queue), 1)
        self.finish_turn()
        self.assertEqual(self.session._send.call_args.args[0]['params']['prompt'][0]['text'],
                         '[AGENT MESSAGE from child] pending')
