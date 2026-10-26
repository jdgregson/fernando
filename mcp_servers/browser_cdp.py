import asyncio
import json
import time
from collections import deque
from urllib.parse import urlsplit


RELAY = """
import json
import os
import sys
import threading
import urllib.request
import websocket

with urllib.request.urlopen('http://localhost:9222/json/version', timeout=10) as response:
    endpoint = json.load(response)['webSocketDebuggerUrl']
ws = websocket.create_connection(endpoint, timeout=10, suppress_origin=True)
ws.settimeout(None)

def receive():
    try:
        while True:
            message = ws.recv()
            if not message:
                return
            print(message, flush=True)
    finally:
        os._exit(1)

threading.Thread(target=receive, daemon=True).start()
for line in sys.stdin:
    ws.send(line.strip())
os._exit(0)
"""


class CDPError(RuntimeError):
    pass


class BrowserCDP:
    target_types = ('page', 'iframe', 'worker', 'shared_worker', 'service_worker', 'webview')

    def __init__(self):
        self.process = None
        self.reader = None
        self.pending = {}
        self.next_id = 0
        self.sequence = 0
        self.events = deque()
        self.event_bytes = 0
        self.dropped = 0
        self.sessions = {}
        self.targets = {}
        self.attaching = set()
        self.tasks = set()
        self.errors = deque(maxlen=30)
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.started = None

    async def start(self):
        async with self.lock:
            if self.process is not None:
                if self.process.returncode is not None or self.reader.done():
                    raise RuntimeError('CDP connection closed. Stop capture, then start it again.')
                return self.status()
            self.process = await asyncio.create_subprocess_exec(
                'docker', 'exec', '-i', '--user', '1000:1000', 'fernando-desktop',
                'python3', '-u', '-c', RELAY,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                limit=48 * 1024 * 1024,
            )
            self.started = time.time()
            self.reader = asyncio.create_task(self.read_messages())
            try:
                await self.command('Target.setAutoAttach', self.auto_attach_params())
                await self.command('Target.setDiscoverTargets', {'discover': True})
                result = await self.command('Target.getTargets')
                for target in result['targetInfos']:
                    self.schedule_attach(target)
                while self.tasks:
                    await asyncio.gather(*list(self.tasks))
            except BaseException:
                await self.stop()
                raise
            return self.status()

    def status(self):
        return {
            'running': self.process is not None and self.process.returncode is None
            and self.reader is not None and not self.reader.done(),
            'started_at': self.started, 'cursor': self.sequence,
            'retained_events': len(self.events), 'retained_bytes': self.event_bytes,
            'dropped_events': self.dropped, 'targets': list(self.targets.values()),
            'capture_errors': list(self.errors),
        }

    def auto_attach_params(self):
        return {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True,
                'filter': [{'type': kind, 'exclude': False} for kind in self.target_types]
                + [{'exclude': True}]}

    async def stop(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.process is not None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.process = None
        self.reader = None
        self.sessions.clear()
        self.targets.clear()
        self.attaching.clear()
        self.events.clear()
        self.event_bytes = 0
        return {'running': False, 'cleared': True}

    async def command(self, method, params=None, session_id=None, timeout=15):
        if self.process is None or self.process.returncode is not None or self.reader.done():
            raise RuntimeError('Start browser capture first.')
        self.next_id += 1
        command_id = self.next_id
        message = {'id': command_id, 'method': method, 'params': params or {}}
        if session_id:
            message['sessionId'] = session_id
        future = asyncio.get_running_loop().create_future()
        self.pending[command_id] = future
        try:
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(command_id, None)

    async def read_messages(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if 'id' in message:
                    future = self.pending.get(message['id'])
                    if future is not None and not future.done():
                        if 'error' in message:
                            future.set_exception(CDPError(json.dumps(message['error'])))
                        else:
                            future.set_result(message.get('result', {}))
                    continue
                method = message.get('method', '')
                params = message.get('params', {})
                if method == 'Target.attachedToTarget':
                    info = params['targetInfo']
                    session_id = params['sessionId']
                    self.sessions[session_id] = info['targetId']
                    self.targets[info['targetId']] = {**info, 'session_id': session_id, 'network_enabled': False}
                    task = asyncio.create_task(self.configure(info['targetId'], session_id))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                elif method == 'Target.targetInfoChanged':
                    info = params['targetInfo']
                    if info['targetId'] in self.targets:
                        self.targets[info['targetId']].update(info)
                elif method == 'Target.detachedFromTarget':
                    session = params['sessionId']
                    target = self.sessions.pop(session, None)
                    if target:
                        self.targets.pop(target, None)
                self.record(message)
        except (OSError, ValueError, asyncio.LimitOverrunError) as error:
            self.errors.append({'connection_error': str(error)})
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError('Browser CDP transport closed'))
            self.changed.set()

    def schedule_attach(self, target):
        target_id = target['targetId']
        if target['type'] not in self.target_types:
            return
        if target_id in self.targets or target_id in self.attaching:
            return
        self.attaching.add(target_id)
        task = asyncio.create_task(self.attach(target))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def attach(self, target):
        target_id = target['targetId']
        try:
            await self.command('Target.attachToTarget', {'targetId': target_id, 'flatten': True})
        except (CDPError, ConnectionError, asyncio.TimeoutError) as error:
            self.errors.append({'target_id': target_id, 'error': str(error)})
        finally:
            self.attaching.discard(target_id)

    async def configure(self, target_id, session_id):
        try:
            await self.command('Target.setAutoAttach', self.auto_attach_params(), session_id)
            await self.command('Network.enable', {
                'maxTotalBufferSize': 32 * 1024 * 1024,
                'maxResourceBufferSize': 8 * 1024 * 1024,
                'maxPostDataSize': 1024 * 1024,
            }, session_id)
            if target_id in self.targets:
                self.targets[target_id]['network_enabled'] = True
        except (CDPError, ConnectionError, asyncio.TimeoutError) as error:
            self.errors.append({'target_id': target_id, 'error': str(error)})
        finally:
            try:
                await self.command('Runtime.runIfWaitingForDebugger', session_id=session_id)
            except (CDPError, ConnectionError, asyncio.TimeoutError) as error:
                self.errors.append({'target_id': target_id, 'resume_error': str(error)})

    def record(self, message):
        self.sequence += 1
        session_id = message.get('sessionId')
        event = {
            'cursor': self.sequence, 'received_at': time.time(),
            'target_id': self.sessions.get(session_id), **message,
        }
        size = len(json.dumps(event).encode())
        if size > 1024 * 1024:
            self.dropped += 1
            self.changed.set()
            return
        self.events.append((event, size))
        self.event_bytes += size
        while len(self.events) > 4000 or self.event_bytes > 16 * 1024 * 1024:
            _, removed = self.events.popleft()
            self.event_bytes -= removed
            self.dropped += 1
        self.changed.set()

    def get_events(self, after=0, method='', target_id=None, limit=100):
        matches = [e for e, _ in self.events if e['cursor'] > after
                   and e.get('method', '').startswith(method)
                   and (target_id is None or e['target_id'] == target_id)]
        selected = matches[:limit]
        return {
            'events': selected, 'cursor': selected[-1]['cursor'] if len(matches) > limit else self.sequence,
            'has_more': len(matches) > limit, 'dropped_events': self.dropped,
            'oldest_cursor': self.events[0][0]['cursor'] if self.events else None,
        }

    def requests(self):
        requests = {}
        for event, _ in self.events:
            method = event.get('method', '')
            params = event.get('params', {})
            request_id = params.get('requestId')
            if not method.startswith('Network.') or not request_id:
                continue
            key = f"{event.get('sessionId', '')}:{request_id}"
            item = requests.setdefault(key, {
                'id': key, 'request_id': request_id, 'session_id': event.get('sessionId'),
                'target_id': event['target_id'], 'events': [],
            })
            item['events'].append(event)
            item['cursor'] = event['cursor']
            if method == 'Network.requestWillBeSent':
                item.update(request=params['request'], frame_id=params.get('frameId'),
                            type=params.get('type'), timestamp=params.get('timestamp'))
                item.pop('finished', None)
                item.pop('error', None)
            elif method == 'Network.responseReceived':
                item['response'] = params['response']
            elif method == 'Network.loadingFinished':
                item['finished'] = True
                item['encoded_bytes'] = params.get('encodedDataLength')
            elif method == 'Network.loadingFailed':
                item['error'] = params
            elif method == 'Network.webSocketCreated':
                item.update(request={'url': params['url'], 'method': 'GET'}, type='WebSocket')
        return requests

    def request(self, request_id):
        item = self.requests().get(request_id)
        if item is None:
            raise ValueError('Request not retained. Start capture before the action and list requests again.')
        return item

    def list_requests(self, url='', method='', status=None, target_id=None, after=0, limit=100):
        rows = []
        for item in self.requests().values():
            request = item.get('request', {})
            response = item.get('response', {})
            if (url not in request.get('url', '') or method and method.upper() != request.get('method')
                    or status is not None and status != response.get('status')
                    or target_id is not None and target_id != item['target_id'] or item['cursor'] <= after):
                continue
            rows.append({
                'id': item['id'], 'target_id': item['target_id'], 'cursor': item['cursor'],
                'url': request.get('url'), 'method': request.get('method'), 'type': item.get('type'),
                'status': response.get('status'), 'mime_type': response.get('mimeType'),
                'finished': item.get('finished', False), 'error': item.get('error'),
                'encoded_bytes': item.get('encoded_bytes'),
            })
        rows.sort(key=lambda item: item['cursor'])
        selected = rows[:limit]
        return {'requests': selected, 'has_more': len(rows) > limit,
                'cursor': selected[-1]['cursor'] if len(rows) > limit else self.sequence,
                'dropped_events': self.dropped}

    async def body(self, request_id, kind, offset=0, limit=16000):
        item = self.request(request_id)
        method = 'Network.getResponseBody' if kind == 'response' else 'Network.getRequestPostData'
        result = await self.command(method, {'requestId': item['request_id']}, item['session_id'])
        value = result['body' if kind == 'response' else 'postData']
        return {'id': request_id, 'kind': kind, 'base64_encoded': result.get('base64Encoded', False),
                'total_characters': len(value), 'offset': offset, 'content': value[offset:offset + limit],
                'next_offset': offset + limit if offset + limit < len(value) else None}

    def session(self, target_id):
        if target_id not in self.targets:
            raise ValueError('Target not attached. Use browser_capture status to list targets.')
        return self.targets[target_id]['session_id']

    async def evaluate(self, target_id, expression, timeout=15, context_id=None):
        params = {'expression': expression, 'returnByValue': True, 'awaitPromise': True,
                  'timeout': timeout * 1000}
        if context_id is not None:
            params['contextId'] = context_id
        result = await self.command('Runtime.evaluate', params, self.session(target_id), timeout + 1)
        if 'exceptionDetails' in result:
            raise CDPError(json.dumps(result['exceptionDetails']))
        return result['result']

    async def wait(self, target_id=None, expression=None, url=None, status=None,
                   after=0, timeout=15):
        if (expression is None) == (url is None):
            raise ValueError('Provide exactly one of expression or url.')
        if expression is not None and target_id is None:
            raise ValueError('A target_id is required for a JavaScript condition.')
        async with asyncio.timeout(timeout):
            while True:
                self.changed.clear()
                if expression is not None:
                    result = await self.evaluate(target_id, f'Boolean({expression})', timeout)
                    if result.get('value') is True:
                        return {'matched': True, 'target_id': target_id}
                    await asyncio.sleep(0.1)
                else:
                    rows = self.list_requests(url=url, status=status, target_id=target_id,
                                              after=after, limit=500)['requests']
                    completed = [row for row in rows if row['finished'] or row['error']]
                    if completed:
                        return {'matched': True, 'request': completed[0]}
                    if self.reader.done():
                        raise ConnectionError('Browser CDP transport closed')
                    await self.changed.wait()

    async def replay(self, request_id, mode='fetch', url=None, method=None, headers=None,
                     body=None, timeout=15):
        item = self.request(request_id)
        if mode == 'xhr':
            if any(value is not None for value in (url, method, headers, body)):
                raise ValueError('Native XHR replay does not accept edits. Use fetch mode for edits.')
            if item.get('type') != 'XHR':
                raise ValueError('Native XHR replay requires a captured XHR request.')
            cursor = self.sequence
            await self.command('Network.replayXHR', {'requestId': item['request_id']}, item['session_id'])
            return {'replayed': True, 'mode': 'xhr', 'after_cursor': cursor}
        request = item['request']
        destination = url or request['url']
        old = urlsplit(request['url'])
        new = urlsplit(destination)
        if new.scheme not in {'http', 'https'} or (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            raise ValueError('Edited replay URL must retain the original HTTP(S) origin; credentials are not forwarded to another origin.')
        if new.username is not None or new.password is not None:
            raise ValueError('Replay URLs cannot contain credentials.')
        outgoing = {key.lower(): value for key, value in request.get('headers', {}).items()}
        outgoing.update({key.lower(): value for key, value in (headers or {}).items()})
        excluded = {'cookie', 'cookie2', 'host', 'connection', 'content-length', 'accept-encoding',
                    'origin', 'referer', 'user-agent', 'transfer-encoding', 'upgrade', 'te', 'trailer'}
        outgoing = {key: value for key, value in outgoing.items()
                    if key.lower() not in excluded and not key.lower().startswith(('sec-', 'proxy-', ':'))}
        verb = (method or request['method']).upper()
        payload = body
        if payload is None and request.get('hasPostData') and verb not in {'GET', 'HEAD'}:
            content_type = next((v for k, v in outgoing.items() if k.lower() == 'content-type'), '')
            if 'multipart/form-data' in content_type or request.get('postDataEntries'):
                raise ValueError('Supply an explicit body for binary or multipart fetch replay; CDP post data can omit uploaded files.')
            result = await self.command('Network.getRequestPostData', {'requestId': item['request_id']}, item['session_id'])
            payload = result['postData']
        options = {'method': verb, 'headers': outgoing, 'credentials': 'include', 'redirect': 'manual'}
        if payload is not None:
            if verb in {'GET', 'HEAD'}:
                raise ValueError('GET/HEAD replay cannot include a body.')
            options['body'] = payload
        context_id = None
        if item.get('frame_id'):
            context = await self.command('Page.createIsolatedWorld', {
                'frameId': item['frame_id'], 'worldName': 'fernando-request-replay',
            }, item['session_id'])
            context_id = context['executionContextId']
        cursor = self.sequence
        expression = f'''(async () => {{
            const options = {json.dumps(options)};
            options.signal = AbortSignal.timeout({int(timeout * 1000)});
            const response = await fetch({json.dumps(destination)}, options);
            return {{url: response.url, status: response.status, type: response.type,
                     headers: Object.fromEntries(response.headers.entries())}};
        }})()'''
        result = await self.evaluate(item['target_id'], expression, timeout + 1, context_id)
        return {'mode': 'fetch', 'after_cursor': cursor, 'response': result.get('value'),
                'note': 'Browser fetch semantics apply, including CORS, current cookies and manual redirects. Inspect captured traffic for the actual request and body.'}


browser = BrowserCDP()
