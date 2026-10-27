import unittest
from unittest.mock import Mock, patch


class ConversationSearchTests(unittest.TestCase):
    def test_semantic_actor_filter_is_applied_before_retrieval(self):
        from src.services import rag
        collection = Mock()
        collection.count.return_value = 2
        collection.query.return_value = {'ids': [[]], 'metadatas': [[]], 'documents': [[]]}
        with patch.object(rag, '_get_collection', return_value=collection):
            self.assertEqual(rag.search('needle', session_ids=['aaaaaaaa'], roles=['user']), [])
        self.assertEqual(collection.query.call_args.kwargs['where'], {
            '$and': [{'session_id': {'$in': ['aaaaaaaa']}}, {'role': {'$in': ['user']}}]
        })

    def test_quoted_search_is_exact_case_insensitive_and_scoped(self):
        from src.services import rag
        identifier = 'a5055f9f-1234-5678-abcd-0123456789ab'
        collection = Mock()
        collection.count.return_value = 3
        collection.get.return_value = {
            'ids': ['wrong', 'embedded', 'exact'],
            'documents': [identifier[:-1] + 'c', 'an unrelated UUID', 'Found ' + identifier.upper()],
            'metadatas': [{'session_id': sid, 'turn_index': 0} for sid in ['wrong', 'embedded', 'exact']],
        }
        with patch.object(rag, '_get_collection', return_value=collection):
            hits = rag.search('"' + identifier + '"', session_ids=['wrong', 'embedded', 'exact'])
        self.assertEqual([hit['session_id'] for hit in hits], ['exact'])
        collection.query.assert_not_called()
        self.assertEqual(collection.get.call_args.kwargs['where'], {'session_id': {'$in': ['wrong', 'embedded', 'exact']}})

    def test_quoted_search_does_not_return_similar_results_when_absent(self):
        from src.services import rag
        collection = Mock()
        collection.count.return_value = 1
        collection.get.return_value = {'ids': ['other'], 'documents': ['a5055f9f-1234-5678-abcd-0123456789ac'], 'metadatas': [{'session_id': 'other', 'turn_index': 0}]}
        with patch.object(rag, '_get_collection', return_value=collection):
            self.assertEqual(rag.search('"a5055f9f-1234-5678-abcd-0123456789ab"'), [])
        collection.query.assert_not_called()

    def test_quotes_are_opt_in_and_support_smart_quotes(self):
        from src.services import rag
        self.assertIsNone(rag.exact_search_phrase('a5055f9f-1234-5678-abcd-0123456789ab'))
        self.assertEqual(rag.exact_search_phrase('“Connection refused”'), 'connection refused')
        self.assertEqual(rag.exact_search_phrase('"Connection refused"'), 'connection refused')
        self.assertIsNone(rag.exact_search_phrase('"unfinished'))

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
            hits = rag.search('a5055f9f-1234-5678-abcd-0123456789ab', session_ids=['a', 'b'], distinct_sessions=True)
        self.assertEqual(collection.query.call_args.kwargs['where'], {'session_id': {'$in': ['a', 'b']}})
        self.assertEqual([hit['session_id'] for hit in hits], ['a', 'b'])

    def test_empty_scope_never_searches_all_conversations(self):
        from src.services import rag
        with patch.object(rag, '_get_collection') as get_collection:
            self.assertEqual(rag.search('tent', session_ids=[]), [])
            get_collection.assert_not_called()
