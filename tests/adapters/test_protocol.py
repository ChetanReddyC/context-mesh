"""Tests for the `SourceAdapter` Protocol and adapter dataclasses."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.adapters import (
    SessionReference,
    SessionTranscript,
    SourceAdapter,
    SyncState,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _ref() -> SessionReference:
    return SessionReference(
        adapter_name="dummy",
        source_id="s-1",
        repo="repo-a",
        agent="claude-code",
        started_at=1,
    )


class _DummyAdapter:
    @property
    def name(self) -> str:
        return "dummy"

    def discover(self) -> list[SessionReference]:
        return [_ref()]

    def fetch(self, reference: SessionReference) -> SessionTranscript:
        return SessionTranscript(reference=reference, text="hello")

    def sync_state(self) -> SyncState | None:
        return None

    def set_sync_state(self, state: SyncState) -> None:
        return None

    def seen_key(self, reference: SessionReference) -> str:
        return reference.source_id

    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]:
        return "", {}


# --- A. Runtime Protocol checks -----------------------------------------------------------


def test_source_adapter_runtime_check_accepts_dummy() -> None:
    assert isinstance(_DummyAdapter(), SourceAdapter) is True


def test_source_adapter_runtime_check_rejects_empty_object() -> None:
    assert isinstance(object(), SourceAdapter) is False


def test_source_adapter_runtime_check_rejects_partial_class() -> None:
    class _Partial:
        @property
        def name(self) -> str:
            return "partial"

        def discover(self) -> list[SessionReference]:
            return []

        def sync_state(self) -> SyncState | None:
            return None

        def set_sync_state(self, state: SyncState) -> None:
            return None

    assert isinstance(_Partial(), SourceAdapter) is False


# --- B. Dataclass shape: frozen + slots ---------------------------------------------------


def test_session_reference_is_frozen() -> None:
    r = _ref()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.repo = "other"  # type: ignore[misc]


def test_session_transcript_is_frozen() -> None:
    t = SessionTranscript(reference=_ref(), text="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.text = "other"  # type: ignore[misc]


def test_sync_state_is_frozen() -> None:
    s = SyncState(adapter_name="dummy", cursor="c", last_synced_at=None, state={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.cursor = "other"  # type: ignore[misc]


def test_session_reference_uses_slots() -> None:
    assert not hasattr(_ref(), "__dict__")


def test_session_transcript_uses_slots() -> None:
    t = SessionTranscript(reference=_ref(), text="x")
    assert not hasattr(t, "__dict__")


def test_sync_state_uses_slots() -> None:
    s = SyncState(adapter_name="dummy", cursor="c", last_synced_at=None, state={})
    assert not hasattr(s, "__dict__")


# --- C. Locked field set ------------------------------------------------------------------

_EXPECTED_REFERENCE_FIELDS: tuple[str, ...] = (
    "adapter_name",
    "source_id",
    "repo",
    "agent",
    "started_at",
    "branch",
    "commit_sha",
    "transcript_uri",
    "metadata",
)


def test_session_reference_field_shape() -> None:
    actual = tuple(f.name for f in dataclasses.fields(SessionReference))
    assert actual == _EXPECTED_REFERENCE_FIELDS
