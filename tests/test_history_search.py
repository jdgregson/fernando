import ast
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.services import conversation_search as search
from src.services.history_search_worker import scan, scan_matches


def chunk(text):
    return {'method': 'session/update', 'params': {'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}}


class HistorySearchTests(unittest.TestCase):
    def test_all_matches_include_every_occurrence_across_streamed_messages(self):
        self.write('aaaaaaaa', [chunk('needle needle nee'), chunk('dle'), {'result': {'stopReason': 'end_turn'}}, {'type': 'user_prompt', 'text': 'needle'}, chunk('needle')])
        matches = list(scan_matches(self.root, 'aaaaaaaa', ['needle']))
        self.assertEqual([match['event_index'] for match in matches], [0, 0, 1, 3, 4])
        self.assertEqual([match['match_occurrence'] for match in matches], [0, 1, 2, 0, 0])
        agent_only = list(scan_matches(self.root, 'aaaaaaaa', ['needle'], ['user']))
        self.assertEqual(len(agent_only), 4)
        user_only = list(scan_matches(self.root, 'aaaaaaaa', ['needle'], ['agent']))
        self.assertEqual([match['event_index'] for match in user_only], [3])

    def test_all_tool_matches_deduplicate_json_and_repeated_output_updates(self):
        update = {'params': {'update': {'sessionUpdate': 'tool_call_update', 'toolCallId': 'tool-1', 'rawOutput': {'text': 'needle needle'}, 'content': [{'type': 'text', 'text': 'needle needle'}]}}}
        self.write('aaaaaaaa', [update, update])
        self.assertEqual(len(list(scan_matches(self.root, 'aaaaaaaa', ['needle']))), 2)
        self.assertEqual(list(scan_matches(self.root, 'aaaaaaaa', ['needle'], ['tools'])), [])

    def test_worker_streams_all_ten_matches_for_a_targeted_chat(self):
        self.write('aaaaaaaa', [{'type': 'user_prompt', 'text': 'needle'} for _ in range(10)])
        replies = []
        with patch.object(search.chat_history, 'HISTORY_DIR', self.root):
            thread = search.start('test-client', '"needle"', [{'id': 'aaaaaaaa', 'name': 'Other'}], replies.append, all_matches=True)
            thread.join(10)
        matches = [s for reply in replies for s in reply.get('sessions', [])]
        self.assertEqual([match['event_index'] for match in matches], list(range(10)))
        self.assertEqual(replies[-1]['state'], 'complete')

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

    def test_exclusions_reach_workers_and_do_not_hide_allowed_matches(self):
        sessions = [{'id': sid, 'name': name} for sid, name in (
            ('aaaaaaaa', 'Message'), ('bbbbbbbb', 'Tool'), ('cccccccc', 'Both'), ('dddddddd', 'my-test-1'))]
        text = chunk('my-test-1')
        tool = {'params': {'update': {'sessionUpdate': 'tool_call_update', 'rawOutput': 'my-test-1'}}}
        self.write('aaaaaaaa', [text])
        self.write('bbbbbbbb', [tool])
        self.write('cccccccc', [text, tool])
        for excluded, expected in (
            (['text'], {'bbbbbbbb', 'cccccccc', 'dddddddd'}),
            (['tools'], {'aaaaaaaa', 'cccccccc', 'dddddddd'}),
            (['text', 'tools'], {'dddddddd'}),
        ):
            with self.subTest(excluded=excluded):
                replies = []
                with patch.object(search.chat_history, 'HISTORY_DIR', self.root):
                    thread = search.start('test-client', '"my-test-1"', sessions, replies.append, excluded=excluded)
                    thread.join(10)
                    self.assertFalse(thread.is_alive())
                matches = [session for reply in replies for session in reply.get('sessions', [])]
                self.assertEqual({session['id'] for session in matches}, expected)
                self.assertEqual(replies[-1]['completed'], len(sessions))
                search.cancel('test-client')

    def test_text_exclusion_disables_semantic_text_results(self):
        self.write('aaaaaaaa', [chunk('needle')])
        for excluded, semantic_calls in ((['text'], 0), (['tools'], 1)):
            replies = []
            with patch.object(search.chat_history, 'HISTORY_DIR', self.root), patch.object(search, 'rag_search', return_value=[]) as rag:
                thread = search.start('test-client', 'needle', [{'id': 'aaaaaaaa', 'name': 'Other'}], replies.append, excluded=excluded)
                thread.join(10)
                self.assertFalse(thread.is_alive())
                self.assertEqual(rag.call_count, semantic_calls)
            matches = [session for reply in replies for session in reply.get('sessions', [])]
            self.assertEqual(len(matches), semantic_calls)
            search.cancel('test-client')

    def test_actor_exclusions_filter_messages_tools_and_semantic_roles(self):
        sessions = [{'id': sid, 'name': 'Other'} for sid in ('aaaaaaaa', 'bbbbbbbb', 'cccccccc')]
        self.write('aaaaaaaa', [{'type': 'user_prompt', 'text': 'needle'}])
        self.write('bbbbbbbb', [chunk('needle')])
        self.write('cccccccc', [{'params': {'update': {'sessionUpdate': 'tool_call_update', 'rawOutput': 'needle'}}}])
        for excluded, expected, roles in (
            (['agent'], {'aaaaaaaa'}, ['user']),
            (['user'], {'bbbbbbbb', 'cccccccc'}, ['assistant']),
            (['agent', 'user'], set(), []),
            (['user', 'tools'], {'bbbbbbbb'}, ['assistant']),
            (['agent', 'text'], set(), []),
        ):
            with self.subTest(excluded=excluded):
                replies = []
                with patch.object(search.chat_history, 'HISTORY_DIR', self.root), patch.object(search, 'rag_search', return_value=[]) as rag:
                    thread = search.start('test-client', 'needle', sessions, replies.append, excluded=excluded)
                    thread.join(10)
                    self.assertFalse(thread.is_alive())
                    if roles:
                        self.assertEqual(rag.call_args.kwargs['roles'], roles)
                    else:
                        rag.assert_not_called()
                self.assertEqual({s['id'] for reply in replies for s in reply.get('sessions', [])}, expected)
                self.assertEqual(replies[-1]['state'], 'complete')
                search.cancel('test-client')

    def test_group_filter_parsing_and_authoritative_scope(self):
        self.assertEqual(search.parse_query('group:a5055f9f "group:literal quoted phrase"'), ('"group:literal quoted phrase"', 'a5055f9f', []))
        self.assertEqual(search.parse_query('"some phrase" group:a5055f9f'), ('"some phrase"', 'a5055f9f', []))
        self.assertEqual(search.parse_query('-text "my-test-1" -tools group:a5055f9f'), ('"my-test-1"', 'a5055f9f', ['text', 'tools']))
        self.assertEqual(search.parse_query('"-text" -tools'), ('"-text"', None, ['tools']))
        self.assertEqual(search.parse_query('"-agent" -user'), ('"-agent"', None, ['user']))
        self.assertEqual(search.parse_query('needle -agent -user'), ('needle', None, ['agent', 'user']))
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

    def test_quoted_hyphenated_phrase_excludes_individual_tokens(self):
        sessions = [
            {'id': 'aaaaaaaa', 'name': 'my test 1'},
            {'id': 'bbbbbbbb', 'name': 'Exact content'},
            {'id': 'cccccccc', 'name': 'my-test-1'},
        ]
        self.write('aaaaaaaa', [chunk('my test 1; my-test-2; my-other-1')])
        self.write('bbbbbbbb', [chunk('Found my-'), chunk('test-1 here')])
        for query in ('"my-test-1"', '“my-test-1”'):
            with self.subTest(query=query):
                replies = []
                with patch.object(search.chat_history, 'HISTORY_DIR', self.root), patch.object(search, 'rag_search') as rag:
                    thread = search.start('test-client', query, sessions, replies.append)
                    thread.join(10)
                    self.assertFalse(thread.is_alive())
                    rag.assert_not_called()
                matches = [session for reply in replies for session in reply.get('sessions', [])]
                self.assertEqual({session['id'] for session in matches}, {'bbbbbbbb', 'cccccccc'})
                self.assertIn('my-test-1', next(session['snippet'] for session in matches if session['id'] == 'bbbbbbbb'))
                search.cancel('test-client')

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
        scope = {'validate_csrf': Mock(return_value=True), 'request': SimpleNamespace(sid='socket'), 'conversation_search': service, 'acp_manager': manager, 'emit': Mock(), 'socketio': Mock(), 're': __import__('re')}
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
            scope['acp_search_conversations']({'query': 'group:a5055f9f "phrase" -text -tools', 'scope': 'archived', 'request_id': 2})
            self.assertEqual(service.start.call_args.args[2], [{'id': 'cccccccc', 'name': 'C'}])
            self.assertEqual(service.start.call_args.args[1], '"phrase"')
            self.assertEqual(service.start.call_args.kwargs['excluded'], ['text', 'tools'])
            scope['acp_search_conversations']({'query': '"phrase" -user', 'scope': 'active', 'request_id': 3, 'session_id': 'aaaaaaaa'})
            self.assertEqual(service.start.call_args.args[2], [{'id': 'aaaaaaaa', 'name': 'A'}])
            self.assertTrue(service.start.call_args.kwargs['all_matches'])
            self.assertEqual(service.start.call_args.kwargs['excluded'], ['user'])
            scope['acp_search_conversations']({'query': '"phrase"', 'scope': 'archived', 'request_id': 4, 'session_id': 'aaaaaaaa'})
            self.assertEqual(service.start.call_args.args[2], [])
            service.start.reset_mock()
            scope['validate_csrf'].return_value = False
            scope['acp_search_conversations']({'query': 'phrase', 'scope': 'active', 'request_id': 3})
            scope['acp_cancel_conversation_search']({'request_id': 3})
            service.start.assert_not_called()
            service.cancel.assert_not_called()

    def test_title_match_does_not_prevent_a_navigable_content_match(self):
        self.write('aaaaaaaa', [{'type': 'user_prompt', 'text': 'needle'}, chunk('needle')])
        replies = []
        with patch.object(search.chat_history, 'HISTORY_DIR', self.root):
            thread = search.start('test-client', '"needle"', [{'id': 'aaaaaaaa', 'name': 'needle'}], replies.append, excluded=['user'])
            thread.join(10)
        matches = [s for reply in replies for s in reply.get('sessions', [])]
        self.assertEqual(matches[0]['match'], 'Title')
        self.assertEqual(matches[-1]['event_index'], 1)
        self.assertEqual(matches[-1]['source'], 'agent_message_chunk')

    def test_semantic_results_include_actor_and_turn_location(self):
        self.write('aaaaaaaa', [chunk('different wording')])
        replies = []
        hit = {'session_id': 'aaaaaaaa', 'snippet': 'different wording', 'turn_index': 7, 'role': 'assistant'}
        with patch.object(search.chat_history, 'HISTORY_DIR', self.root), patch.object(search, 'rag_search', return_value=[hit]):
            thread = search.start('test-client', 'needle', [{'id': 'aaaaaaaa', 'name': 'Other'}], replies.append)
            thread.join(10)
        matches = [s for reply in replies for s in reply.get('sessions', [])]
        self.assertEqual(matches[0]['turn_index'], 7)
        self.assertEqual(matches[0]['role'], 'assistant')

    def test_active_and_archived_replay_preserve_indices_through_chunk_collapse(self):
        from src.services import chat_history, acp, groups
        events = [{'type': 'user_prompt', 'text': 'hello'}, chunk('my-'), chunk('test-1'), {'result': {'stopReason': 'end_turn'}}]
        manager = Mock()
        session = SimpleNamespace(history=events, mark_read=Mock(), is_loaded=True, ready=True, model='test', backend='opencode')
        emitted = Mock()
        scope = {'validate_csrf': lambda data: True, 'logger': Mock(), 'request': SimpleNamespace(sid='socket'), 'acp_manager': manager, 'acp_subscribers': {}, 'acp_event_seq': {}, 'emit': emitted, 'replay_events': chat_history.replay_events, 'time': SimpleNamespace(time=lambda: 0)}
        tree = ast.parse(Path('src/routes/websocket.py').read_text())
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'acp_subscribe')
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<handler>', 'exec'), scope)
        for archived in (False, True):
            with self.subTest(archived=archived):
                manager.get_session.return_value = None if archived else session
                emitted.reset_mock()
                with patch.object(chat_history, 'reference', return_value={'event_count': 1}), patch.object(acp, 'load_history_file', return_value=events), patch.object(groups, 'get_session_groups', return_value={}), patch.object(groups, 'list_groups', return_value=[]):
                    scope['acp_subscribe']({'session_id': 'aaaaaaaa'})
                batch = next(call.args[1] for call in emitted.call_args_list if call.args[0] == 'acp_history_batch')
                merged = next(event for event in batch['events'] if event.get('params'))
                self.assertEqual((merged['history_start'], merged['history_end']), (1, 2))
                self.assertEqual(merged['params']['update']['content']['text'], 'my-test-1')
                self.assertTrue(any(event.get('type') == 'fork_boundary' for event in batch['events']))

    def test_live_events_use_persisted_indices_and_ignore_transient_events(self):
        event = chunk('needle')
        session = SimpleNamespace(history=[{'type': 'user_prompt'}, event, chunk('later')], _lock=threading.RLock())
        manager = Mock()
        manager.get_session.return_value = session
        socket = Mock()
        scope = {'acp_manager': manager, 'acp_event_seq': {}, 'acp_subscribers': {'aaaaaaaa': {'socket'}}, 'logger': Mock(), 'socketio': socket}
        tree = ast.parse(Path('src/routes/websocket.py').read_text())
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'acp_on_event')
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<handler>', 'exec'), scope)
        scope['acp_on_event']('aaaaaaaa', event)
        sent = socket.emit.call_args.args[1]['event']
        self.assertEqual((sent['history_start'], sent['history_end']), (1, 1))
        self.assertNotIn('history_start', event)
        scope['acp_on_event']('aaaaaaaa', {'type': 'session_ready'})
        self.assertNotIn('history_start', socket.emit.call_args.args[1]['event'])
