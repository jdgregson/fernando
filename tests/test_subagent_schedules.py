import asyncio
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from flask import Flask

from src.services import acp, subagent_schedules
from src.services.subagent_schedules import ResumeScheduler, next_occurrence, validate_schedule


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'schedules.json'
        self.scheduler = ResumeScheduler(self.path)
        self.now = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo('UTC')).timestamp()
        self.manager = acp.ACPManager.__new__(acp.ACPManager)
        self.manager._lock = threading.Lock()
        self.manager._active_pane_sessions = set()
        self.manager._save = Mock()
        self.manager._save_pid_map = Mock()
        self.manager._broadcast_sessions_list = Mock()
        self.session = acp.ACPSession('aaaaaaaa')
        self.session.acp_session_id = 'original-native-conversation'
        self.session._save_history = Mock()
        self.session._send = Mock()
        self.session._notify_status_change = Mock()
        self.manager.sessions = {self.session.id: self.session}
        self.ready()
        self.scheduler.create(self.session.id, 'bbbbbbbb', '* * * * *', 'UTC', 'check job', 'start monitoring', now=self.now)

    def ready(self):
        self.session.proc = Mock()
        self.session.proc.poll.return_value = None
        self.session.ready = True
        self.session._alive = True
        self.session._reloading = False

    def finish(self, now):
        self.session._is_prompting = False
        self.session._last_activity = now

    def test_initial_and_repeated_checks_keep_one_conversation(self):
        self.scheduler.tick(self.manager, self.now)
        self.assertEqual(self.session.history[-1]['text'], 'start monitoring')
        self.finish(self.now)
        self.scheduler.tick(self.manager, self.now + 60)
        self.assertEqual(self.session.history[-1]['text'], '[SCHEDULED CHECK] check job')
        self.finish(self.now + 60)
        self.scheduler.tick(self.manager, self.now + 120)
        self.assertEqual(len(self.manager.sessions), 1)
        self.assertEqual(self.session._send.call_count, 3)
        for call in self.session._send.call_args_list:
            self.assertEqual(call.args[0]['params']['sessionId'], 'original-native-conversation')

    def test_busy_ticks_skip_without_queueing_or_cancelling(self):
        self.scheduler.tick(self.manager, self.now)
        self.session.cancel = Mock()
        for seconds in (60, 120, 180):
            self.scheduler.tick(self.manager, self.now + seconds)
        self.assertEqual(self.session._send.call_count, 1)
        self.assertEqual(self.session._agent_message_queue, [])
        self.assertEqual(self.scheduler.get(self.session.id)['last_result'], 'skipped_busy')
        self.session.cancel.assert_not_called()
        self.finish(self.now + 180)
        self.scheduler.tick(self.manager, self.now + 181)
        self.assertEqual(self.session._send.call_count, 1)
        self.scheduler.tick(self.manager, self.now + 240)
        self.assertEqual(self.session._send.call_count, 2)

    def test_sleep_and_wake_retain_context(self):
        self.scheduler.tick(self.manager, self.now)
        self.finish(self.now)
        self.scheduler.tick(self.manager, self.now + 6)
        self.assertFalse(self.session.is_loaded)
        self.assertEqual(self.session.acp_session_id, 'original-native-conversation')
        with patch.object(self.manager, 'reload_session') as reload:
            self.scheduler.tick(self.manager, self.now + 60)
            reload.assert_called_once_with(self.session.id)
        self.ready()
        self.scheduler.tick(self.manager, self.now + 61)
        self.assertEqual(self.session._send.call_count, 2)
        self.assertEqual(len(self.session.history), 2)

    def test_pending_user_work_and_active_panes_prevent_sleep(self):
        self.scheduler.tick(self.manager, self.now)
        self.finish(self.now)
        self.manager._active_pane_sessions.add(self.session.id)
        self.scheduler.tick(self.manager, self.now + 6)
        self.assertTrue(self.session.is_loaded)
        self.manager._active_pane_sessions.clear()
        self.session._agent_message_queue.append({'type': 'user_message_queued'})
        self.scheduler.tick(self.manager, self.now + 7)
        self.assertTrue(self.session.is_loaded)
        self.scheduler.tick(self.manager, self.now + 60)
        self.assertEqual(self.session._send.call_count, 1)

    def test_cancel_during_wake_prevents_delivery_and_is_persisted(self):
        self.scheduler.tick(self.manager, self.now)
        self.finish(self.now)
        self.session.proc = None
        with patch.object(self.manager, 'reload_session'):
            self.scheduler.tick(self.manager, self.now + 60)
        self.scheduler.cancel(self.session.id)
        self.ready()
        self.scheduler.tick(self.manager, self.now + 61)
        self.assertEqual(self.session._send.call_count, 1)
        restored = ResumeScheduler(self.path)
        self.assertFalse(restored.get(self.session.id)['enabled'])

    def test_restart_does_not_replay_missed_ticks(self):
        self.scheduler.tick(self.manager, self.now)
        self.finish(self.now)
        restored = ResumeScheduler(self.path)
        with patch.object(subagent_schedules.time, 'time', return_value=self.now + 3605), patch.object(subagent_schedules.threading, 'Thread'):
            restored.start(self.manager)
        self.assertEqual(restored.get(self.session.id)['next_run'], self.now + 3660)
        restored.tick(self.manager, self.now + 3605)
        self.assertEqual(self.session._send.call_count, 1)

    def test_initial_delivery_is_not_duplicated_after_restart(self):
        self.session.history = [{'type': 'user_prompt', 'text': 'start monitoring'}]
        restored = ResumeScheduler(self.path)
        restored.tick(self.manager, self.now)
        self.session._send.assert_not_called()
        self.assertEqual(restored.get(self.session.id)['last_result'], 'already_started')

    def test_missing_conversation_fails_without_spawning_replacement(self):
        self.scheduler.tick(self.manager, self.now)
        self.finish(self.now)
        self.session.proc = None
        self.session.acp_session_id = None
        with self.assertLogs(subagent_schedules.logger, level='ERROR'):
            self.scheduler.tick(self.manager, self.now + 60)
        row = self.scheduler.get(self.session.id)
        self.assertFalse(row['enabled'])
        self.assertIn('original conversation', row['error'])

    def test_failed_wake_has_bounded_deadline(self):
        self.session.ready = False
        self.scheduler.tick(self.manager, self.now)
        with self.assertLogs(subagent_schedules.logger, level='ERROR'):
            self.scheduler.tick(self.manager, self.now + 120)
        self.assertFalse(self.scheduler.get(self.session.id)['enabled'])
        self.session._send.assert_not_called()

    def test_update_changes_future_checks_and_rejects_invalid_values(self):
        self.scheduler.tick(self.manager, self.now)
        with patch.object(subagent_schedules.time, 'time', return_value=self.now):
            result = self.scheduler.update(self.session.id, cron_schedule='*/5 * * * *', wake_message='new check')
        self.assertEqual(result['next_run'], self.now + 300)
        with self.assertRaises(ValueError):
            self.scheduler.update(self.session.id, timezone='not/a/timezone')
        self.finish(self.now)
        self.scheduler.tick(self.manager, self.now + 300)
        self.assertEqual(self.session.history[-1]['text'], '[SCHEDULED CHECK] new check')

    def test_removed_session_cancels_schedule(self):
        self.manager.sessions.clear()
        self.scheduler.tick(self.manager, self.now)
        self.assertEqual(self.scheduler.get(self.session.id)['last_result'], 'session_removed')

    def test_destroy_cancels_before_stopping_session(self):
        with patch.object(subagent_schedules, 'resume_scheduler', self.scheduler):
            self.session.stop = Mock(side_effect=lambda: self.assertFalse(self.scheduler.get(self.session.id)['enabled']))
            self.manager.destroy_session(self.session.id, delete_history=False)
        self.session.stop.assert_called_once()

    def test_archive_cancels_before_stopping_session(self):
        with patch.object(subagent_schedules, 'resume_scheduler', self.scheduler), patch.object(acp, '_load_archived_map', return_value={}), patch.object(acp, '_save_archived_map'):
            self.session.stop = Mock(side_effect=lambda: self.assertFalse(self.scheduler.get(self.session.id)['enabled']))
            self.manager.archive_session(self.session.id)
        self.session.stop.assert_called_once()

    def test_concurrent_checks_claim_only_one_turn(self):
        barrier = threading.Barrier(3)
        results = []

        def deliver():
            barrier.wait()
            results.append(self.manager.deliver_scheduled_prompt(self.session.id, 'check'))

        threads = [threading.Thread(target=deliver) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ['delivered', 'skipped_busy'])
        self.session._send.assert_called_once()

    def test_one_failed_check_does_not_stop_other_schedules(self):
        self.scheduler.create('cccccccc', 'bbbbbbbb', '* * * * *', 'UTC', 'check other', 'start other', now=self.now)
        manager = Mock()
        manager.deliver_scheduled_prompt.side_effect = [OSError('broken transport'), 'delivered']
        with self.assertLogs(subagent_schedules.logger, level='ERROR'):
            self.scheduler.tick(manager, self.now)
        self.assertFalse(self.scheduler.get(self.session.id)['enabled'])
        self.assertEqual(self.scheduler.get('cccccccc')['last_result'], 'delivered')

    def test_cron_validation_and_timezone_dst(self):
        for expression in ('* * * * * *', 'garbage', '61 * * * *', '0 0 31 2 *'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                validate_schedule(expression, 'UTC', 'check')
        before = datetime(2026, 10, 31, 10, tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
        after = next_occurrence('0 9 * * *', 'America/Los_Angeles', before)
        local = datetime.fromtimestamp(after, ZoneInfo('America/Los_Angeles'))
        self.assertEqual((local.day, local.hour, local.utcoffset().total_seconds()), (1, 9, -28800))


class ScheduleRouteTests(unittest.TestCase):
    def setUp(self):
        from src.routes import web
        self.web = web
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        self.client = app.test_client()
        self.scheduler = Mock()
        self.scheduler.get.return_value = {'enabled': True}
        self.scheduler.create.return_value = {'enabled': True}
        for target, name, value in (
            (web, '_check_api_key', Mock(return_value=True)),
            (subagent_schedules, 'resume_scheduler', self.scheduler),
            (acp, 'get_parent', lambda sid: 'bbbbbbbb' if sid == 'aaaaaaaa' else None),
            (acp, 'is_child_of', lambda child, parent: (child, parent) == ('aaaaaaaa', 'bbbbbbbb')),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, actor='bbbbbbbb', **fields):
        return self.client.post('/api/acp/subagent_schedule', json={'actor_session_id': actor, **fields})

    def test_requires_api_key(self):
        self.web._check_api_key.return_value = False
        self.assertEqual(self.post(session_id='aaaaaaaa', action='cancel').status_code, 401)
        self.scheduler.cancel.assert_not_called()

    def test_parent_and_child_can_manage_but_unrelated_session_cannot(self):
        self.assertEqual(self.post(session_id='aaaaaaaa', action='get').status_code, 200)
        self.assertEqual(self.post(actor='aaaaaaaa', action='get').status_code, 200)
        self.assertEqual(self.post(actor='cccccccc', session_id='aaaaaaaa', action='cancel').status_code, 403)
        self.assertEqual(self.post(actor='bbbbbbbb', action='cancel').status_code, 403)
        self.scheduler.cancel.assert_not_called()

    def test_child_can_cancel_own_schedule(self):
        self.scheduler.cancel.return_value = {'enabled': False}
        response = self.post(actor='aaaaaaaa', action='cancel')
        self.assertEqual(response.status_code, 200)
        self.scheduler.cancel.assert_called_once_with('aaaaaaaa')

    def test_parent_can_update_and_invalid_update_returns_error(self):
        self.scheduler.update.return_value = {'cron_schedule': '*/5 * * * *'}
        response = self.post(session_id='aaaaaaaa', action='update', cron_schedule='*/5 * * * *')
        self.assertEqual(response.status_code, 200)
        self.scheduler.update.assert_called_once_with('aaaaaaaa', cron_schedule='*/5 * * * *')
        self.scheduler.update.side_effect = ValueError('Invalid cron_schedule')
        self.assertEqual(self.post(session_id='aaaaaaaa', action='update', cron_schedule='invalid').status_code, 400)

    def test_bad_resume_schedule_is_rejected_before_creating_session(self):
        with patch.object(self.web, 'acp_manager') as manager:
            response = self.client.post('/api/spawn_subagent', json={
                'task': 'monitor', 'schedule_mode': 'resume', 'parent_session_id': 'bbbbbbbb',
                'cron_schedule': 'not cron',
            })
            self.assertEqual(response.status_code, 400)
            manager.create_session.assert_not_called()

    def test_resume_spawn_creates_one_session_and_registers_initial_task(self):
        with patch.object(self.web, 'acp_manager') as manager, patch.object(acp, 'set_parent') as lineage, patch.object(self.web.threading, 'Thread') as thread:
            manager.create_session.return_value = 'aaaaaaaa'
            response = self.client.post('/api/spawn_subagent', json={
                'task': 'monitor', 'schedule_mode': 'resume', 'parent_session_id': 'bbbbbbbb',
                'cron_schedule': '*/5 * * * *', 'group_id': 'fixture-group',
            })
            self.assertEqual(response.status_code, 200)
            manager.create_session.assert_called_once()
            lineage.assert_called_once_with('aaaaaaaa', 'bbbbbbbb')
            self.assertEqual(self.scheduler.create.call_args.args[:3], ('aaaaaaaa', 'bbbbbbbb', '*/5 * * * *'))
            self.assertEqual(self.scheduler.create.call_args.args[-1], 'monitor')
            thread.assert_not_called()


class ScheduleToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        with patch.object(sys, 'path', [str(root / 'mcp_servers'), *sys.path]):
            spec = importlib.util.spec_from_file_location('schedule_subagents_mcp', root / 'mcp_servers' / 'subagents_mcp.py')
            cls.tools = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.tools)

    def test_schema_exposes_resume_and_schedule_management(self):
        tools = {tool.name: tool for tool in asyncio.run(self.tools.list_tools())}
        self.assertIn('resume', tools['spawn_subagent'].inputSchema['properties']['schedule_mode']['enum'])
        self.assertIn('manage_subagent_schedule', tools)

    def test_resume_never_writes_spawn_script_or_crontab(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.tools, 'create_workspace', return_value=('monitor-unique', directory)), patch.object(self.tools, 'write_spawn_script') as script, patch.object(self.tools, 'schedule_cron') as cron, patch.object(self.tools, 'read_api_key', return_value='fixture-key'), patch.object(self.tools.urllib.request, 'urlopen') as urlopen:
            response = Mock()
            response.read.return_value = b'{"session_id":"aaaaaaaa","schedule":{"enabled":true}}'
            urlopen.return_value = response
            result = self.tools.create_subagent('monitor', 'watch job', cron_schedule='*/5 * * * *', schedule_mode='resume', parent_session_id='bbbbbbbb', group_id='fixture-group')
            self.assertEqual(result['session_id'], 'aaaaaaaa')
            payload = json.loads(urlopen.call_args.args[0].data)
            self.assertEqual(payload['schedule_mode'], 'resume')
            self.assertEqual(payload['parent_session_id'], 'bbbbbbbb')
            self.assertEqual(json.loads((Path(directory) / 'task.json').read_text())['session_id'], 'aaaaaaaa')
            script.assert_not_called()
            cron.assert_not_called()

    def test_invalid_combinations_do_not_create_workspace(self):
        with patch.object(self.tools, 'create_workspace') as workspace:
            for kwargs in ({'at_schedule': 'now', 'cron_schedule': '* * * * *'}, {'schedule_mode': 'resume'}, {'schedule_mode': 'other'}):
                self.assertIn('error', self.tools.create_subagent('monitor', 'watch', **kwargs))
            workspace.assert_not_called()

    def test_schedule_tool_derives_actor_instead_of_forwarding_supplied_actor(self):
        with patch.object(self.tools, 'find_my_session_id', return_value='aaaaaaaa'), patch.object(self.tools, 'read_api_key', return_value='fixture'), patch.object(self.tools.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"schedule":{"enabled":false}}'
            asyncio.run(self.tools.call_tool('manage_subagent_schedule', {'action': 'cancel', 'actor_session_id': 'bbbbbbbb'}))
            self.assertEqual(json.loads(urlopen.call_args.args[0].data)['actor_session_id'], 'aaaaaaaa')
