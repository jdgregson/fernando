"""RAG service for indexing and searching chat conversation history using ChromaDB."""

import json
import logging
import os

import chromadb

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
HISTORY_DIR = os.path.join(DATA_DIR, "chat_history")
COLLECTION_NAME = "chat_history"


def _get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(COLLECTION_NAME)


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


CHUNK_SIZE = 800  # chars (~200 tokens), well within MiniLM's 256-token limit
CHUNK_OVERLAP = 100  # overlap so phrases spanning boundaries are still found


def _chunk_text(text):
    """Split text into overlapping chunks that fit within the embedding model's token limit."""
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return chunks


def index_session(session_id, session_name, history):
    """Index all turns from a session's history into ChromaDB, chunked for full coverage."""
    turns = _extract_turns(history)
    if not turns:
        return
    collection = _get_collection()
    ids = []
    documents = []
    metadatas = []
    for turn_idx, user_text, assistant_text in turns:
        doc = ""
        if user_text:
            doc += f"User: {user_text}\n"
        if assistant_text:
            doc += f"Assistant: {assistant_text}"
        doc = doc.strip()
        chunks = _chunk_text(doc)
        for chunk_idx, chunk in enumerate(chunks):
            chunk_id = f"{session_id}_{turn_idx}_{chunk_idx}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({"session_id": session_id, "session_name": session_name, "turn_index": turn_idx})
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    logger.info(f"Indexed {len(ids)} chunks for session {session_id}")


def delete_session(session_id):
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
    results = collection.query(query_texts=[query], n_results=min(limit, collection.count()))
    hits = []
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        hits.append({
            "session_id": meta["session_id"],
            "session_name": meta.get("session_name", ""),
            "turn_index": meta.get("turn_index", 0),
            "snippet": results["documents"][0][i][:500],
            "score": results["distances"][0][i] if results.get("distances") else None,
        })
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
