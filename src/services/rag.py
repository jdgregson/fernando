"""RAG service for indexing and searching chat conversation history using ChromaDB."""

import json
import logging
import os
import threading
from pathlib import Path
from src.services.rag_chunking import chunk_text as _chunk_text

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
HISTORY_DIR = os.path.join(DATA_DIR, "chat_history")
COLLECTION_NAME = "chat_history"
NEW_COLLECTION_NAME = "chat_history_sentences_v2"
ACTIVE_COLLECTION_FILE = os.path.join(DATA_DIR, 'rag_collection.json')
_index_lock = threading.RLock()
_migration_thread = None


def _get_collection(name=None):
    import chromadb
    if name is None:
        try:
            with open(ACTIVE_COLLECTION_FILE) as stream:
                name = json.load(stream)['collection']
        except FileNotFoundError:
            name = COLLECTION_NAME
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(name)


def _extract_turns(history):
    """Extract (turn_index, user_text, assistant_text) tuples from a history list."""
    turns = []
    turn_idx = 0
    i = 0
    while i < len(history):
        entry = history[i]
        if not isinstance(entry, dict):
            i += 1
            continue
        entry_type = entry.get("type", "")
        if entry_type in ("user_prompt", "continuation"):
            user_text = entry.get("text", "")
            # Collect assistant chunks until next user_prompt/continuation or result with stopReason
            assistant_parts = []
            i += 1
            while i < len(history):
                e = history[i]
                if not isinstance(e, dict):
                    i += 1
                    continue
                if e.get("type") in ("user_prompt", "continuation"):
                    break
                if e.get("method") == "session/update":
                    update = (e.get("params", {}).get("update") or {})
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        content = update.get("content", {})
                        if content.get("type") == "text":
                            assistant_parts.append(content.get("text", ""))
                if "result" in e and e.get("result", {}).get("stopReason"):
                    i += 1
                    break
                i += 1
            assistant_text = "".join(assistant_parts).strip()
            if user_text or assistant_text:
                turns.append((turn_idx, user_text, assistant_text))
            turn_idx += 1
        else:
            i += 1
    return turns


def index_session(session_id, session_name, history, collection_name=None):
    from src.services.chat_history import path_for
    with _index_lock:
        if not path_for(session_id).exists():
            return
        _index_session(session_id, session_name, history, collection_name)


def _index_session(session_id, session_name, history, collection_name=None):
    """Index all turns from a session's history into ChromaDB, chunked for full coverage."""
    from src.services.chat_history import inherited_count
    inherited = inherited_count(session_id)
    offset = sum(e.get('type') in ('user_prompt', 'continuation') for e in history[:inherited])
    turns = _extract_turns(history[inherited:])
    collection = _get_collection(collection_name) if collection_name else _get_collection()
    existing = collection.get(where={'session_id': session_id}, include=['documents', 'metadatas'])
    previous = dict(zip(existing['ids'], zip(existing['documents'], existing['metadatas'])))
    ids = []
    documents = []
    metadatas = []
    desired = set()
    for turn_idx, user_text, assistant_text in turns:
        turn_idx += offset
        # A lengthy reply must not drown out a short user request (or vice versa).
        for role, text in [('user', user_text), ('assistant', assistant_text)]:
            for chunk_idx, chunk in enumerate(_chunk_text(text)):
                chunk_id = f"{session_id}_{turn_idx}_{role}_{chunk_idx}"
                desired.add(chunk_id)
                metadata = {"session_id": session_id, "session_name": session_name, "turn_index": turn_idx, 'role': role}
                if previous.get(chunk_id) == (chunk, metadata):
                    continue
                ids.append(chunk_id)
                documents.append(chunk)
                metadatas.append(metadata)
    for start in range(0, len(ids), 128):
        collection.upsert(ids=ids[start:start + 128], documents=documents[start:start + 128], metadatas=metadatas[start:start + 128])
    stale = list(set(previous) - desired)
    for start in range(0, len(stale), 128):
        collection.delete(ids=stale[start:start + 128])
    logger.info(f"Indexed {len(ids)} chunks for session {session_id}")


def delete_session(session_id):
    with _index_lock:
        _delete_session(session_id)


def _delete_session(session_id):
    """Remove all chunks for a session from ChromaDB."""
    collection = _get_collection()
    results = collection.get(where={"session_id": session_id})
    if results["ids"]:
        collection.delete(ids=results["ids"])
        logger.info(f"Deleted {len(results['ids'])} chunks for session {session_id}")


def search(query, limit=5):
    """Search conversations. Returns list of {session_id, session_name, turn_index, snippet, score}."""
    collection = _get_collection()
    if collection.count() == 0:
        return []
    limit = max(1, min(int(limit), 100))
    results = collection.query(query_texts=[query], n_results=min(limit * 5, collection.count()))
    hits = []
    seen = set()
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        key = (meta['session_id'], meta['turn_index'])
        if key in seen:
            continue
        seen.add(key)
        hits.append({
            "session_id": meta["session_id"],
            "session_name": meta.get("session_name", ""),
            "turn_index": meta.get("turn_index", 0),
            "snippet": results["documents"][0][i][:500],
            "score": results["distances"][0][i] if results.get("distances") else None,
        })
        if len(hits) >= limit:
            break
    return hits


def get_conversation(session_id, offset=0, limit=None):
    """Load conversation history for a session, returning readable turns with optional slicing."""
    from src.services.acp import load_history_file
    history = load_history_file(session_id)
    if not history:
        return None
    turns = _extract_turns(history)
    end = offset + limit if limit else None
    sliced = turns[offset:end]
    return [{"turn_index": t[0], "user": t[1], "assistant": t[2]} for t in sliced]


def _needs_migration():
    """Check if migration from old to new collection format is needed.
    
    Returns True if:
    - The activation file doesn't exist (not yet migrated)
    - AND the old collection has data (there's something to migrate)
    """
    if os.path.exists(ACTIVE_COLLECTION_FILE):
        return False
    # Check if old collection has data
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        old_collection = client.get_collection(COLLECTION_NAME)
        return old_collection.count() > 0
    except Exception:
        return False


def _run_migration():
    """Migrate all sessions from old index to new sentence-based index."""
    from src.services import chat_history
    
    logger.info("Starting RAG index migration to sentence-based chunking...")
    
    # First, write the activation file so new convos go to the right place immediately
    activation_path = Path(ACTIVE_COLLECTION_FILE)
    temp_path = activation_path.with_suffix('.tmp')
    temp_path.write_text(json.dumps({'collection': NEW_COLLECTION_NAME}) + '\n')
    os.replace(temp_path, activation_path)
    logger.info(f"Activated new collection: {NEW_COLLECTION_NAME}")
    
    # Get session names from old index and sessions file
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        old_collection = client.get_collection(COLLECTION_NAME)
        old_meta = old_collection.get(include=['metadatas'])
        names = {m['session_id']: m.get('session_name', '') for m in old_meta['metadatas']}
    except Exception:
        names = {}
    
    sessions_path = Path(DATA_DIR) / 'chat_sessions.json'
    if sessions_path.exists():
        try:
            names.update({sid: data.get('name', sid) for sid, data in json.loads(sessions_path.read_text()).items()})
        except Exception:
            pass
    
    # Index all sessions to the new collection
    paths = sorted(chat_history.HISTORY_DIR.glob('*.jsonl'))
    total = len(paths)
    for i, path in enumerate(paths, 1):
        try:
            history = chat_history.load(path.stem)
            index_session(path.stem, names.get(path.stem, path.stem), history, NEW_COLLECTION_NAME)
            if i % 50 == 0 or i == total:
                logger.info(f"Migration progress: {i}/{total} sessions indexed")
        except Exception as e:
            logger.warning(f"Failed to migrate session {path.stem}: {e}")
    
    logger.info(f"RAG migration complete: {total} sessions indexed to {NEW_COLLECTION_NAME}")


def maybe_start_migration():
    """Check if migration is needed and start it in a background thread.
    
    Call this at application startup. Safe to call multiple times.
    """
    global _migration_thread
    
    if _migration_thread is not None and _migration_thread.is_alive():
        return  # Already running
    
    if not _needs_migration():
        return  # Nothing to migrate
    
    logger.info("Detected old RAG index format, scheduling migration...")
    _migration_thread = threading.Thread(target=_run_migration, daemon=True, name="rag-migration")
    _migration_thread.start()
