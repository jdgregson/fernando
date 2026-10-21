import ast
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.services import conversation_search as search
from src.services.history_search_worker import scan


def chunk(text):
    return {'method': 'session/update', 'params': {'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}}


class HistorySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        search.cancel('test-client')
        self.temp.cleanup()

    def write(self, sid, events):
        (self.root / (sid + '.jsonl')).write_text(''.join(json.dumps(event) + '\n' for event in events))

    def test_streaming_unicode_and_phrase_across_events(self):
        self.write('aaaaaaaa', [chunk('We saw Caf'), chunk('é and a snow'), chunk('man today')])
        result = scan(self.root, 'aaaaaaaa', ['café and a snowman'])
        self.assertIsNotNone(result)
        self.assertEqual(result['event_index'], 2)

    def test_message_boundaries_do_not_create_false_phrases(self):
        self.write('aaaaaaaa', [chunk('a snow'), {'result': {'stopReason': 'end_turn'}}, chunk('man')])
        self.assertIsNone(scan(self.root, 'aaaaaaaa', ['snowman']))

    def test_tool_inputs_outputs_and_escaped_text(self):
        for field in ('rawInput', 'rawOutput', 'content'):
            with self.subTest(field=field):
                self.write('aaaaaaaa', [{'params': {'update': {'sessionUpdate': 'tool_call_update', field: {'nested': ['one\ntwo café abcde']}}}}])
                result = scan(self.root, 'aaaaaaaa', ['one\ntwo café abcde'])
                self.assertEqual(result['match'], 'Tool content')

    def test_tool_json_representation_is_searchable(self):
        self.write('aaaaaaaa', [{'params': {'update': {'sessionUpdate': 'tool_call', 'rawInput': {'command': 'hello world'}}}}])
        self.assertIsNotNone(scan(self.root, 'aaaaaaaa', ['"command": "hello world"']))

    def test_forks_include_only_inherited_prefix(self):
        self.write('aaaaaaaa', [chunk('inherited phrase'), {'result': {'stopReason': 'end_turn'}}, chunk('parent only secret')])
        self.write('bbbbbbbb', [{'type': 'history_reference', 'session_id': 'aaaaaaaa', 'event_count': 2}, chunk('child text')])
        self.assertIsNotNone(scan(self.root, 'bbbbbbbb', ['inherited phrase']))
        self.assertIsNone(scan(self.root, 'bbbbbbbb', ['parent only secret']))
        self.assertIsNotNone(scan(self.root, 'bbbbbbbb', ['child text']))

    def test_partial_append_is_not_parsed(self):
        self.write('aaaaaaaa', [chunk('needle')])
        with (self.root / 'aaaaaaaa.jsonl').open('ab') as stream:
            stream.write(b'{"params":')
        self.assertIsNone(scan(self.root, 'aaaaaaaa', ['absent']))

    def test_group_filter_parsing_and_authoritative_scope(self):
        self.assertEqual(search.parse_query('group:a5055f9f "group:literal quoted phrase"'), ('"group:literal quoted phrase"', 'a5055f9f'))
        self.assertEqual(search.parse_query('"some phrase" group:a5055f9f'), ('"some phrase"', 'a5055f9f'))
        sessions = [{'id': 'aaaaaaaa'}, {'id': 'bbbbbbbb'}]
        self.assertEqual(search.scoped_sessions(sessions, {'chat:aaaaaaaa': 'a5055f9f'}, 'a5055f9f'), [sessions[0]])
        self.assertEqual(search.scoped_sessions(sessions, {}, 'a5055f9f'), [])
        self.assertEqual(search.scoped_sessions(sessions, {'chat:aaaaaaaa': 'a5055f9f'}, '__ungrouped__'), [sessions[1]])
        with self.assertRaises(ValueError):
            search.parse_query('group:../../etc "phrase"')

    def test_real_workers_emit_individual_matches_without_twenty_result_limit(self):
        sessions = [{'id': f'{i:08x}', 'name': f'Chat {i}'} for i in range(24)]
        for session in sessions:
            self.write(session['id'], [chunk('real needle')])
        replies = []
        with patch.object(search.chat_history, 'HISTORY_DIR', self.root), patch.object(search, 'rag_search') as rag:
            thread = search.start('test-client', '"real needle"', sessions, replies.append)
            thread.join(15)
            self.assertFalse(thread.is_alive())
            rag.assert_not_called()
        matches = [reply for reply in replies if reply.get('sessions')]
        self.assertEqual(len(matches), 24)
        self.assertTrue(all(len(reply['sessions']) == 1 for reply in matches))
        self.assertEqual(replies[-1]['state'], 'complete')
        self.assertLess(replies.index(matches[0]), len(replies) - 1)

    def test_cancel_stops_work_and_releases_worker_capacity(self):
        self.write('aaaaaaaa', [chunk('never match this') for _ in range(100000)])
        replies = []
        started = threading.Event()
        def send(reply):
            replies.append(reply)
            if reply.get('events'):
                started.set()
        with patch.object(search.chat_history, 'HISTORY_DIR', self.root):
            thread = search.start('test-client', '"absent"', [{'id': 'aaaaaaaa', 'name': 'A'}], send, request_id=1)
            self.assertTrue(started.wait(3))
            search.cancel('test-client', 2)
            size = len(replies)
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(replies), size)
        acquired = [search._slots.acquire(blocking=False) for _ in range(search.WORKERS)]
        self.assertTrue(all(acquired))
        for item in acquired:
            if item:
                search._slots.release()

    def test_stale_request_cannot_replace_newer_search(self):
        search.cancel('test-client', 3)
        self.assertIsNone(search.start('test-client', '"phrase"', [], lambda reply: self.fail(), request_id=2))

    def test_worker_failure_is_visible(self):
        (self.root / 'aaaaaaaa.jsonl').write_text('invalid json\n')
        replies = []
        with patch.object(search.chat_history, 'HISTORY_DIR', self.root):
            thread = search.start('test-client', '"needle"', [{'id': 'aaaaaaaa', 'name': 'A'}], replies.append)
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies[-1]['state'], 'error')

    def test_socket_handler_authenticates_and_filters_before_dispatch(self):
        manager = Mock()
        manager.list_sessions.return_value = [{'id': 'aaaaaaaa', 'name': 'A'}, {'id': 'bbbbbbbb', 'name': 'B'}]
        manager.list_archived.return_value = [{'id': 'cccccccc', 'name': 'C'}]
        service = Mock(wraps=search)
        service.start = Mock()
        scope = {'validate_csrf': Mock(return_value=True), 'request': SimpleNamespace(sid='socket'), 'conversation_search': service, 'acp_manager': manager, 'emit': Mock(), 'socketio': Mock()}
        tree = ast.parse(Path('src/routes/websocket.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ('acp_search_conversations', 'acp_cancel_conversation_search'):
                node.decorator_list = []
                exec(compile(ast.Module(body=[node], type_ignores=[]), '<handler>', 'exec'), scope)
        from src.services import groups
        with patch.object(groups, 'get_session_groups', return_value={'chat:aaaaaaaa': 'a5055f9f', 'chat:cccccccc': 'a5055f9f'}):
            scope['acp_search_conversations']({'query': 'group:a5055f9f "phrase"', 'scope': 'active', 'request_id': 1})
            self.assertEqual(service.start.call_args.args[2], [{'id': 'aaaaaaaa', 'name': 'A'}])
            service.start.call_args.args[3]({'sessions': []})
            self.assertEqual(scope['socketio'].emit.call_args.kwargs['to'], 'socket')
            scope['acp_search_conversations']({'query': 'group:a5055f9f "phrase"', 'scope': 'archived', 'request_id': 2})
            self.assertEqual(service.start.call_args.args[2], [{'id': 'cccccccc', 'name': 'C'}])
            service.start.reset_mock()
            scope['validate_csrf'].return_value = False
            scope['acp_search_conversations']({'query': 'phrase', 'scope': 'active', 'request_id': 3})
            scope['acp_cancel_conversation_search']({'request_id': 3})
            service.start.assert_not_called()
            service.cancel.assert_not_called()
