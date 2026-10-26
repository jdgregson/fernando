import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    'browser_cdp', Path(__file__).resolve().parents[1] / 'mcp_servers' / 'browser_cdp.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.browser = module.BrowserCDP()
        self.browser.sessions['session'] = 'target'

    def event(self, method, params):
        self.browser.record({'sessionId': 'session', 'method': method, 'params': params})

    def test_redirects_and_wire_headers_are_preserved(self):
        self.event('Network.requestWillBeSent', {'requestId': '1', 'request': {'url': 'https://a.test/a', 'method': 'GET'}})
        self.event('Network.requestWillBeSentExtraInfo', {'requestId': '1', 'headers': {'Cookie': 'a=b'}})
        self.event('Network.requestWillBeSent', {'requestId': '1', 'redirectResponse': {'status': 302}, 'request': {'url': 'https://a.test/b', 'method': 'GET'}})
        self.event('Network.responseReceived', {'requestId': '1', 'response': {'status': 200}})
        self.event('Network.loadingFinished', {'requestId': '1', 'encodedDataLength': 10})
        item = self.browser.request('session:1')
        self.assertEqual(len(item['events']), 5)
        self.assertEqual(item['request']['url'], 'https://a.test/b')
        self.assertTrue(item['finished'])
        self.assertEqual(item['events'][1]['params']['headers']['Cookie'], 'a=b')

    def test_event_bound_and_cursor(self):
        for index in range(4100):
            self.event('Network.dataReceived', {'requestId': str(index)})
        result = self.browser.get_events(limit=2)
        self.assertEqual(result['dropped_events'], 100)
        self.assertEqual(result['cursor'], 102)
        self.assertTrue(result['has_more'])

    async def test_cross_origin_replay_rejected_before_network(self):
        self.event('Network.requestWillBeSent', {'requestId': '1', 'request': {'url': 'https://a.test/private', 'method': 'POST', 'headers': {'Authorization': 'secret'}}})
        with self.assertRaisesRegex(ValueError, 'original HTTP'):
            await self.browser.replay('session:1', url='https://b.test/')

    async def test_native_replay_rejects_edits(self):
        self.event('Network.requestWillBeSent', {'requestId': '1', 'type': 'XHR', 'request': {'url': 'https://a.test/'}})
        with self.assertRaisesRegex(ValueError, 'does not accept edits'):
            await self.browser.replay('session:1', mode='xhr', body='replacement')

    async def test_wait_requires_exactly_one_condition(self):
        with self.assertRaises(ValueError):
            await self.browser.wait()
        with self.assertRaises(ValueError):
            await self.browser.wait(expression='true', url='/')


FIXTURE = '''
import base64
import hashlib
import http.server
import json
import sys
import threading
import time

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/ws':
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            header = self.rfile.read(2)
            size = header[1] & 127
            mask = self.rfile.read(4)
            payload = self.rfile.read(size)
            data = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            self.wfile.write(bytes([129, len(data)]) + data)
            self.wfile.flush()
            return
        if self.path == '/worker.js':
            body = b"fetch('/worker-request').then(r=>r.text()).then(t=>postMessage(t));"
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript')
        elif self.path == '/':
            body = b'<html><body>fixture</body></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Set-Cookie', 'fixture=present; HttpOnly; SameSite=Lax')
        elif self.path == '/redirect':
            body = b''
            self.send_response(302)
            self.send_header('Location', '/result')
        else:
            if self.path == '/slow':
                time.sleep(0.3)
            body = json.dumps({'path': self.path, 'cookie': self.headers.get('Cookie')}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()
        body = json.dumps({'path': self.path, 'body': data, 'cookie': self.headers.get('Cookie'), 'custom': self.headers.get('X-Fixture')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(server.server_port, flush=True)
sys.stdin.read()
server.shutdown()
'''


@unittest.skipUnless(os.environ.get('FERNANDO_CDP_LIVE') == '1', 'requires desktop Chrome')
class LiveBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_tool_registration_and_dispatch(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server = Path(__file__).resolve().parents[1] / 'mcp_servers' / 'desktop_mcp.py'
        parameters = StdioServerParameters(command=sys.executable, args=[str(server)])
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                self.assertTrue({'browser_capture', 'browser_network', 'browser_request',
                                 'browser_cdp', 'browser_events', 'browser_wait', 'browser_replay'} <= names)
                result = await session.call_tool('browser_capture', {'action': 'start'})
                self.assertFalse(result.isError, result.content)
                self.assertTrue(json.loads(result.content[0].text)['running'])
                result = await session.call_tool('browser_cdp', {'method': 'Browser.getVersion'})
                self.assertFalse(result.isError, result.content)
                self.assertIn('Chrome', json.loads(result.content[0].text)['product'])
                result = await session.call_tool('browser_network', {'limit': 0})
                self.assertTrue(result.isError)
                result = await session.call_tool('browser_capture', {'action': 'stop'})
                self.assertFalse(result.isError, result.content)

    async def test_real_capture_wait_body_replay_and_cleanup(self):
        fixture = await asyncio.create_subprocess_exec(
            'docker', 'exec', '-i', '--user', '1000:1000', 'fernando-desktop',
            'python3', '-u', '-c', FIXTURE,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        browser = module.BrowserCDP()
        target = None
        try:
            port = int(await asyncio.wait_for(fixture.stdout.readline(), 10))
            origin = f'http://127.0.0.1:{port}'
            await browser.start()
            target = (await browser.command('Target.createTarget', {'url': 'about:blank'}))['targetId']
            async with asyncio.timeout(10):
                while not browser.targets.get(target, {}).get('network_enabled'):
                    await asyncio.sleep(0.05)
            await browser.command('Page.navigate', {'url': origin}, browser.session(target))
            await browser.wait(target_id=target, expression="document.body?.innerText === 'fixture'", timeout=10)
            cursor = browser.sequence
            await browser.evaluate(target, "fetch('/slow'); undefined")
            match = await browser.wait(url='/slow', target_id=target, after=cursor, timeout=5)
            request_id = match['request']['id']
            body = await browser.body(request_id, 'response')
            self.assertIn('fixture=present', body['content'])
            self.assertTrue(any(e['method'] == 'Network.requestWillBeSentExtraInfo' for e in browser.request(request_id)['events']))
            cursor = browser.sequence
            await browser.evaluate(target, "fetch('/post',{method:'POST',headers:{'Content-Type':'application/json','X-Fixture':'original'},body:JSON.stringify({a:1})}).then(r=>r.text())")
            post = await browser.wait(url='/post', after=cursor, timeout=5)
            post_id = post['request']['id']
            original = await browser.body(post_id, 'request')
            self.assertEqual(json.loads(original['content']), {'a': 1})
            replay = await browser.replay(post_id, url=origin + '/edited', headers={'X-Fixture': 'edited'}, body='replacement')
            edited = await browser.wait(url='/edited', after=replay['after_cursor'], timeout=5)
            result = json.loads((await browser.body(edited['request']['id'], 'response'))['content'])
            self.assertEqual(result['body'], 'replacement')
            self.assertEqual(result['custom'], 'edited')
            self.assertIn('fixture=present', result['cookie'])
            cursor = browser.sequence
            await browser.evaluate(target, "new Promise(resolve=>{const x=new XMLHttpRequest();x.open('GET','/xhr');x.onload=()=>resolve(x.status);x.send()})")
            xhr = await browser.wait(url='/xhr', after=cursor, timeout=5)
            cursor = browser.sequence
            await browser.replay(xhr['request']['id'], mode='xhr')
            await browser.wait(url='/xhr', after=cursor, timeout=5)
            cursor = browser.sequence
            await browser.evaluate(target, "fetch('/redirect').then(r=>r.text())")
            redirect = await browser.wait(url='/result', after=cursor, timeout=5)
            self.assertTrue(any('redirectResponse' in e.get('params', {}) for e in browser.request(redirect['request']['id'])['events']))
            with self.assertRaises(TimeoutError):
                await browser.wait(target_id=target, expression='false', timeout=0.2)
            await browser.command('Runtime.enable', session_id=browser.session(target))
            cursor = browser.sequence
            await browser.evaluate(target, "console.log('cdp-fixture'); true")
            self.assertTrue(browser.get_events(after=cursor, method='Runtime.consoleAPICalled')['events'])
            cursor = browser.sequence
            await browser.evaluate(target, "new Promise((resolve,reject)=>{window.fixtureWorker=new Worker('/worker.js');fixtureWorker.onmessage=e=>resolve(e.data);fixtureWorker.onerror=reject})")
            worker = await browser.wait(url='/worker-request', after=cursor, timeout=5)
            self.assertEqual(worker['request']['status'], 200)
            self.assertTrue(any(t['type'] == 'worker' for t in browser.targets.values()))
            cursor = browser.sequence
            result = await browser.evaluate(target, "new Promise((resolve,reject)=>{const w=new WebSocket(location.origin.replace('http:','ws:')+'/ws');w.onopen=()=>w.send('fixture-frame');w.onmessage=e=>{resolve(e.data);w.close()};w.onerror=reject})")
            self.assertEqual(result['value'], 'fixture-frame')
            events = browser.get_events(after=cursor, method='Network.webSocket')['events']
            self.assertTrue(any(e['method'] == 'Network.webSocketFrameSent' for e in events))
            self.assertTrue(any(e['method'] == 'Network.webSocketFrameReceived' for e in events))
            self.assertFalse(browser.errors, list(browser.errors))
            self.assertTrue(browser.status()['running'])
        finally:
            if target is not None and browser.status()['running']:
                await browser.command('Target.closeTarget', {'targetId': target})
            await browser.stop()
            fixture.stdin.close()
            await asyncio.wait_for(fixture.wait(), 5)
        self.assertFalse(browser.status()['running'])


if __name__ == '__main__':
    unittest.main()
