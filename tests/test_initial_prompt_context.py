import unittest
from unittest.mock import Mock, patch

from src.services import acp


class InitialPromptContextTests(unittest.TestCase):
    def test_initial_turn_includes_live_group_context_without_panes(self):
        manager = object.__new__(acp.ACPManager)
        manager.list_sessions = Mock(return_value=[
            {'id': 'self', 'loaded': True},
            {'id': 'asleep', 'loaded': False},
        ])
        manager._save = Mock()
        manager._save_pid_map = Mock()
        session = Mock()
        group_data = {
            'groups': [{'id': 'group', 'name': 'Project', 'color': 'b8860b'}],
            'session_groups': {
                'chat:self': 'group', 'chat:asleep': 'group',
                'notebook:project': 'group', 'notebook:stopped': 'group',
                'jupyter:analysis': 'group', 'terminal': 'group',
                'chat:archived': 'group', 'notebook:other': 'elsewhere',
            },
        }
        with (
            patch('src.services.groups.get_all', return_value=group_data),
            patch('src.services.rewards.get_balance', return_value=13),
            patch.object(acp, 'count_unread_child_messages', return_value=2),
            patch('src.services.notebooks.list_notebooks', return_value=[
                {'name': 'project', 'running': True},
                {'name': 'stopped', 'running': False},
                {'name': 'other', 'running': True},
            ]),
            patch('src.services.pty_service.pty_service.list_sessions', return_value=['terminal']),
            patch('src.routes.websocket._open_jupyter', {'analysis'}),
        ):
            manager._start_new('self', session, 'Read the notebook.')
        session.send_prompt.assert_called_once_with(
            '[Pane context: subagent_messages: 2 unread, group_name: "Project", '
            'group_id: group, group_color: #b8860b, group_members: chat:self, '
            'chat:asleep (sleeping), notebook:project, jupyter:analysis, terminal, '
            'reward balance: 13]\nRead the notebook.',
            initial_only=True,
        )

    def test_ungrouped_context_omits_group_and_zero_unread_count(self):
        manager = object.__new__(acp.ACPManager)
        with (
            patch('src.services.groups.get_all', return_value={'groups': [], 'session_groups': {}}),
            patch('src.services.rewards.get_balance', return_value=0),
            patch.object(acp, 'count_unread_child_messages', return_value=0),
        ):
            self.assertEqual(manager._initial_prompt_context('self'), '[Pane context: reward balance: 0]')
