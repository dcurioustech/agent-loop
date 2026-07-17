from __future__ import annotations

from .base import Provider, ProviderError

PROVIDERS: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    PROVIDERS[cls.name] = cls
    return cls


def get_provider(name: str) -> Provider:
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS)) or "<none>"
        raise ProviderError(f"Unknown provider '{name}'. Known: {known}")
    return PROVIDERS[name]()


def known_provider_names() -> list[str]:
    return sorted(PROVIDERS)


# Importing the concrete modules triggers @register side effects.
from . import antigravity as _antigravity  # noqa: E402, F401
from . import claude as _claude  # noqa: E402, F401
from . import codex as _codex  # noqa: E402, F401
from . import gemini as _gemini  # noqa: E402, F401
from . import grok as _grok  # noqa: E402, F401
