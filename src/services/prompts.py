import json
import os
import threading
import uuid
from pathlib import Path


_PROMPTS_FILE = Path(__file__).resolve().parents[2] / "data" / "prompts.json"
_lock = threading.Lock()


def _load():
    if not _PROMPTS_FILE.exists():
        return []
    return json.loads(_PROMPTS_FILE.read_text())


def list_prompts():
    with _lock:
        return _load()


def add_prompt(text, title=""):
    with _lock:
        prompts = _load()
        prompt = {"id": uuid.uuid4().hex, "text": text, "title": title}
        prompts.append(prompt)
        _save(prompts)
        return prompt


def _save(prompts):
    temporary = _PROMPTS_FILE.with_suffix(".tmp")
    with open(temporary, "w", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        json.dump(prompts, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, _PROMPTS_FILE)


def update_prompt(prompt_id, text, title):
    with _lock:
        prompts = _load()
        for prompt in prompts:
            if prompt["id"] == prompt_id:
                prompt.update(text=text, title=title)
                _save(prompts)
                return prompt
        return None


def delete_prompt(prompt_id):
    with _lock:
        prompts = _load()
        remaining = [prompt for prompt in prompts if prompt["id"] != prompt_id]
        if len(remaining) == len(prompts):
            return False
        _save(remaining)
        return True
