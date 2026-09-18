import json
import os
import re
import shutil
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / 'data'
HISTORY_DIR = ROOT / 'chat_history'
lock = threading.RLock()


def path_for(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(r'[a-f0-9]{8}', session_id):
        raise ValueError('Invalid history session ID')
    return HISTORY_DIR / f'{session_id}.jsonl'


def read_local(session_id):
    path = path_for(session_id)
    if not path.exists():
        return []
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def reference(session_id):
    path = path_for(session_id)
    if not path.exists():
        return None
    with path.open() as stream:
        line = stream.readline()
    if not line.strip():
        return None
    event = json.loads(line)
    return event if event.get('type') == 'history_reference' else None


def load(session_id, ancestors=()):
    with lock:
        if session_id in ancestors:
            raise ValueError('Circular history reference')
        events = read_local(session_id)
        if not events or events[0].get('type') != 'history_reference':
            return events
        ref = events[0]
        count = ref['event_count']
        if type(count) is not int or count < 0:
            raise ValueError('Invalid history boundary')
        parent = load(ref['session_id'], (*ancestors, session_id))
        if count > len(parent):
            raise ValueError('Referenced history is incomplete')
        return parent[:count] + events[1:]


def fork(source_id, new_id, event_count):
    with lock:
        parent = load(source_id)
        if type(event_count) is not int or not 0 <= event_count <= len(parent):
            raise ValueError('Invalid fork history boundary')
        if source_id == new_id:
            raise ValueError('Cannot reference own history')
        path = path_for(new_id)
        ref = {'type': 'history_reference', 'session_id': source_id, 'event_count': event_count}
        with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write(json.dumps(ref) + '\n')


def inherited_count(session_id):
    with lock:
        ref = reference(session_id)
        return ref['event_count'] if ref else 0


def is_deleted(session_id):
    return path_for(session_id).with_suffix('.deleted').exists()


def delete(session_id):
    with lock:
        path_for(session_id).with_suffix('.deleted').touch(mode=0o600)
        removed = []
        while True:
            referenced = set()
            for path in HISTORY_DIR.glob('*.jsonl'):
                ref = reference(path.stem)
                if ref:
                    referenced.add(ref['session_id'])
            disposable = [p for p in HISTORY_DIR.glob('*.deleted') if p.stem not in referenced]
            if not disposable:
                return removed
            for marker in disposable:
                sid = marker.stem
                path_for(sid).unlink(missing_ok=True)
                for cache in ('image_cache', 'file_cache'):
                    shutil.rmtree(ROOT / cache / sid, ignore_errors=True)
                marker.unlink()
                removed.append(sid)
