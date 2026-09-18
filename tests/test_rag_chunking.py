import unittest
from unittest.mock import Mock, patch

from src.services import rag
from src.services.rag_chunking import chunk_text, _tokenizer, MAX_TOKENS


class ChunkingTests(unittest.TestCase):
    def test_sentences_lists_and_roles_do_not_blend(self):
        text = '[Pane context: pane1: this chat]\nFirst complete sentence. Another independent sentence!\n- Refactor modules\n- Fix swimming'
        self.assertEqual(chunk_text(text), ['First complete sentence.', 'Another independent sentence!', '- Refactor modules', '- Fix swimming'])

    def test_long_unicode_text_is_bounded_and_tail_survives(self):
        text = ' '.join(f'東京 café_{i}' for i in range(400)) + ' unique_tail'
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(chunks[-1].endswith('unique_tail'))
        for chunk in chunks:
            self.assertLessEqual(len(_tokenizer().encode(chunk, add_special_tokens=False).ids), MAX_TOKENS + 2)
        for word in text.split():
            self.assertTrue(any(word in chunk for chunk in chunks), word)

    def test_reindex_skips_unchanged_embeddings_and_removes_stale_chunks(self):
        collection = Mock()
        meta = {'session_id': 'aaaaaaaa', 'session_name': 'test', 'turn_index': 0, 'role': 'user'}
        collection.get.return_value = {'ids': ['aaaaaaaa_0_user_0', 'old_chunk'], 'documents': ['Hello.', 'stale'], 'metadatas': [meta, meta]}
        with patch.object(rag, '_get_collection', return_value=collection), patch('src.services.chat_history.inherited_count', return_value=0):
            rag._index_session('aaaaaaaa', 'test', [{'type': 'user_prompt', 'text': 'Hello.'}])
        collection.upsert.assert_not_called()
        collection.delete.assert_called_once_with(ids=['old_chunk'])
