import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services import acp


class SidebarOriginTests(unittest.TestCase):
    def session(self, name):
        return SimpleNamespace(id='aaaaaaaa', display_name=name, history=[
            {'type': 'user_prompt'}, {'type': 'continuation'},
            {'type': 'user_prompt'}, {'type': 'user_prompt'},
        ])

    def test_persisted_turn_survives_rename(self):
        relation = {'parent': 'bbbbbbbb', 'kind': 'fork', 'fork_turn': 7}
        self.assertEqual(acp._session_origin(self.session('renamed'), relation), ('fork', 7))

    @patch.object(acp.chat_history, 'reference', return_value=None)
    def test_legacy_nested_fork_uses_own_selected_turn(self, reference):
        session = self.session('name (fork@2) (fork@5)')
        self.assertEqual(acp._session_origin(session, {'parent': 'bbbbbbbb'}), ('fork', 5))

    @patch.object(acp.chat_history, 'reference', return_value={'event_count': 3})
    def test_full_fork_counts_only_inherited_user_turns(self, reference):
        self.assertEqual(acp._session_origin(self.session('name (fork)'), {}), ('fork', 2))
        self.assertEqual(acp._session_origin(self.session('renamed'), {}), ('fork', 2))

    @patch.object(acp.chat_history, 'reference', return_value=None)
    def test_subagent_root_and_unknown_legacy_turn(self, reference):
        self.assertEqual(acp._session_origin(self.session('worker'), {'parent': 'bbbbbbbb'}), ('subagent', None))
        self.assertEqual(acp._session_origin(self.session('root'), {}), (None, None))
        self.assertEqual(acp._session_origin(self.session('name (fork)'), {}), ('fork', None))


if __name__ == '__main__':
    unittest.main()
