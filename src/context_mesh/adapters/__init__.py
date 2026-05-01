"""Source adapters — Protocol, dataclasses, and a process-local registry."""

from __future__ import annotations

from context_mesh.adapters.agent_memory import AgentMemoryAdapter
from context_mesh.adapters.entire import EntireAdapter
from context_mesh.adapters.protocol import (
    SessionReference,
    SessionTranscript,
    SourceAdapter,
    SyncState,
)
from context_mesh.adapters.registry import (
    AdapterAlreadyRegisteredError,
    AdapterNotRegisteredError,
    get_adapter,
    list_adapters,
    register_adapter,
)
from context_mesh.adapters.sync import SkipRecord, SkipStage, SyncResult

__all__ = [
    "AdapterAlreadyRegisteredError",
    "AdapterNotRegisteredError",
    "AgentMemoryAdapter",
    "EntireAdapter",
    "SessionReference",
    "SessionTranscript",
    "SkipRecord",
    "SkipStage",
    "SourceAdapter",
    "SyncResult",
    "SyncState",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
