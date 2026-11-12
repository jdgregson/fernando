import threading
import time

from src.services.providers import get_provider


_cache = {}
_lock = threading.Lock()


def list_models(backend):
    provider = get_provider(backend)
    with _lock:
        cached = _cache.get(backend)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
    result = provider.list_models()
    with _lock:
        _cache[backend] = (time.monotonic(), result)
    return result


def normalized_models(backend):
    return get_provider(backend).normalize_models(list_models(backend))
