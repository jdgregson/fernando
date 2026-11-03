import subprocess
import re
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta
from threading import Lock

from scripts.color_logs import parse_line


FILES = {
    'flask': '/tmp/fernando-flask.log',
    'restart': '/tmp/fernando-mutate.log',
    'nginx-access': '/tmp/nginx-fernando-access.log',
    'nginx-error': '/tmp/nginx-fernando-error.log',
    'jupyter': '/tmp/fernando-jupyter.log',
}


class ErrorIndex:
    def __init__(self):
        self.lock = Lock()
        self.identity = None
        self.offset = 0
        self.entries = deque(maxlen=20)
        self.timestamps = deque()
        self.context = deque(maxlen=3)
        self.current = None

    def snapshot(self, path, include_records=False, now=None):
        now = now or datetime.now()
        cutoff = now - timedelta(hours=1)
        with self.lock:
            with Path(path).open('rb') as stream:
                import os
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if identity != self.identity or stat.st_size < self.offset:
                    self.identity = identity
                    self.offset = 0
                    self.entries.clear()
                    self.timestamps.clear()
                    self.context.clear()
                    self.current = None
                stream.seek(self.offset)
                while raw := stream.readline():
                    if not raw.endswith(b'\n'):
                        break
                    self.offset = stream.tell()
                    line = raw.decode(errors='replace').rstrip('\r\n')
                    record = parse_line(line)
                    if record.get('level'):
                        self.current = None
                        if record['level'] in {'ERROR', 'CRITICAL'}:
                            timestamp = datetime.strptime(record['timestamp'], '%Y-%m-%d %H:%M:%S,%f')
                            if timestamp > cutoff:
                                self.timestamps.append(timestamp)
                            self.current = {'lines': list(self.context) + [line], 'size': len(line)}
                            self.entries.append(self.current)
                    elif self.current is not None and self.current['size'] < 65536:
                        self.current['lines'].append(line[:65536 - self.current['size']])
                        self.current['size'] += len(line)
                    self.context.append(line[:4096])
            self.timestamps = deque(timestamp for timestamp in self.timestamps if timestamp > cutoff)
            result = {'recent_count': sum(timestamp <= now for timestamp in self.timestamps)}
            if include_records:
                result['records'] = [parse_line(line) for entry in self.entries for line in [*entry['lines'], '']]
            return result


error_index = ErrorIndex()


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=5)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Log command failed')
    return result.stdout + result.stderr


def sources(source):
    if source == 'docker':
        output = command(['docker', 'ps', '-a', '--format', '{{.ID}} {{.Names}}'])
        return [{'id': parts[0], 'label': parts[1]} for line in output.splitlines()
                if len(parts := line.split()) == 2 and
                (parts[1] == 'fernando-desktop' or parts[1].startswith('fernando-notebook-'))]
    raise ValueError('Unknown log source')


def read_logs(source, target):
    if source == 'errors':
        return error_index.snapshot(FILES['flask'], include_records=True)['records']
    if source in FILES:
        path = Path(FILES[source])
    elif source == 'docker':
        if target not in {item['id'] for item in sources(source)}:
            raise ValueError('Unknown container')
        return records(command(['docker', 'logs', '--tail', '1000', '--timestamps', target]))
    elif source == 'journal':
        return records(command(['journalctl', '--unit=fernando.service', '--unit=fernando-runtime.service', '--unit=fernando-web.service', '--lines=1000', '--no-pager', '--output=short-iso']))
    else:
        raise ValueError('Unknown log source')
    with path.open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 1048576))
        if size > 1048576:
            stream.readline()
        return records(stream.read(1048576).decode(errors='replace'))


def records(text):
    return [parse_line(line) for line in text[-1048576:].splitlines()[-1000:]]


def redact_text(text, api_key):
    if api_key:
        text = text.replace(api_key, 'REDACTED')
    text = re.sub(
        r'''(?i)(\b(?:x-api-key|api_key|api-key)["']?\s*[:=]\s*["']?)[^\s&"'<>\\,;}]+''',
        r'\1REDACTED', text,
    )
    return re.sub(
        r'(?i)((?:x-api-key|api_key|api-key|api%5fkey|api%2dkey)(?:%22|%27)?(?:%20)*(?:%3d|%3a)(?:%20)*(?:%22|%27)?)(?:(?!(?:%26|%22|%27|%20|%3c|%3e))[^\s&"\'<>])+',
        r'\1REDACTED', text,
    )


def redact_payload(payload):
    api_key = Path('/tmp/fernando-api-key').read_text().strip()

    def redact(value):
        if isinstance(value, str):
            return redact_text(value, api_key)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {redact_text(key, api_key): redact(item) for key, item in value.items()}
        return value

    return redact(payload)
