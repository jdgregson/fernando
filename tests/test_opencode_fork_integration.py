import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import psutil
import requests

from src.services import acp, chat_history


@unittest.skipUnless(os.environ.get('RUN_OPENCODE_INTEGRATION') == '1', 'Explicit native OpenCode integration run')
class OpenCodeForkIntegration(unittest.TestCase):
    def test_fork_during_reply_then_resume_with_native_and_rich_history(self):
        held = threading.Event()
        release = threading.Event()
        received = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(body)
                text = json.dumps(body['messages'][-1]['content'])
                if 'hold-this-reply' in text:
                    held.set()
                    release.wait(120)
                if body.get('stream'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    for delta, finish in (({'role': 'assistant', 'content': 'fixture answer'}, None), ({}, 'stop')):
                        event = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1,
                                 'model': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                        'model': 'fixture', 'choices': [{'index': 0, 'message': {'role': 'assistant',
                        'content': 'fixture answer'}, 'finish_reason': 'stop'}]}).encode())

        provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        self.addCleanup(provider.server_close)
        self.addCleanup(provider.shutdown)
        self.addCleanup(release.set)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            history = root / 'history'
            history.mkdir()
            for module, key, value in ((chat_history, 'HISTORY_DIR', history),
                                       (acp, 'HISTORY_DIR', str(history))):
                stack.enter_context(patch.object(module, key, value))
            stack.enter_context(patch.object(acp.ACPSession, '_index_rag_background'))
            config = {'model': 'fixture/fixture', 'small_model': 'fixture/fixture',
                      'provider': {'fixture': {'npm': '@ai-sdk/openai-compatible',
                          'options': {'baseURL': f'http://127.0.0.1:{provider.server_port}/v1', 'apiKey': 'fixture'},
                          'models': {'fixture': {'name': 'fixture', 'limit': {'context': 200000, 'output': 1024}}}}}}
            original_popen = subprocess.Popen

            def spawn(*args, **kwargs):
                env = dict(kwargs['env'])
                env.update({'HOME': directory, 'XDG_DATA_HOME': str(root / 'data'),
                            'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_STATE_HOME': str(root / 'state'),
                            'OPENCODE_CONFIG_CONTENT': json.dumps(config)})
                kwargs['env'] = env
                return original_popen(*args, **kwargs)

            stack.enter_context(patch.object(acp.subprocess, 'Popen', side_effect=spawn))

            def stop_session(session):
                process = session.proc
                session.unload()
                if process:
                    process.wait(timeout=10)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()

            source = acp.ACPSession('aaaaaaaa', backend='opencode')
            source.model = 'fixture/fixture'
            stack.callback(stop_session, source)
            source.start()
            source.ready = True
            listeners = [c.laddr.port for c in psutil.Process(source.proc.pid).net_connections(kind='tcp')
                         if c.status == psutil.CONN_LISTEN]
            self.assertEqual(len(listeners), 1)
            response = requests.get(f'http://127.0.0.1:{listeners[0]}/session', timeout=5)
            self.assertEqual(response.status_code, 401)
            for text in ('one', 'two'):
                source.send_prompt(text)
                self.wait_for(lambda: not source._is_prompting)
            source.send_prompt('hold-this-reply')
            self.assertTrue(held.wait(45), 'OpenCode did not start the held provider request')
            self.assertTrue(source._is_prompting)
            with chat_history.lock:
                source._save_history()
                snapshot = source.history[:source._flushed]
            boundaries = [i for i, event in enumerate(snapshot) if event.get('type') == 'user_prompt']
            self.assertEqual(len(boundaries), 3)
            for turn, expected_users in ((0, 0), (1, 1)):
                earlier_id = source.fork_opencode(snapshot, boundaries[turn])
                earlier = source.opencode_request('GET', '/session/' + earlier_id + '/message')
                self.assertEqual(sum(m['info']['role'] == 'user' for m in earlier), expected_users)
            native_id = source.fork_opencode(snapshot, boundaries[2])
            self.assertTrue(source._is_prompting)
            native = source.opencode_request('GET', '/session/' + native_id + '/message')
            self.assertEqual(sum(m['info']['role'] == 'user' for m in native), 2)
            chat_history.fork(source.id, 'bbbbbbbb', boundaries[2])
            child = acp.ACPSession('bbbbbbbb', backend='opencode')
            child.model = source.model
            stack.callback(stop_session, child)
            child.load(native_id)
            child.ready = True
            self.assertEqual(child.history, snapshot[:boundaries[2]])
            self.assertEqual(len(chat_history.read_local(child.id)), 1)
            child.send_prompt('branch-message')
            self.wait_for(lambda: not child._is_prompting)
            branch_request = next(body for body in received if 'branch-message' in json.dumps(body['messages'][-1]))
            branch_context = json.dumps(branch_request['messages'])
            self.assertIn('one', branch_context)
            self.assertIn('two', branch_context)
            self.assertNotIn('hold-this-reply', branch_context)
            self.assertTrue(source._is_prompting)
            release.set()
            self.wait_for(lambda: not source._is_prompting)
            self.assertNotIn('hold-this-reply', json.dumps(chat_history.load(child.id)))
            self.assertIn('branch-message', json.dumps(chat_history.load(child.id)))

    def wait_for(self, predicate):
        deadline = time.monotonic() + 45
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(predicate(), 'Timed out waiting for native OpenCode')


if __name__ == '__main__':
    unittest.main()
