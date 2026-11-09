import asyncio
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask

from src.services import acp, chat_resumes
from src.services.chat_resumes import ChatResumeScheduler


class ChatResumeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'resumes.json'
        self.scheduler = ChatResumeScheduler(self.path)
        self.manager = acp.ACPManager.__new__(acp.ACPManager)
        self.manager._lock = threading.Lock()
        self.manager._broadcast_sessions_list = Mock()
        self.manager._save = Mock()
        self.session = acp.ACPSession('aaaaaaaa')
        self.session.acp_session_id = 'original-conversation'
        self.session._save_history = Mock()
        self.session._notify_status_change = Mock()
        self.session._send = Mock()
        self.session.on_event = Mock()
        self.manager.sessions = {self.session.id: self.session}
        self.scheduler.manager = self.manager
        self.ready()
        self.row = self.scheduler.create(self.session.id, 900, 'Check deployment', 'Read deployment status and verify result')
        self.due = self.row['resume_at']

    def ready(self):
        self.session.proc = Mock()
        self.session.proc.poll.return_value = None
        self.session.ready = True
        self.session._alive = True
        self.session._reloading = False

    def status(self):
        return self.scheduler.rows[self.row['id']]['status']

    def test_deadline_and_same_conversation_once(self):
        self.scheduler.tick(self.due - 1)
        self.session._send.assert_not_called()
        self.scheduler.tick(self.due)
        self.scheduler.tick(self.due + 1)
        self.assertEqual(self.status(), 'resumed')
        self.session._send.assert_called_once()
        args = self.session._send.call_args.args[0]['params']
        self.assertEqual(args['sessionId'], 'original-conversation')
        self.assertEqual(args['prompt'][0]['text'], '[CONTINUATION] ' + self.row['continuation'])
        self.assertEqual(self.session._send.call_args.kwargs, {'strict': True})

    def test_busy_waits_without_deadline_or_interrupting(self):
        self.session._is_prompting = True
        self.scheduler.tick(self.due)
        self.scheduler.tick(self.due + 3600)
        self.assertEqual(self.status(), 'queued')
        self.session._send.assert_not_called()
        self.session._is_prompting = False
        self.scheduler.tick(self.due + 3601)
        self.assertEqual(self.status(), 'resumed')

    def test_pending_user_message_has_priority(self):
        self.session._agent_message_queue.append({'type': 'user_message_queued'})
        self.scheduler.tick(self.due)
        self.assertEqual(self.status(), 'queued')
        self.session._send.assert_not_called()

    def test_sleep_wakes_before_delivery(self):
        self.session.proc = None
        with patch.object(self.manager, 'reload_session') as wake:
            self.scheduler.tick(self.due)
            wake.assert_called_once_with(self.session.id)
        self.assertEqual(self.status(), 'waking')
        self.session._send.assert_not_called()
        self.ready()
        self.scheduler.tick(self.due + 1)
        self.assertEqual(self.status(), 'resumed')

    def test_cancel_while_waking_survives_restart(self):
        self.session.proc = None
        with patch.object(self.manager, 'reload_session'):
            self.scheduler.tick(self.due)
        self.scheduler.change(self.session.id, self.row['id'], 'cancel')
        self.ready()
        restored = ChatResumeScheduler(self.path)
        restored.manager = self.manager
        restored.tick(self.due + 1)
        self.session._send.assert_not_called()
        self.assertEqual(restored.rows[self.row['id']]['status'], 'canceled')

    def test_overdue_resume_survives_restart(self):
        restored = ChatResumeScheduler(self.path)
        with patch.object(chat_resumes.threading, 'Thread'):
            restored.start(self.manager)
        restored.tick(self.due + 100)
        self.assertEqual(restored.rows[self.row['id']]['status'], 'resumed')
        self.session._send.assert_called_once()

    def test_resume_now_and_scope(self):
        with self.assertRaises(ValueError):
            self.scheduler.change('bbbbbbbb', self.row['id'], 'cancel')
        self.scheduler.change(self.session.id, self.row['id'], 'resume_now')
        self.scheduler.tick()
        self.assertEqual(self.status(), 'resumed')

    def test_transport_failure_is_visible_and_not_retried(self):
        self.session._send.side_effect = BrokenPipeError('broken pipe')
        with self.assertLogs(chat_resumes.logger, level='ERROR'):
            self.scheduler.tick(self.due)
        self.assertEqual(self.status(), 'failed')
        self.assertFalse(self.session._is_prompting)
        self.scheduler.tick(self.due + 1)
        self.session._send.assert_called_once()
        self.assertEqual(self.session.history[-1]['schedule']['status'], 'failed')

    def test_wake_timeout(self):
        self.session.ready = False
        self.scheduler.tick(self.due)
        with self.assertLogs(chat_resumes.logger, level='ERROR'):
            self.scheduler.tick(self.due + 120)
        self.assertEqual(self.status(), 'failed')

    def test_delivery_receipt_prevents_duplicate_after_interruption(self):
        self.manager.deliver_chat_resume(self.row)
        self.session._is_prompting = False
        self.scheduler.tick(self.due)
        self.assertEqual(self.status(), 'resumed')
        self.session._send.assert_called_once()

    def test_interrupted_delivery_without_receipt_fails_explicitly(self):
        self.session.history.append({'type': 'continuation', 'schedule_id': self.row['id']})
        with self.assertLogs(chat_resumes.logger, level='ERROR'):
            self.scheduler.tick(self.due)
        self.assertEqual(self.status(), 'failed')
        self.session._send.assert_not_called()

    def test_append_only_history_persists_status_changes(self):
        with patch.object(acp, 'HISTORY_DIR', str(self.path.parent)):
            self.session._save_history = acp.ACPSession._save_history.__get__(self.session)
            self.session._save_history()
            self.scheduler.change(self.session.id, self.row['id'], 'cancel')
            events = [json.loads(line) for line in Path(self.session._history_path()).read_text().splitlines()]
        self.assertEqual([event['schedule']['status'] for event in events], ['scheduled', 'canceled'])
        self.assertEqual(events[0]['schedule']['id'], events[1]['schedule']['id'])

    def test_validation_and_duplicate(self):
        for delay in (True, 0, -1, 31536001, '900'):
            with self.assertRaises(ValueError):
                self.scheduler.create(self.session.id, delay, 'check', 'check')
        with self.assertRaises(ValueError):
            self.scheduler.create(self.session.id, 1, 'check', 'check')
        for schedule_id in (None, [], {}):
            with self.assertRaises(ValueError):
                self.scheduler.change(self.session.id, schedule_id, 'cancel')

    def test_destroy_cancels_before_stop(self):
        self.session.stop = Mock(side_effect=lambda: self.assertEqual(self.status(), 'canceled'))
        with patch.object(chat_resumes, 'chat_resume_scheduler', self.scheduler):
            self.manager.destroy_session(self.session.id, delete_history=False)
        self.session.stop.assert_called_once()

    def test_route_requires_auth_and_validates_input(self):
        from src.routes import web
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        client = app.test_client()
        with patch.object(web, '_check_api_key', return_value=False):
            self.assertEqual(client.post('/api/acp/chat_resume', json={}).status_code, 401)
        with patch.object(web, '_check_api_key', return_value=True), patch.object(chat_resumes, 'chat_resume_scheduler', self.scheduler):
            self.assertEqual(client.post('/api/acp/chat_resume', json=[]).status_code, 400)
            self.assertEqual(client.post('/api/acp/chat_resume', json={'session_id': self.session.id, 'action': 'cancel', 'schedule_id': []}).status_code, 400)
            result = client.post('/api/acp/chat_resume', json={'session_id': self.session.id, 'action': 'cancel', 'schedule_id': self.row['id']})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['schedule']['status'], 'canceled')


class ChatResumeToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        with patch.object(sys, 'path', [str(root / 'mcp_servers'), *sys.path]):
            spec = importlib.util.spec_from_file_location('resume_chat_mcp', root / 'mcp_servers' / 'chat_mcp.py')
            cls.tools = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.tools)

    def test_schemas_are_chat_scoped(self):
        tools = {tool.name: tool for tool in asyncio.run(self.tools.list_tools())}
        for name in ('schedule_resume', 'cancel_resume'):
            self.assertNotIn('session_id', tools[name].inputSchema['properties'])
        self.assertEqual(tools['schedule_resume'].inputSchema['required'], ['delay_seconds', 'reason', 'continuation'])

    def test_tool_derives_chat_and_sends_auth(self):
        with patch.object(self.tools, 'find_my_session_id', return_value='aaaaaaaa'), patch.object(self.tools, 'read_api_key', return_value='fixture-key'), patch.object(self.tools.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"schedule":{"id":"fixture"}}'
            asyncio.run(self.tools.call_tool('schedule_resume', {'session_id': 'other-chat', 'delay_seconds': 900, 'reason': 'check', 'continuation': 'continue'}))
            req = urlopen.call_args.args[0]
            self.assertEqual(json.loads(req.data)['session_id'], 'aaaaaaaa')
            self.assertEqual(json.loads(req.data)['action'], 'schedule')
            self.assertEqual(req.get_header('X-api-key'), 'fixture-key')

    def test_missing_chat_fails_before_request(self):
        with patch.object(self.tools, 'find_my_session_id', return_value=None), patch.object(self.tools.urllib.request, 'urlopen') as urlopen:
            with self.assertRaises(ValueError):
                asyncio.run(self.tools.call_tool('cancel_resume', {'schedule_id': 'fixture'}))
            urlopen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
