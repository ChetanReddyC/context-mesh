"""Tests for `Mesh.sync` — the Phase 5 sync orchestrator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.adapters import (
    SessionReference,
    SessionTranscript,
    SkipRecord,
    SyncResult,
    SyncState,
)
from context_mesh.api import Mesh
from context_mesh.distillation import DistillerCandidate
from context_mesh.embeddings import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


# ----- fakes -----------------------------------------------------------------


@dataclass
class _FakeAdapter:
    """In-memory adapter implementing the full SourceAdapter Protocol.

    `references` is the discovery list. `transcripts` maps source_id -> text.
    `fetch_errors` causes `fetch` to raise for the listed source_ids.
    `name_value` controls the registry-friendly name.
    """

    references: list[SessionReference]
    transcripts: dict[str, str] = field(default_factory=dict)
    fetch_errors: set[str] = field(default_factory=set)
    name_value: str = "fake"
    state_storage: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.name_value

    def discover(self) -> list[SessionReference]:
        return list(self.references)

    def fetch(self, reference: SessionReference) -> SessionTranscript:
        if reference.source_id in self.fetch_errors:
            raise RuntimeError(f"boom: {reference.source_id}")
        return SessionTranscript(
            reference=reference,
            text=self.transcripts.get(reference.source_id, "default text"),
        )

    def sync_state(self) -> SyncState | None:
        if not self.state_storage:
            return None
        return SyncState(
            adapter_name=self.name_value,
            cursor=str(self.state_storage.get("cursor", "")),
            last_synced_at=self.state_storage.get("last_synced_at"),
            state=dict(self.state_storage.get("state", {})),
        )

    def set_sync_state(self, state: SyncState) -> None:
        self.state_storage = {
            "cursor": state.cursor,
            "last_synced_at": state.last_synced_at,
            "state": dict(state.state),
        }

    def seen_key(self, reference: SessionReference) -> str:
        return reference.source_id

    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]:
        existing_keys: list[str] = []
        if existing is not None:
            raw = existing.state.get("seen_keys", [])
            if isinstance(raw, list):
                existing_keys = list(raw)
        merged = sorted(set(existing_keys) | {self.seen_key(r) for r in processed_refs})
        if processed_refs:
            cursor = self.seen_key(processed_refs[-1])
        else:
            cursor = existing.cursor if existing is not None else ""
        return cursor, {"seen_keys": merged}


@dataclass(frozen=True)
class _FakeDistiller:
    """Yields one episodic DistillerCandidate per non-empty input."""

    name_value: str = "fake-distiller/v1"
    body_template: str = "fake body for: {snippet}"

    @property
    def name(self) -> str:
        return self.name_value

    def distill(self, session_text: str) -> list[DistillerCandidate]:
        text = session_text.strip()
        if not text:
            return []
        return [
            DistillerCandidate(
                body=self.body_template.format(snippet=text[:32]),
                headline=text.splitlines()[0][:60],
                kind="episodic",
            )
        ]


def _make_ref(source_id: str, *, started_at: int = 1, repo: str = "repo-x") -> SessionReference:
    return SessionReference(
        adapter_name="fake",
        source_id=source_id,
        repo=repo,
        agent="claude-code",
        started_at=started_at,
    )


# ----- fixtures --------------------------------------------------------------


@pytest.fixture()
def mesh() -> Iterator[Mesh]:
    m = Mesh.local(":memory:")
    try:
        yield m
    finally:
        m.close()


@pytest.fixture()
def embedder() -> DeterministicEmbeddingProvider:
    return DeterministicEmbeddingProvider()


# ----- 1. Happy-path orchestrator -------------------------------------------


def test_sync_happy_path_runs_full_pipeline(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("s1", started_at=10), _make_ref("s2", started_at=20)]
    adapter = _FakeAdapter(
        references=refs,
        transcripts={"s1": "session one body", "s2": "session two body"},
    )
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)

    assert isinstance(result, SyncResult)
    assert result.adapter_name == "fake"
    assert result.discovered == 2
    assert result.fetched == 2
    assert result.memory_nodes_added == 2
    assert result.skipped == ()
    assert result.cursor == "s2"
    assert result.state_keys_count == 2
    assert result.dry_run is False
    assert result.elapsed_ms >= 0

    nodes = mesh.list_nodes()
    assert len(nodes) == 2


# ----- 2. Dry run does not persist or commit state --------------------------


def test_sync_dry_run_does_not_persist_or_commit_state(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("s1"), _make_ref("s2")]
    adapter = _FakeAdapter(references=refs, transcripts={"s1": "a", "s2": "b"})
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder, dry_run=True)

    assert result.dry_run is True
    assert result.discovered == 2
    assert result.fetched == 0
    assert result.memory_nodes_added == 0
    assert result.skipped == ()
    assert result.cursor == ""
    assert result.state_keys_count == 0
    assert mesh.list_nodes() == []
    assert mesh.get_sync_state(adapter.name) is None


# ----- 3. Idempotent rerun (no new nodes the second time) -------------------


def test_sync_idempotent_when_adapter_filters_seen_refs(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    """A real adapter filters seen refs in `discover()`; we simulate that here."""
    refs = [_make_ref("s1"), _make_ref("s2")]
    adapter = _FakeAdapter(references=list(refs), transcripts={"s1": "a", "s2": "b"})
    distiller = _FakeDistiller()

    first = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)
    assert first.fetched == 2

    # Simulate a real adapter: after sync, `discover` returns only unseen refs.
    adapter.references = []

    second = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)
    assert second.discovered == 0
    assert second.fetched == 0
    assert second.memory_nodes_added == 0
    assert mesh.list_nodes() == mesh.list_nodes()  # unchanged
    assert len(mesh.list_nodes()) == 2


# ----- 4. Partial fetch failure becomes a SkipRecord ------------------------


def test_sync_partial_fetch_failure_records_skip(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("ok"), _make_ref("bad")]
    adapter = _FakeAdapter(
        references=refs,
        transcripts={"ok": "good body", "bad": "ignored"},
        fetch_errors={"bad"},
    )
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)

    assert result.fetched == 1
    assert result.memory_nodes_added == 1
    assert len(result.skipped) == 1
    skip = result.skipped[0]
    assert isinstance(skip, SkipRecord)
    assert skip.source_id == "bad"
    assert skip.stage == "fetch"
    assert "boom: bad" in skip.reason


# ----- 5. Distill exception becomes a SkipRecord ----------------------------


def test_sync_distill_failure_records_skip(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    @dataclass(frozen=True)
    class _RaisingDistiller:
        name_value: str = "raising/v1"

        @property
        def name(self) -> str:
            return self.name_value

        def distill(self, session_text: str) -> list[DistillerCandidate]:
            raise RuntimeError("distill failed")

    refs = [_make_ref("s1")]
    adapter = _FakeAdapter(references=refs, transcripts={"s1": "body"})
    distiller = _RaisingDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)

    assert result.fetched == 0
    assert result.memory_nodes_added == 0
    assert len(result.skipped) == 1
    assert result.skipped[0].stage == "distill"
    assert "distill failed" in result.skipped[0].reason


# ----- 6. Duplicate content_hash IntegrityError becomes SkipRecord ----------


def test_sync_duplicate_content_hash_skipped(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    """Two refs with the same body produce the same content_hash; the second
    persistence raises IntegrityError, which the orchestrator turns into a skip.

    Note: real distill commits each candidate independently; the second
    candidate's commit hits the unique constraint on `content_hash`.
    """
    refs = [_make_ref("a"), _make_ref("b")]
    # Same body for both → same DistillerCandidate body → same content_hash
    adapter = _FakeAdapter(references=refs, transcripts={"a": "same", "b": "same"})
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)

    # First persists, second's distill IntegrityError becomes a skip.
    assert result.memory_nodes_added == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].stage == "distill"
    assert "duplicate content_hash" in result.skipped[0].reason


# ----- 7. Limit caps references processed ------------------------------------


def test_sync_limit_caps_processed_references(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref(f"s{i}", started_at=i) for i in range(5)]
    transcripts = {f"s{i}": f"body-{i}" for i in range(5)}
    adapter = _FakeAdapter(references=refs, transcripts=transcripts)
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder, limit=2)

    assert result.discovered == 5
    assert result.fetched == 2
    assert result.memory_nodes_added == 2


# ----- 8. limit=0 short-circuits to nothing fetched -------------------------


def test_sync_limit_zero_short_circuits(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("a"), _make_ref("b")]
    adapter = _FakeAdapter(references=refs, transcripts={"a": "x", "b": "y"})
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder, limit=0)

    assert result.discovered == 2
    assert result.fetched == 0
    assert result.memory_nodes_added == 0
    assert mesh.list_nodes() == []


def test_sync_negative_limit_raises(mesh: Mesh, embedder: DeterministicEmbeddingProvider) -> None:
    refs = [_make_ref("a")]
    adapter = _FakeAdapter(references=refs, transcripts={"a": "x"})
    distiller = _FakeDistiller()

    with pytest.raises(ValueError):
        mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder, limit=-1)


# ----- 9. scope+session rows created idempotently ---------------------------


def test_sync_creates_default_scope_and_session_rows(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("s1", started_at=42, repo="alpha")]
    adapter = _FakeAdapter(references=refs, transcripts={"s1": "some body"})
    distiller = _FakeDistiller()

    mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)

    scope_row = mesh.connection.execute(
        "SELECT id, level FROM scopes WHERE id = 'default'"
    ).fetchone()
    assert scope_row == ("default", "team")

    sess_row = mesh.connection.execute(
        "SELECT id, repo, agent, started_at FROM sessions WHERE id = 's1'"
    ).fetchone()
    assert sess_row == ("s1", "alpha", "claude-code", 42)

    # Re-running with new refs must not break / duplicate the default scope.
    adapter2 = _FakeAdapter(
        references=[_make_ref("s2", started_at=43, repo="alpha")],
        transcripts={"s2": "another body"},
    )
    mesh.sync(adapter=adapter2, distiller=distiller, embedder=embedder)
    count = mesh.connection.execute("SELECT COUNT(*) FROM scopes WHERE id = 'default'").fetchone()[
        0
    ]
    assert count == 1


# ----- 10. Run-summary audit row -------------------------------------------


def test_sync_emits_run_summary_audit_row(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("a"), _make_ref("b")]
    adapter = _FakeAdapter(references=refs, transcripts={"a": "x body", "b": "y body"})
    distiller = _FakeDistiller()

    mesh.sync(
        adapter=adapter,
        distiller=distiller,
        embedder=embedder,
        actor="test:sync",
    )

    row = mesh.connection.execute(
        "SELECT event_type, actor, metadata FROM audit "
        "WHERE event_type = 'sync_pull' "
        "AND json_extract(metadata, '$.action') = 'sync_run' "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == "sync_pull"
    assert row[1] == "test:sync"
    metadata = json.loads(row[2])
    assert metadata["adapter"] == "fake"
    assert metadata["discovered"] == 2
    assert metadata["fetched"] == 2
    assert metadata["memory_nodes_added"] == 2
    assert metadata["skipped_count"] == 0
    assert metadata["dry_run"] is False


# ----- 11. state_keys_count counts list lengths in merged state -------------


def test_sync_state_keys_count_matches_merged_seen_keys(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [_make_ref("a"), _make_ref("b"), _make_ref("c")]
    adapter = _FakeAdapter(references=refs, transcripts={"a": "1", "b": "2", "c": "3"})
    distiller = _FakeDistiller()

    result = mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)
    assert result.state_keys_count == 3

    # Reload state from db; the adapter's set_sync_state should have been called.
    persisted = mesh.get_sync_state(adapter.name)
    assert persisted is not None
    assert persisted.cursor == "c"
    assert sorted(persisted.state["seen_keys"]) == ["a", "b", "c"]


# ----- 12. Oldest-first ordering preserved ----------------------------------


def test_sync_processes_in_oldest_first_order(
    mesh: Mesh, embedder: DeterministicEmbeddingProvider
) -> None:
    refs = [
        _make_ref("alpha", started_at=1),
        _make_ref("bravo", started_at=2),
        _make_ref("charlie", started_at=3),
    ]
    adapter = _FakeAdapter(
        references=refs,
        transcripts={"alpha": "a-body", "bravo": "b-body", "charlie": "c-body"},
    )
    distiller = _FakeDistiller()

    mesh.sync(adapter=adapter, distiller=distiller, embedder=embedder)

    persisted = mesh.get_sync_state(adapter.name)
    assert persisted is not None
    # Cursor reflects the LAST processed ref (oldest-first ordering).
    assert persisted.cursor == "charlie"

    # And the per-node `add` audit rows for distillation arrived in that order.
    rows = mesh.connection.execute(
        "SELECT json_extract(metadata, '$.source_session_id') FROM audit "
        "WHERE event_type = 'add' AND json_extract(metadata, '$.distiller') IS NOT NULL "
        "ORDER BY rowid"
    ).fetchall()
    ids = [r[0] for r in rows]
    assert ids == ["alpha", "bravo", "charlie"]
