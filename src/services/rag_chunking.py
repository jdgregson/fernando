"""Sentence-first passages, bounded by the embedding model's actual tokenizer."""

import re
from functools import lru_cache

MAX_TOKENS = 192  # MiniLM allows 256, including special tokens.
OVERLAP_TOKENS = 24


@lru_cache(maxsize=1)
def _tokenizer():
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    from tokenizers import Tokenizer
    model = ONNXMiniLM_L6_V2()
    model._download_model_if_not_exists()
    tokenizer = Tokenizer.from_file(str(model.DOWNLOAD_PATH / model.EXTRACTED_FOLDER_NAME / 'tokenizer.json'))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return tokenizer


def chunk_text(text):
    # UI context is not conversation content and dilutes every short embedding.
    text = re.sub(r'^\s*\[Pane context:[^\n]*\]\s*\n?', '', text)
    tokenizer = _tokenizer()
    chunks = []
    # Keep list items/paragraphs separate; don't blend unrelated requests.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sentences = re.split(r'(?<=[.!?])\s+(?=[\"\“\‘\'A-Z0-9])', line)
        for sentence in sentences:
            encoded = tokenizer.encode(sentence, add_special_tokens=False)
            if len(encoded.ids) <= MAX_TOKENS:
                chunks.append(sentence)
                continue
            # Oversized sentences/code still retain every character. Token
            # offsets avoid corrupting Unicode or silently truncating the tail.
            start = 0
            while start < len(encoded.ids):
                end = min(start + MAX_TOKENS, len(encoded.ids))
                left = encoded.offsets[start][0] if start else 0
                right = encoded.offsets[end][0] if end < len(encoded.ids) else len(sentence)
                chunks.append(sentence[left:right].strip())
                if end == len(encoded.ids):
                    break
                start = end - OVERLAP_TOKENS
    return [chunk for chunk in chunks if chunk]
