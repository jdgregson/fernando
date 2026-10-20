import unittest
from unittest.mock import Mock, patch


class ConversationSearchTests(unittest.TestCase):
    def test_scope_is_applied_before_retrieval_and_sessions_are_deduplicated(self):
        from src.services import rag
        collection = Mock()
        collection.count.return_value = 3
        collection.query.return_value = {
            'ids': [['a0', 'a1', 'b0']],
            'metadatas': [[{'session_id': 'a', 'turn_index': 0}, {'session_id': 'a', 'turn_index': 1}, {'session_id': 'b', 'turn_index': 0}]],
            'documents': [['first', 'second', 'third']],
            'distances': [[.1, .2, .3]],
        }
        with patch.object(rag, '_get_collection', return_value=collection):
            hits = rag.search('tent', session_ids=['a', 'b'], distinct_sessions=True)
        self.assertEqual(collection.query.call_args.kwargs['where'], {'session_id': {'$in': ['a', 'b']}})
        self.assertEqual([hit['session_id'] for hit in hits], ['a', 'b'])

    def test_empty_scope_never_searches_all_conversations(self):
        from src.services import rag
        with patch.object(rag, '_get_collection') as get_collection:
            self.assertEqual(rag.search('tent', session_ids=[]), [])
            get_collection.assert_not_called()
