"""Process-local registry for SourceAdapters.

This module is single-threaded by design — adapters are expected to register
once at import / startup time and remain read-only thereafter. There is no
lock; concurrent registration is not supported in v1.
"""

from __future__ import annotations

from context_mesh.adapters.protocol import SourceAdapter

_REGISTRY: dict[str, SourceAdapter] = {}


class AdapterAlreadyRegisteredError(LookupError):
    """Raised when `register_adapter` is called with a name that is already taken."""


class AdapterNotRegisteredError(LookupError):
    """Raised when `get_adapter` is called with an unknown name."""


def register_adapter(adapter: SourceAdapter) -> None:
    if not isinstance(adapter, SourceAdapter):
        raise TypeError(
            f"register_adapter: expected SourceAdapter-conformant object, "
            f"got {type(adapter).__name__}"
        )
    name = adapter.name
    if name in _REGISTRY:
        raise AdapterAlreadyRegisteredError(f"adapter already registered: {name!r}")
    _REGISTRY[name] = adapter


def get_adapter(name: str) -> SourceAdapter:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise AdapterNotRegisteredError(f"no adapter registered under name: {name!r}") from exc


def list_adapters() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


__all__ = [
    "AdapterAlreadyRegisteredError",
    "AdapterNotRegisteredError",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
