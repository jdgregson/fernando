"""Build/resume a candidate index, benchmark searches, optionally activate it."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services import chat_history, rag


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collection', default='chat_history_sentences_v2')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--sample', type=int)
    args = parser.parse_args()
    start = time.monotonic()
    collection = rag._get_collection(args.collection)
    old = rag._get_collection('chat_history').get(include=['metadatas'])
    names = {m['session_id']: m.get('session_name', '') for m in old['metadatas']}
    sessions_path = Path(rag.DATA_DIR) / 'chat_sessions.json'
    if sessions_path.exists():
        names.update({sid: data.get('name', sid) for sid, data in json.loads(sessions_path.read_text()).items()})
    paths = sorted(chat_history.HISTORY_DIR.glob('*.jsonl'))
    # Benchmark the two motivating conversations first, then the rest.
    paths.sort(key=lambda p: (p.stem not in ('50675b7b', 'a2f8dd07'), p.name))
    if args.sample:
        paths = paths[:args.sample]
    for i, path in enumerate(paths, 1):
        history = chat_history.load(path.stem)
        rag.index_session(path.stem, names.get(path.stem, path.stem), history, args.collection)
        print(json.dumps({'session': path.stem, 'done': i, 'total': len(paths), 'chunks': collection.count(), 'seconds': round(time.monotonic() - start, 2)}), flush=True)
    # Remove sessions physically deleted during/after a previous pass.
    # Batch the get() calls to avoid SQLite's "too many SQL variables" limit.
    stale = []
    total_chunks = collection.count()
    batch_size = 10000
    for offset in range(0, total_chunks, batch_size):
        batch = collection.get(include=['metadatas'], limit=batch_size, offset=offset)
        stale.extend(doc_id for doc_id, meta in zip(batch['ids'], batch['metadatas']) if not chat_history.path_for(meta['session_id']).exists())
    for i in range(0, len(stale), 128):
        collection.delete(ids=stale[i:i + 128])
    queries = [
        'That Kiro and OpenCode store their own copy of this matters little to me.',
        'Reorganize monolith files into easy to maintain modules that move but do not alter or replace functionality',
        'Keep game behavior the same while breaking large files into reusable modules',
        'What happens to model context when the original native chat session disappears?',
        'Prevent placing or picking up a tent while swimming',
    ]
    for query in queries:
        for target in ['chat_history', args.collection]:
            before = time.monotonic()
            result = rag._get_collection(target).query(query_texts=[query], n_results=5)
            print(json.dumps({'query': query, 'collection': target, 'seconds': round(time.monotonic() - before, 3),
                'hits': [{'session': m['session_id'], 'turn': m['turn_index'], 'distance': d, 'text': text[:240]}
                         for m, d, text in zip(result['metadatas'][0], result['distances'][0], result['documents'][0])]}), flush=True)
    if args.activate:
        if args.sample:
            raise ValueError('Cannot activate a sample index')
        path = Path(rag.ACTIVE_COLLECTION_FILE)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'collection': args.collection}) + '\n')
        os.replace(temporary, path)
        print('ACTIVATED ' + args.collection, flush=True)


if __name__ == '__main__':
    main()
