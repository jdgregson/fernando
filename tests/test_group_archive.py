import ast
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.services import acp, groups


class GroupArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for module, name, value in (
            (groups, 'GROUPS_FILE', str(root / 'groups.json')),
            (acp, 'SESSIONS_FILE', str(root / 'sessions.json')),
            (acp, 'ARCHIVED_FILE', str(root / 'archived.json')),
            (acp, 'KIRO_SESSIONS_DIR', str(root / 'native')),
        ):
            target = patch.object(module, name, value)
            target.start()
            self.addCleanup(target.stop)
        self.manager = acp.ACPManager.__new__(acp.ACPManager)
        self.manager._lock = threading.Lock()
        self.manager._on_status_change = None
        self.manager.sessions = {}
        self.manager._recover_orphans = Mock()
        self.emit = Mock()
        self.csrf = Mock(return_value=True)
        self.scope = {
            'validate_csrf': self.csrf,
            'emit': self.emit,
            'acp_manager': self.manager,
            'acp_subscribers': {},
            'acp_on_event': Mock(),
            '_group_archive_lock': threading.Lock(),
        }
        tree = ast.parse(Path('src/routes/websocket.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ('handle_group_archive', 'handle_group_restore'):
                node.decorator_list = []
                exec(compile(ast.Module(body=[node], type_ignores=[]), '<handler>', 'exec'), self.scope)

    def add_session(self, sid, group_id, backend='opencode', native_id=None):
        session = acp.ACPSession(sid, backend=backend)
        session.acp_session_id = native_id
        session.display_name = 'Chat ' + sid
        session.context_snapshot = {'group_id': group_id, 'fixture': True}
        session.stop = Mock()
        self.manager.sessions[sid] = session
        groups.move_session_to_group('chat:' + sid, group_id)
        return session

    def test_sixty_chats_archive_and_restore_without_starting_any_process(self):
        group = groups.create_group('Large group', '#123456')
        other = groups.create_group('Other')
        sessions = [self.add_session(f'{i:08x}', group['id'], native_id=f'ses_{i}') for i in range(60)]
        outsider = self.add_session('eeeeeeee', other['id'], native_id='ses_outside')
        groups.move_session_to_group('Shell', group['id'])
        groups.move_session_to_group('notebook:work', group['id'])
        groups.move_session_to_group('chat:old', group['id'])
        acp._save_archived_map({'old': {'acp_id': 'ses_old', 'name': 'Previously archived'}})
        lineage = {sessions[1].id: {'parent': sessions[0].id}}
        with patch.object(acp, '_load_lineage', return_value=lineage):
            self.scope['handle_group_archive']({'group_id': group['id']})
        self.assertEqual(set(self.manager.sessions), {outsider.id})
        for session in sessions:
            session.stop.assert_called_once()
        outsider.stop.assert_not_called()
        self.assertEqual([g['id'] for g in groups.list_groups()], [other['id']])
        archived_group = groups.list_groups(archived=True)[0]
        self.assertEqual(len(archived_group['archived_members']), 62)
        self.assertNotIn('chat:old', archived_group['archived_members'])
        self.assertEqual(len(acp._load_archived_map()), 61)
        with (
            patch.object(acp.threading, 'Thread') as thread,
            patch.object(acp.ACPSession, '_load_history'),
            patch.object(acp.ACPSession, 'start') as start,
            patch.object(acp.ACPSession, 'load') as load,
        ):
            self.scope['handle_group_restore']({'group_id': group['id']})
            thread.assert_not_called()
            start.assert_not_called()
            load.assert_not_called()
        self.assertEqual(len(self.manager.sessions), 61)
        self.assertEqual(groups.list_groups(archived=True), [])
        restored_group = next(g for g in groups.list_groups() if g['id'] == group['id'])
        self.assertEqual(restored_group, group)
        self.assertEqual(set(acp._load_archived_map()), {'old'})
        with patch.object(acp, '_load_lineage', return_value=lineage):
            child = next(s for s in self.manager.list_sessions() if s['id'] == sessions[1].id)
        self.assertEqual(child['parent_id'], sessions[0].id)
        for original in sessions:
            restored = self.manager.get_session(original.id)
            self.assertFalse(restored.is_loaded)
            self.assertFalse(restored.ready)
            self.assertEqual(restored.acp_session_id, original.acp_session_id)
            self.assertEqual(restored.context_snapshot, original.context_snapshot)
            self.assertEqual(groups.get_session_groups()['chat:' + original.id], group['id'])
        saved = json.loads(Path(acp.SESSIONS_FILE).read_text())
        self.assertTrue(all(not saved[s.id]['loaded'] for s in sessions))
        with patch.object(acp.threading, 'Thread') as thread:
            self.assertTrue(self.manager.reload_session(sessions[0].id))
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['args'][0], sessions[0].id)

    def test_sleeping_missing_native_session_survives_restart_without_launch(self):
        acp._save_archived_map({
            'missing': {'acp_id': 'missing-native', 'backend': 'kiro'},
            'new': {'acp_id': None, 'backend': 'opencode'},
        })
        with patch.object(acp.threading, 'Thread') as thread, patch.object(acp.ACPSession, '_load_history'):
            for sid in ('missing', 'new'):
                self.assertTrue(self.manager.restore_session(sid, sleeping=True))
            self.manager.sessions = {}
            with patch.object(acp, '_pop_continuation', return_value=None):
                self.manager.restore_sessions(lambda sid: Mock())
            thread.assert_not_called()
        self.assertEqual(set(self.manager.sessions), {'missing', 'new'})
        self.assertTrue(self.manager.get_session('new')._start_on_open)
        with patch.object(acp.threading, 'Thread') as thread:
            self.assertTrue(self.manager.reload_session('new'))
            self.assertEqual(thread.call_args.kwargs['target'], self.manager._start_new)
            self.assertFalse(self.manager.reload_session('new'))
            thread.return_value.start.assert_called_once()

    def test_invalid_csrf_cannot_archive_or_restore(self):
        group = groups.create_group('Protected')
        self.csrf.return_value = False
        self.scope['handle_group_archive']({'group_id': group['id']})
        self.assertEqual(groups.list_groups(), [group])
        groups.archive_group(group['id'], set())
        self.scope['handle_group_restore']({'group_id': group['id']})
        self.assertEqual(len(groups.list_groups(archived=True)), 1)
        self.emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
