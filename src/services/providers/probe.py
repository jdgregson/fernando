import json
import os
import selectors
import signal
import subprocess
import tempfile
import time


def probe(launch, method, params, timeout=45):
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(launch.command, env=launch.environment,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b''

        def request(identifier, method, params):
            nonlocal buffer
            process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params}) + '\n').encode())
            process.stdin.flush()
            while True:
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    message = json.loads(line)
                    if message.get('id') == identifier and 'method' not in message:
                        if 'error' in message:
                            raise RuntimeError(message['error'].get('message', 'ACP discovery failed'))
                        return message['result']
                    if 'method' in message and 'id' in message:
                        response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Discovery does not support client requests'}}
                        process.stdin.write((json.dumps(response) + '\n').encode())
                        process.stdin.flush()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('ACP discovery timed out')
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('ACP process exited during discovery')
                buffer += chunk
                if len(buffer) > 8 * 1024 * 1024:
                    raise ValueError('ACP discovery response exceeds limit')

        try:
            request(1, 'initialize', {'protocolVersion': 1, 'clientCapabilities': {},
                                      'clientInfo': {'name': 'fernando-discovery', 'version': '1.0.0'}})
            return request(2, method, params)
        finally:
            selector.close()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
            process.stdin.close()
            process.stdout.close()
