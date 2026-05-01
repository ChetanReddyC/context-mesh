"""SyncResult and SkipRecord — return types for Mesh.sync."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SkipStage = Literal["fetch", "distill", "persist"]


@dataclass(frozen=True, slots=True)
class SkipRecord:
    source_id: str
    stage: SkipStage
    reason: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    adapter_name: str
    discovered: int
    fetched: int
    memory_nodes_added: int
    skipped: tuple[SkipRecord, ...]
    cursor: str
    state_keys_count: int
    elapsed_ms: int
    dry_run: bool = False


__all__ = ["SkipRecord", "SkipStage", "SyncResult"]
