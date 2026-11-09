import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from src.services import acp, subagent_core as core, subagent_schedules


class ManagementTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.patch(core, 'SUBAGENT_DIR', str(self.directory))
        self.task_id = 'watcher-unique'
        (self.directory / self.task_id).mkdir()
        self.record = {'task_id': self.task_id, 'schedule': '* * * * *', 'schedule_mode': 'spawn',
                       'schedule_enabled': True, 'parent_session_id': 'parent', 'session_ids': ['worker']}
        core.save_task(self.task_id, self.record)
        self.script = str(self.directory / self.task_id / 'spawn.sh')
        self.crontab = '* * * * * ' + self.script + '\n0 * * * * /unrelated/task\n'
        self.sessions = {'worker': SimpleNamespace(display_name='renamed-worker')}
        self.manager = Mock()
        self.manager.get_session.side_effect = self.sessions.get
        self.manager.list_sessions.side_effect = lambda: [{'id': sid, 'name': value.display_name} for sid, value in self.sessions.items()]
        self.resume = subagent_schedules.ResumeScheduler(self.directory / 'resume.json')
        self.patch(subagent_schedules, 'resume_scheduler', self.resume)
        self.patch(acp, 'get_parent', lambda sid: 'parent' if sid == 'worker' else None)
        self.patch(acp, 'is_child_of', lambda child, parent: child == 'worker' and parent == 'parent')
        self.commands = []
        self.patch(core.subprocess, 'run', self.command)

    def patch(self, target, name, value):
        override = patch.object(target, name, value)
        override.start()
        self.addCleanup(override.stop)

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args == ['crontab', '-l']:
            return subprocess.CompletedProcess(args, 0, self.crontab, '')
        if args == ['crontab', '-']:
            self.crontab = kwargs['input']
            return subprocess.CompletedProcess(args, 0, '', '')
        raise AssertionError(args)

    def test_legacy_worker_self_cancel_stops_cron_without_termination(self):
        result = core.manage_schedule(self.manager, 'worker', 'cancel')
        self.assertFalse(result['enabled'])
        self.assertEqual(result['schedule_mode'], 'spawn')
        self.assertEqual(self.crontab, '0 * * * * /unrelated/task\n')
        self.assertFalse(core.read_task(self.task_id)['schedule_enabled'])
        self.manager.destroy_session.assert_not_called()
        self.assertFalse(core.manage_schedule(self.manager, 'worker', 'cancel')['enabled'])

    def test_parent_cancels_before_first_worker_exists(self):
        self.sessions.clear()
        self.record['session_ids'] = []
        core.save_task(self.task_id, self.record)
        self.assertFalse(core.manage_schedule(self.manager, 'parent', 'cancel', task_id=self.task_id)['enabled'])

    def test_old_cron_worker_without_new_metadata_can_self_cancel(self):
        core.save_task(self.task_id, {'task_id': self.task_id, 'schedule': '* * * * *'})
        self.sessions['worker'].display_name = 'subagent-' + self.task_id
        result = core.manage_schedule(self.manager, 'worker', 'cancel')
        self.assertFalse(result['enabled'])
        self.assertNotIn(self.script, self.crontab)

    def test_parent_can_cancel_old_task_by_its_existing_worker(self):
        core.save_task(self.task_id, {'task_id': self.task_id, 'schedule': '* * * * *'})
        self.sessions['worker'].display_name = 'subagent-' + self.task_id
        result = core.manage_schedule(self.manager, 'parent', 'cancel', task_id=self.task_id)
        self.assertFalse(result['enabled'])

    def test_unrelated_caller_cannot_cancel(self):
        for target in ({'task_id': self.task_id}, {'session_id': 'worker'}):
            with self.assertRaises(PermissionError):
                core.manage_schedule(self.manager, 'stranger', 'cancel', **target)
        self.assertIn(self.script, self.crontab)

    def test_unknown_schedule_fails_explicitly(self):
        self.crontab = ''
        with self.assertRaisesRegex(ValueError, 'nothing was canceled'):
            core.manage_schedule(self.manager, 'worker', 'cancel')
        self.assertTrue(core.read_task(self.task_id)['schedule_enabled'])

    def test_resume_uses_same_interface_without_crontab(self):
        self.resume.create('worker', 'parent', '* * * * *', 'UTC', 'check', 'start')
        result = core.manage_schedule(self.manager, 'worker', 'cancel')
        self.assertEqual(result['schedule_mode'], 'resume')
        self.assertFalse(result['enabled'])
        self.assertEqual(self.commands, [])

    def test_termination_cancels_before_destroying_all_task_workers(self):
        self.record['session_id'] = 'worker'
        core.save_task(self.task_id, self.record)
        self.manager.destroy_session.side_effect = lambda *args, **kwargs: self.assertNotIn(self.script, self.crontab)
        result = core.terminate_subagent(self.task_id, manager=self.manager)
        self.assertTrue(result['schedule_canceled'])
        self.manager.destroy_session.assert_called_once_with('worker', delete_history=False)

    def test_session_termination_cancels_origin_even_after_rename(self):
        self.manager.destroy_session.side_effect = lambda *args, **kwargs: self.assertNotIn(self.script, self.crontab)
        result = core.terminate_subagent('worker', manager=self.manager)
        self.assertTrue(result['schedule_canceled'])
        self.manager.destroy_session.assert_called_once()

    def test_cancel_fences_already_launched_cron_request(self):
        core.manage_schedule(self.manager, 'worker', 'cancel')
        with self.assertRaisesRegex(ValueError, 'no new agent'):
            core.prepare_scheduled_spawn('subagent-' + self.task_id, 'parent')
        self.assertIsNone(core.prepare_scheduled_spawn('ordinary-agent', 'parent'))

    def test_direct_cron_removal_also_fences_launches(self):
        result = core.remove_cron_job(self.task_id)
        self.assertTrue(result['schedule_canceled'])
        with self.assertRaises(ValueError):
            core.prepare_scheduled_spawn('subagent-' + self.task_id, 'parent')

    def test_immediate_tasks_can_start(self):
        self.record.update(schedule=None, schedule_enabled=False)
        core.save_task(self.task_id, self.record)
        self.assertEqual(core.prepare_scheduled_spawn('subagent-' + self.task_id, 'parent'), self.task_id)

    def test_owner_mismatch_rejected_before_registration(self):
        with self.assertRaisesRegex(ValueError, 'owner'):
            core.prepare_scheduled_spawn('subagent-' + self.task_id, 'stranger')

    def test_failed_crontab_write_is_not_reported_as_success(self):
        original = self.command

        def fail(args, **kwargs):
            if args == ['crontab', '-']:
                return subprocess.CompletedProcess(args, 1, '', 'permission denied')
            return original(args, **kwargs)

        with patch.object(core.subprocess, 'run', fail):
            with self.assertRaisesRegex(RuntimeError, 'permission denied'):
                core.manage_schedule(self.manager, 'worker', 'cancel')
        self.assertFalse(core.read_task(self.task_id)['schedule_enabled'])
        with self.assertRaises(ValueError):
            core.prepare_scheduled_spawn('subagent-' + self.task_id, 'parent')

    def test_crontab_noop_write_detected(self):
        original = self.command

        def ignore_write(args, **kwargs):
            if args == ['crontab', '-']:
                return subprocess.CompletedProcess(args, 0, '', '')
            return original(args, **kwargs)

        with patch.object(core.subprocess, 'run', ignore_write):
            with self.assertRaisesRegex(RuntimeError, 'verified'):
                core.manage_schedule(self.manager, 'worker', 'cancel')

    def test_update_preserves_unrelated_cron_and_rejects_resume_only_fields(self):
        result = core.manage_schedule(self.manager, 'worker', 'update', cron_schedule='*/5 * * * *')
        self.assertEqual(result['cron_schedule'], '*/5 * * * *')
        self.assertIn('*/5 * * * * ' + self.script, self.crontab)
        self.assertIn('/unrelated/task', self.crontab)
        with self.assertRaises(ValueError):
            core.manage_schedule(self.manager, 'worker', 'update', wake_message='other')

    def test_cron_schedule_rejects_line_injection(self):
        with self.assertRaises(ValueError):
            core.schedule_cron(self.script, '* * * *\n*')

    def test_cancel_and_spawn_are_serialized(self):
        entered = threading.Event()
        finished = threading.Event()

        def cancel():
            entered.set()
            core.manage_schedule(self.manager, 'worker', 'cancel')
            finished.set()

        with core.schedule_guard():
            worker = threading.Thread(target=cancel)
            worker.start()
            self.assertTrue(entered.wait(3))
            self.assertFalse(finished.wait(0.05))
            self.assertEqual(core.prepare_scheduled_spawn('subagent-' + self.task_id, 'parent'), self.task_id)
        worker.join(3)
        self.assertTrue(finished.is_set())
        with self.assertRaises(ValueError):
            core.prepare_scheduled_spawn('subagent-' + self.task_id, 'parent')

    def test_route_rejects_canceled_legacy_spawn_before_creating_agent(self):
        from src.routes import web
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        core.manage_schedule(self.manager, 'worker', 'cancel')
        with patch.object(web, '_check_api_key', return_value=True), patch.object(web, 'acp_manager', self.manager):
            result = app.test_client().post('/api/spawn_subagent', json={'task': 'check', 'name': 'subagent-' + self.task_id, 'parent_session_id': 'parent'})
        self.assertEqual(result.status_code, 409)
        self.manager.create_session.assert_not_called()

    def test_unified_http_cancel_and_termination_paths(self):
        from src.routes import web
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        client = app.test_client()
        with patch.object(web, '_check_api_key', return_value=False):
            self.assertEqual(client.post('/api/acp/terminate', json={'task_id': self.task_id}).status_code, 401)
        with patch.object(web, '_check_api_key', return_value=True), patch.object(web, 'acp_manager', self.manager):
            denied = client.post('/api/acp/terminate', json={'task_id': self.task_id, 'actor_session_id': 'stranger'})
            self.assertEqual(denied.status_code, 403)
            canceled = client.post('/api/acp/subagent_schedule', json={'actor_session_id': 'worker', 'action': 'cancel'})
            self.assertEqual(canceled.status_code, 200)
            self.assertFalse(canceled.json['schedule']['enabled'])
            terminated = client.post('/api/acp/terminate', json={'actor_session_id': 'parent', 'task_id': self.task_id})
            self.assertEqual(terminated.status_code, 200)
            self.assertTrue(terminated.json['schedule_canceled'])
        self.assertNotIn(self.script, self.crontab)
        self.manager.destroy_session.assert_called_once()

    def test_missing_schedule_http_returns_error(self):
        from src.routes import web
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        self.crontab = ''
        with patch.object(web, '_check_api_key', return_value=True), patch.object(web, 'acp_manager', self.manager):
            result = app.test_client().post('/api/acp/subagent_schedule', json={'actor_session_id': 'worker', 'action': 'cancel'})
        self.assertEqual(result.status_code, 400)
        self.assertIn('nothing was canceled', result.json['error'])


class ManagementToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        with patch.object(sys, 'path', [str(root / 'mcp_servers'), *sys.path]):
            spec = importlib.util.spec_from_file_location('management_subagents_mcp', root / 'mcp_servers' / 'subagents_mcp.py')
            cls.tools = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.tools)

    def test_default_monitor_reuses_session_without_cron(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(core, 'SUBAGENT_DIR', directory), patch.object(self.tools, 'write_spawn_script') as script, patch.object(self.tools, 'schedule_cron') as cron, patch.object(self.tools, 'read_api_key', return_value='key'), patch.object(self.tools.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.read.return_value = b'{"session_id":"worker","schedule":{"enabled":true}}'
            result = self.tools.create_subagent('monitor', 'check', cron_schedule='*/5 * * * *', parent_session_id='parent', group_id='fixture')
            self.assertEqual(result['schedule_mode'], 'resume')
            self.assertEqual(json.loads(urlopen.call_args.args[0].data)['schedule_mode'], 'resume')
            script.assert_not_called()
            cron.assert_not_called()

    def test_explicit_spawn_includes_correct_cancel_instructions_and_owner(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(core, 'SUBAGENT_DIR', directory), patch.object(self.tools, 'schedule_cron') as cron:
            result = self.tools.create_subagent('monitor', 'check', cron_schedule='*/5 * * * *', schedule_mode='spawn', parent_session_id='parent', group_id='fixture')
            self.assertEqual(result['schedule_mode'], 'spawn')
            record = core.read_task(result['task_id'])
            self.assertEqual(record['parent_session_id'], 'parent')
            self.assertIn('manage_subagent_schedule(action="cancel", task_id=', record['additional_context'])
            cron.assert_called_once()

    def test_termination_preserves_task_id_and_derives_actor(self):
        with patch.object(self.tools, 'find_my_session_id', return_value='parent'), patch.object(self.tools, 'read_api_key', return_value='key'), patch.object(self.tools.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.read.return_value = b'{"terminated":true}'
            asyncio.run(self.tools.call_tool('terminate_subagent', {'task_id': 'monitor-unique', 'actor_session_id': 'stranger'}))
            payload = json.loads(urlopen.call_args.args[0].data)
            self.assertEqual(payload, {'task_id': 'monitor-unique', 'actor_session_id': 'parent'})


if __name__ == '__main__':
    unittest.main()
