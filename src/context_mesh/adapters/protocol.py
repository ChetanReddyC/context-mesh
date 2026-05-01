"""SourceAdapter Protocol + reference / transcript / sync-state dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class SessionReference:
    adapter_name: str
    source_id: str
    repo: str
    agent: str
    started_at: int
    branch: str | None = None
    commit_sha: str | None = None
    transcript_uri: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SessionTranscript:
    reference: SessionReference
    text: str
    ended_at: int | None = None
    turn_count: int | None = None
    token_usage: int | None = None


@dataclass(frozen=True, slots=True)
class SyncState:
    """Durable per-adapter sync state.

    `last_synced_at` is server-stamped on writes via `Mesh.set_sync_state`;
    any value supplied on input dataclass instances is advisory only.
    """

    adapter_name: str
    cursor: str
    last_synced_at: int | None
    state: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def discover(self) -> list[SessionReference]: ...

    def fetch(self, reference: SessionReference) -> SessionTranscript: ...

    def sync_state(self) -> SyncState | None: ...

    def set_sync_state(self, state: SyncState) -> None: ...

    def seen_key(self, reference: SessionReference) -> str: ...

    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]: ...


__all__ = [
    "SessionReference",
    "SessionTranscript",
    "SourceAdapter",
    "SyncState",
]
