import argparse
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from src.services import conversation_search as search


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sessions', type=int, default=16)
    parser.add_argument('--events', type=int, default=100000)
    args = parser.parse_args()
    ordinary = {'method': 'session/update', 'params': {'update': {'sessionUpdate': 'tool_call_update', 'toolCallId': 'benchmark-tool', 'rawOutput': {'content': [{'type': 'text', 'text': 'Routine tool output without a match. ' * 8}]}}}}
    last = {'params': {'update': {'sessionUpdate': 'tool_call_update', 'rawOutput': {'text': 'The final event contains z9x7q here'}}}}
    block = (json.dumps(ordinary) + '\n').encode()
    sessions = [{'id': f'{i:08x}', 'name': 'Benchmark ' + str(i)} for i in range(args.sessions)]
    with tempfile.TemporaryDirectory(prefix='fernando-search-benchmark-') as directory:
        root = Path(directory)
        for session in sessions:
            with (root / (session['id'] + '.jsonl')).open('wb') as stream:
                for _ in range((args.events - 1) // 1000):
                    stream.write(block * 1000)
                stream.write(block * ((args.events - 1) % 1000))
                stream.write((json.dumps(last) + '\n').encode())
        size = sum(path.stat().st_size for path in root.iterdir())
        for query in ('"z9x7q"', '"absent-five-character-needle"'):
            started = time.perf_counter()
            first = None
            matches = 0
            state = None
            def receive(reply):
                nonlocal first, matches, state
                if reply.get('sessions'):
                    matches += len(reply['sessions'])
                    if first is None:
                        first = time.perf_counter() - started
                state = reply.get('state', state)
            with patch.object(search.chat_history, 'HISTORY_DIR', root):
                thread = search.start('benchmark', query, sessions, receive)
                thread.join(180)
                if thread.is_alive():
                    search.cancel('benchmark')
                    raise RuntimeError('Benchmark exceeded 180 seconds')
            search.cancel('benchmark')
            duration = time.perf_counter() - started
            print(json.dumps({'query': query, 'sessions': args.sessions, 'events_per_session': args.events, 'MiB': round(size / 1048576, 2), 'workers': search.WORKERS, 'first_result_seconds': first, 'total_seconds': duration, 'MiB_per_second': size / 1048576 / duration, 'matches': matches, 'state': state}), flush=True)


if __name__ == '__main__':
    main()
