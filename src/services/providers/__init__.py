from src.services.providers.kiro import KiroProvider
from src.services.providers.opencode import OpenCodeProvider
from src.services.providers.claude_code import ClaudeCodeProvider


_PROVIDERS = {provider.id: provider for provider in (KiroProvider(), OpenCodeProvider(), ClaudeCodeProvider())}


def get_provider(backend):
    if not isinstance(backend, str) or backend not in _PROVIDERS:
        raise ValueError('Unknown agent backend')
    return _PROVIDERS[backend]


def providers():
    return tuple(_PROVIDERS.values())


def provider_ids():
    return tuple(_PROVIDERS)


def legacy_provider(native_id):
    for provider in providers():
        if provider.matches_native_id(native_id):
            return provider.id
    return 'kiro'
