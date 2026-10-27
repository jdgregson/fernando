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
        yield 'user_message' if kind in ('user_prompt', 'continuation') else 'agent_message', str(event.get('text', '')), False
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
                yield 'tool:' + key, '\n'.join(strings(value)), False
                if isinstance(value, (dict, list)):
                    yield 'tool:' + key, orjson.dumps(value, option=orjson.OPT_INDENT_2).decode(), False


def scan(root, session_id, terms, report=None, excluded=()):
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
            actor = 'user' if key in ('user_message', 'user_message_chunk') else 'agent'
            if actor in excluded or ('tools' if key.startswith('tool:') else 'text') in excluded:
                previous, tail, found = None, '', set()
                continue
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
                return {'snippet': last_snippet, 'event_index': index, 'source': key, 'match_text': terms[0], 'match': 'Tool content' if key.startswith('tool:') else 'Message content'}
            tail = combined[-overlap:] if streaming and overlap else ''
            previous = key if streaming else None
    return None


def scan_matches(root, session_id, terms, excluded=()):
    text_parts = []
    segments = []
    length = 0
    previous = None
    seen_tools = set()

    def matches(text, source, positions, tool_id=None):
        folded = text.casefold()
        if not all(term in folded for term in terms):
            return
        offset = 0
        occurrence = 0
        while (position := folded.find(terms[0], offset)) >= 0:
            end = position + len(terms[0])
            event_index = next(index for boundary, index in positions if boundary >= end)
            identity = (tool_id, 'tool:output' if source in ('tool:rawOutput', 'tool:content') else source, occurrence)
            if tool_id is None or identity not in seen_tools:
                if tool_id is not None:
                    seen_tools.add(identity)
                yield {'snippet': text[max(0, position - 120):end + 260], 'event_index': event_index, 'source': source, 'match_text': terms[0], 'match_occurrence': occurrence, 'match': 'Tool content' if source.startswith('tool:') else 'Message content'}
            occurrence += 1
            offset = end

    for index, event in enumerate(events(root, session_id)):
        parts = list(fields(event))
        if not parts and not event.get('result', {}).get('stopReason'):
            continue
        if not parts:
            parts = [(None, '', False)]
        for key, text, streaming in parts:
            if text_parts and (not streaming or key != previous):
                yield from matches(''.join(text_parts), previous, segments)
                text_parts, segments, length = [], [], 0
            actor = 'user' if key in ('user_message', 'user_message_chunk') else 'agent'
            if key is None or actor in excluded or ('tools' if key.startswith('tool:') else 'text') in excluded:
                previous = None
                continue
            if streaming:
                text_parts.append(text)
                length += len(text.casefold())
                segments.append((length, index))
            else:
                tool_id = event.get('params', {}).get('update', {}).get('toolCallId') if key.startswith('tool:') else None
                yield from matches(text, key, [(len(text.casefold()), index)], tool_id)
            previous = key if streaming else None
    if text_parts:
        yield from matches(''.join(text_parts), previous, segments)


def main():
    for line in sys.stdin:
        job = json.loads(line)
        def progress(count):
            print(json.dumps({'progress': count, 'id': job['id']}), flush=True)
        if job.get('all_matches'):
            for result in scan_matches(job['root'], job['id'], job['terms'], job['excluded']):
                print(json.dumps({'id': job['id'], 'match': result}), flush=True)
            result = None
        else:
            result = scan(job['root'], job['id'], job['terms'], progress, job['excluded'])
        print(json.dumps({'id': job['id'], 'result': result}), flush=True)


if __name__ == '__main__':
    main()
