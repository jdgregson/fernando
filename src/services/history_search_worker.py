import itertools
import json
import os
import orjson
from pathlib import Path
import re
import sys


def history_path(root, session_id):
    if not re.fullmatch(r'[a-f0-9]{8}', session_id):
        raise ValueError('Invalid history session ID')
    return Path(root) / (session_id + '.jsonl')


def events(root, session_id, ancestors=()):
    if session_id in ancestors:
        raise ValueError('Circular history reference')
    path = history_path(root, session_id)
    if not path.exists():
        return
    with path.open('rb') as stream:
        remaining = os.fstat(stream.fileno()).st_size
        first = True
        while remaining:
            line = stream.readline(remaining)
            remaining -= len(line)
            if not line.endswith(b'\n'):
                break
            if not line.strip():
                continue
            event = orjson.loads(line)
            if first and event.get('type') == 'history_reference':
                count = event['event_count']
                if type(count) is not int or count < 0:
                    raise ValueError('Invalid fork boundary')
                inherited = 0
                for parent in itertools.islice(events(root, event['session_id'], (*ancestors, session_id)), count):
                    inherited += 1
                    yield parent
                if inherited != count:
                    raise ValueError('Referenced history is incomplete')
            else:
                yield event
            first = False


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        if value.get('type') in ('image', 'audio'):
            return
        for key, child in value.items():
            yield key
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif value is not None:
        yield str(value)


def fields(event):
    kind = event.get('type')
    if kind in ('user_prompt', 'continuation', 'agent_message', 'error'):
        yield 'message', str(event.get('text', '')), False
    update = event.get('params', {}).get('update') or {}
    kind = update.get('sessionUpdate')
    if kind in ('agent_message_chunk', 'user_message_chunk', 'agent_thought_chunk'):
        content = update.get('content') or {}
        if content.get('type') == 'text':
            yield kind, content.get('text', ''), True
    elif kind in ('tool_call', 'tool_call_update'):
        for key in ('title', 'rawInput', 'rawOutput', 'content', 'locations'):
            value = update.get(key)
            if value is not None:
                yield 'tool', '\n'.join(strings(value)), False
                if isinstance(value, (dict, list)):
                    yield 'tool', orjson.dumps(value, option=orjson.OPT_INDENT_2).decode(), False


def scan(root, session_id, terms, report=None):
    tail = ''
    previous = None
    overlap = max(map(len, terms)) - 1
    found = set()
    last_snippet = ''
    for index, event in enumerate(events(root, session_id)):
        if report and index and index % 10000 == 0:
            report(index)
        parts = list(fields(event))
        if not parts:
            if event.get('result', {}).get('stopReason'):
                previous, tail, found = None, '', set()
            continue
        for key, text, streaming in parts:
            if not streaming or previous != key:
                tail, found = '', set()
            combined = tail + text
            folded = combined.casefold()
            for term in terms:
                position = folded.find(term)
                if position >= 0:
                    found.add(term)
                    last_snippet = combined[max(0, position - 120):position + max(380, len(term))]
            if len(found) == len(terms):
                return {'snippet': last_snippet, 'event_index': index, 'match': 'Tool content' if key == 'tool' else 'Message content'}
            tail = combined[-overlap:] if streaming and overlap else ''
            previous = key if streaming else None
    return None


def main():
    for line in sys.stdin:
        job = json.loads(line)
        def progress(count):
            print(json.dumps({'progress': count, 'id': job['id']}), flush=True)
        result = scan(job['root'], job['id'], job['terms'], progress)
        print(json.dumps({'id': job['id'], 'result': result}), flush=True)


if __name__ == '__main__':
    main()
