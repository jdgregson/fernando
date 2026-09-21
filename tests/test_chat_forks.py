import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.services import acp, chat_history, rag


def prompt(text):
    return {'type': 'user_prompt', 'text': text}


def reply(text):
    return {'method': 'session/update', 'params': {'update': {
        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text},
    }}}


class HistoryForkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / 'chat_history'
        self.history.mkdir()
        for target, name, value in (
            (chat_history, 'ROOT', self.root),
            (chat_history, 'HISTORY_DIR', self.history),
            (acp, 'HISTORY_DIR', str(self.history)),
            (acp, 'LINEAGE_FILE', str(self.root / 'lineage.json')),
            (acp, 'SESSIONS_FILE', str(self.root / 'sessions.json')),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = [prompt('one'), reply('first'), prompt('two'), reply('second')]
        self.write('aaaaaaaa', self.events)

    def write(self, sid, events):
        chat_history.path_for(sid).write_text(''.join(json.dumps(e) + '\n' for e in events))

    def test_nested_forks_store_only_new_events_and_have_fixed_boundaries(self):
        chat_history.fork('aaaaaaaa', 'bbbbbbbb', 2)
        session = acp.ACPSession('bbbbbbbb')
        session._load_history()
        session.history.extend([prompt('branch'), reply('branch answer')])
        session._save_history()
        chat_history.fork('bbbbbbbb', 'cccccccc', 4)
        self.write('aaaaaaaa', self.events + [prompt('later'), reply('not inherited')])
        self.assertEqual(chat_history.load('cccccccc'), self.events[:2] + session.history[2:])
        self.assertEqual(len(chat_history.read_local('bbbbbbbb')), 3)
        self.assertEqual(len(chat_history.read_local('cccccccc')), 1)
        self.assertEqual(chat_history.path_for('cccccccc').stat().st_mode & 0o777, 0o600)

    def test_parent_deletion_retains_history_and_assets_until_last_descendant(self):
        cache = self.root / 'image_cache' / 'aaaaaaaa'
        cache.mkdir(parents=True)
        (cache / 'image.png').write_bytes(b'image')
        chat_history.fork('aaaaaaaa', 'bbbbbbbb', 2)
        chat_history.fork('bbbbbbbb', 'cccccccc', 2)
        self.assertEqual(chat_history.delete('aaaaaaaa'), [])
        self.assertTrue(chat_history.is_deleted('aaaaaaaa'))
        self.assertTrue(cache.exists())
        self.assertEqual(chat_history.delete('bbbbbbbb'), [])
        self.assertEqual(chat_history.load('cccccccc'), self.events[:2])
        self.assertEqual(set(chat_history.delete('cccccccc')), {'aaaaaaaa', 'bbbbbbbb', 'cccccccc'})
        self.assertFalse(cache.exists())

    def test_invalid_references_fail(self):
        with self.assertRaises(ValueError):
            chat_history.load('../config')
        with self.assertRaises(ValueError):
            chat_history.fork('aaaaaaaa', 'bbbbbbbb', 99)
        self.write('bbbbbbbb', [{'type': 'history_reference', 'session_id': 'bbbbbbbb', 'event_count': 0}])
        with self.assertRaises(ValueError):
            chat_history.load('bbbbbbbb')

    def test_rag_indexes_only_owned_turns_with_resolved_indices(self):
        chat_history.fork('aaaaaaaa', 'bbbbbbbb', 2)
        history = self.events[:2] + [prompt('branch'), reply('answer')]
        collection = Mock()
        collection.get.return_value = {'ids': ['bbbbbbbb_0_0'], 'documents': ['inherited'], 'metadatas': [{'turn_index': 0}]}
        with patch.object(rag, '_get_collection', return_value=collection):
            rag.index_session('bbbbbbbb', 'branch', history)
        collection.delete.assert_called_once_with(ids=['bbbbbbbb_0_0'])
        indexed = collection.upsert.call_args.kwargs
        self.assertEqual(indexed['ids'], ['bbbbbbbb_1_user_0', 'bbbbbbbb_1_assistant_0'])
        self.assertEqual(indexed['documents'], ['branch', 'answer'])
        self.assertEqual(indexed['metadatas'][0]['turn_index'], 1)

    def test_empty_fork_does_not_reindex_parent(self):
        chat_history.fork('aaaaaaaa', 'bbbbbbbb', 2)
        collection = Mock()
        collection.get.return_value = {'ids': [], 'documents': [], 'metadatas': []}
        with patch.object(rag, '_get_collection', return_value=collection):
            rag.index_session('bbbbbbbb', 'branch', chat_history.load('bbbbbbbb'))
        collection.upsert.assert_not_called()

    def test_late_background_index_cannot_recreate_deleted_fork_chunks(self):
        chat_history.fork('aaaaaaaa', 'bbbbbbbb', 2)
        snapshot = chat_history.load('bbbbbbbb')
        chat_history.delete('bbbbbbbb')
        with patch.object(rag, '_get_collection') as get_collection:
            rag.index_session('bbbbbbbb', 'deleted', snapshot)
        get_collection.assert_not_called()

    def test_kiro_fork_preserves_rewind_boundary_and_shared_history(self):
        source = acp.ACPSession('aaaaaaaa')
        source.context_snapshot = {'servers': {'fixture': {'command': 'fixture'}}, 'documents': []}
        source._load_history()
        source.proc = Mock()
        source.proc.poll.return_value = None
        source.execute_command = Mock(side_effect=[
            {'success': True, 'data': {'turns': [
                {'logIndex': 2, 'label': 'two'}, {'logIndex': 1, 'label': 'one'},
            ]}},
            {'success': True, 'data': {'sessionId': 'kiro-fork'}},
        ])
        with patch.object(threading.Thread, 'start'):
            manager = acp.ACPManager()
            manager.sessions[source.id] = source
            fork_id = manager.fork_at_turn(source.id, 2)
        self.assertEqual(source.execute_command.call_args.args, ('rewind', {'value': '1'}))
        self.assertEqual(chat_history.load(fork_id), self.events[:2])
        self.assertEqual(len(chat_history.read_local(fork_id)), 1)
        self.assertEqual(manager.sessions[fork_id].acp_session_id, 'kiro-fork')
        self.assertEqual(acp.get_parent(fork_id), source.id)
        self.assertEqual(manager.sessions[fork_id].context_snapshot, source.context_snapshot)
        manager.sessions[fork_id].context_snapshot['servers'].clear()
        self.assertIn('fixture', source.context_snapshot['servers'])

    def test_opencode_uses_exact_native_user_boundary_including_continuations(self):
        source = acp.ACPSession('aaaaaaaa', backend='opencode')
        source.acp_session_id = 'ses_original'
        history = [prompt('repeat'), {'type': 'continuation', 'text': '[CONTINUATION] continue'}, prompt('repeat')]
        native = [{'info': {'id': f'msg_{i}', 'role': 'user'}, 'parts': [{'type': 'text', 'text': e['text']}]}
                  for i, e in enumerate(history)]
        source.opencode_request = Mock(side_effect=[native, {'id': 'ses_fork'}])
        self.assertEqual(source.fork_opencode(history, 2), 'ses_fork')
        self.assertEqual(source.opencode_request.call_args.args,
                         ('POST', '/session/ses_original/fork', {'messageID': 'msg_2'}))
        source.opencode_request = Mock(return_value=native)
        with self.assertRaises(ValueError):
            source.fork_opencode([prompt('different')], 0)
        self.assertEqual(source.opencode_request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
