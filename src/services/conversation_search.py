from collections import deque
from difflib import SequenceMatcher
import json
import logging
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import threading

from src.services import chat_history
from src.services.rag import exact_search_phrase, search as rag_search


logger = logging.getLogger(__name__)
WORKERS = min(4, max(1, (os.cpu_count() or 2) - 1))
_slots = threading.BoundedSemaphore(WORKERS)
_semantic_slot = threading.Semaphore(1)
_lock = threading.Lock()
_searches = {}
_versions = {}
WORKER = Path(__file__).with_name('history_search_worker.py')


def parse_query(query):
    tokens = re.findall(r'"[^"]*"|“[^”]*”|\S+', query)
    groups = [token[6:] for token in tokens if token.startswith('group:')]
    if len(groups) > 1 or (groups and not re.fullmatch(r'[a-f0-9]{8}|__ungrouped__', groups[0])):
        raise ValueError('Use one group:<id> filter')
    return ' '.join(token for token in tokens if not token.startswith('group:')).strip(), groups[0] if groups else None


def scoped_sessions(sessions, memberships, group_id):
    if group_id is None:
        return sessions
    return [session for session in sessions if (memberships.get('chat:' + session['id']) or '__ungrouped__') == group_id]


def cancel(client, request_id=None):
    with _lock:
        if request_id is not None:
            version = (request_id, 0)
            if version <= _versions.get(client, (-1, -1)):
                return
            _versions[client] = version
        else:
            _versions.pop(client, None)
        state = _searches.pop(client, None)
        if state:
            state.set()


def start(client, query, sessions, send, preferred=None, request_id=0):
    with _lock:
        version = (request_id, 1)
        if version <= _versions.get(client, (-1, -1)):
            return None
        _versions[client] = version
        previous = _searches.get(client)
        if previous:
            previous.set()
        cancelled = threading.Event()
        _searches[client] = cancelled
    thread = threading.Thread(target=_run, args=(client, cancelled, query, sessions, send, preferred), daemon=True)
    thread.start()
    return thread


def _run(client, cancelled, query, sessions, send, preferred):
    send_lock = threading.Lock()
    seen = {}
    completed = 0
    total = len(sessions)
    semantic_done = threading.Event()
    phrase = exact_search_phrase(query)
    terms = [phrase] if phrase is not None else list(dict.fromkeys(query.casefold().split()))

    def notify(payload):
        with send_lock:
            if not cancelled.is_set():
                send(payload)

    def match(session, result):
        with send_lock:
            rank = 1 if result['match'] == 'Related content' else 2
            if not cancelled.is_set() and rank > seen.get(session['id'], 0):
                seen[session['id']] = rank
                send({'sessions': [{**session, **result}]})

    def semantic():
        acquired = False
        try:
            while not cancelled.is_set():
                acquired = _semantic_slot.acquire(timeout=.1)
                if acquired:
                    break
            if not acquired or cancelled.is_set():
                return
            by_id = {s['id']: s for s in sessions}
            for hit in rag_search(query, limit=20, session_ids=list(by_id), distinct_sessions=True):
                if hit['session_id'] in by_id:
                    match(by_id[hit['session_id']], {'snippet': hit['snippet'], 'match': 'Related content'})
        except Exception:
            logger.exception('Semantic conversation search failed')
            notify({'warning': 'Semantic search failed; history scan continues'})
        finally:
            if acquired:
                _semantic_slot.release()
            semantic_done.set()

    processes = []
    buffers = {}
    selector = selectors.DefaultSelector()

    def close_workers():
        while processes:
            process = processes.pop()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
            _slots.release()

    try:
        notify({'state': 'searching', 'completed': 0, 'total': total})
        pending = deque()
        for session in sorted(sessions, key=lambda s: s['id'] != preferred):
            words = re.findall(r'\w+', session['name'].casefold())
            title_match = not query or (phrase is not None and phrase in session['name'].casefold())
            if phrase is None and terms:
                title_match = all(any(term in word or (len(term) >= 4 and SequenceMatcher(None, term, word).ratio() >= .78) for word in words) for term in terms)
            if title_match:
                match(session, {'snippet': '', 'match': 'Title'})
                completed += 1
            else:
                pending.append(session)
        if phrase is None and query and sessions:
            threading.Thread(target=semantic, daemon=True).start()
        else:
            semantic_done.set()

        def dispatch(process, session):
            process.stdin.write((json.dumps({'root': str(chat_history.HISTORY_DIR), 'id': session['id'], 'terms': terms}) + '\n').encode())
            process.stdin.flush()
            selector.register(process.stdout, selectors.EVENT_READ, (process, session))

        while pending and not cancelled.is_set() and len(processes) < WORKERS:
            acquired = _slots.acquire(timeout=.1 if not processes else 0)
            if not acquired:
                if processes:
                    break
                continue
            try:
                process = subprocess.Popen([sys.executable, '-u', str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            except OSError:
                _slots.release()
                raise
            processes.append(process)
            buffers[process] = b''
            dispatch(process, pending.popleft())
        while selector.get_map() and not cancelled.is_set():
            for key, _ in selector.select(timeout=.1):
                process, session = key.data
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('History search worker exited before completing its scan')
                buffers[process] += chunk
                while b'\n' in buffers[process]:
                    line, buffers[process] = buffers[process].split(b'\n', 1)
                    reply = json.loads(line)
                    if 'progress' in reply:
                        notify({'state': 'searching', 'completed': completed, 'total': total, 'events': reply['progress']})
                        continue
                    if reply['result']:
                        match(session, reply['result'])
                    completed += 1
                    notify({'state': 'searching', 'completed': completed, 'total': total})
                    selector.unregister(process.stdout)
                    if pending:
                        dispatch(process, pending.popleft())
        close_workers()
        while not cancelled.is_set() and not semantic_done.wait(.1):
            if completed == total:
                notify({'state': 'semantic', 'completed': completed, 'total': total})
        if not cancelled.is_set():
            notify({'state': 'complete', 'completed': completed, 'total': total})
    except Exception:
        logger.exception('Conversation history search failed')
        notify({'state': 'error', 'error': 'History search failed; see server logs', 'completed': completed, 'total': total})
        cancelled.set()
    finally:
        selector.close()
        close_workers()
        with _lock:
            if _searches.get(client) is cancelled:
                del _searches[client]
