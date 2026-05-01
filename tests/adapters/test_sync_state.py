"""Tests for migration 0002, registry, and `Mesh.{get,set}_sync_state`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.adapters import (
    AdapterAlreadyRegisteredError,
    AdapterNotRegisteredError,
    SessionReference,
    SessionTranscript,
    SourceAdapter,
    SyncState,
    get_adapter,
    list_adapters,
    register_adapter,
)
from context_mesh.adapters import registry as _registry
from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path


@pytest.fixture()
def mesh() -> Iterator[Mesh]:
    m = Mesh.local(":memory:")
    try:
        yield m
    finally:
        m.close()


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    _registry._REGISTRY.clear()
    try:
        yield
    finally:
        _registry._REGISTRY.clear()


def _last_audit_row(mesh: Mesh) -> tuple[str, str, str | None]:
    row = mesh.connection.execute(
        "SELECT event_type, actor, metadata FROM audit ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    return (row[0], row[1], row[2])


# =============================================================================
# A. Migration 0002 shape
# =============================================================================


def test_migration_creates_adapter_sync_state_table(mesh: Mesh) -> None:
    row = mesh.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='adapter_sync_state'"
    ).fetchone()
    assert row == ("adapter_sync_state",)


def test_migration_columns_match_schema(mesh: Mesh) -> None:
    rows = mesh.connection.execute("PRAGMA table_info('adapter_sync_state')").fetchall()
    by_name = {row[1]: row for row in rows}
    assert set(by_name) == {"adapter_name", "cursor", "last_synced_at", "state_json"}
    # adapter_name is the primary key
    assert by_name["adapter_name"][5] == 1
    # cursor and state_json are NOT NULL with defaults
    assert by_name["cursor"][3] == 1
    assert by_name["cursor"][4] == "''"
    assert by_name["state_json"][3] == 1
    assert by_name["state_json"][4] == "'{}'"
    # last_synced_at is nullable
    assert by_name["last_synced_at"][3] == 0


def test_migration_records_schema_version_row(mesh: Mesh) -> None:
    rows = mesh.connection.execute(
        "SELECT version, name FROM schema_version ORDER BY version"
    ).fetchall()
    assert (2, "0002_adapter_sync_state") in rows


def test_migration_idempotent_on_file_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    m1 = Mesh.local(db_path)
    m1.close()
    m2 = Mesh.local(db_path)
    try:
        rows = m2.connection.execute(
            "SELECT version FROM schema_version WHERE version = 2"
        ).fetchall()
        assert rows == [(2,)]
    finally:
        m2.close()


# =============================================================================
# B. get_sync_state / set_sync_state behavior
# =============================================================================


def test_get_sync_state_returns_none_when_absent(mesh: Mesh) -> None:
    assert mesh.get_sync_state("agent-memory") is None


def test_set_sync_state_round_trip(mesh: Mesh) -> None:
    written = mesh.set_sync_state(
        "agent-memory",
        cursor="2026-04-30T12:00:00Z",
        state={"seen_hashes": ["h1", "h2"]},
    )
    assert isinstance(written, SyncState)
    assert written.adapter_name == "agent-memory"
    assert written.cursor == "2026-04-30T12:00:00Z"
    assert written.last_synced_at is not None
    assert written.state == {"seen_hashes": ["h1", "h2"]}

    fetched = mesh.get_sync_state("agent-memory")
    assert fetched is not None
    assert fetched.cursor == written.cursor
    assert fetched.last_synced_at == written.last_synced_at
    assert fetched.state == written.state


def test_set_sync_state_upserts_single_row(mesh: Mesh) -> None:
    mesh.set_sync_state("entire", cursor="sha-1", state={"k": 1})
    mesh.set_sync_state("entire", cursor="sha-2", state={"k": 2})
    count = mesh.connection.execute(
        "SELECT COUNT(*) FROM adapter_sync_state WHERE adapter_name = ?",
        ("entire",),
    ).fetchone()[0]
    assert count == 1
    fetched = mesh.get_sync_state("entire")
    assert fetched is not None
    assert fetched.cursor == "sha-2"
    assert fetched.state == {"k": 2}


def test_set_sync_state_round_trips_nested_dict(mesh: Mesh) -> None:
    state: dict[str, Any] = {
        "outer": {"inner": [1, 2, 3], "flag": True},
        "list": ["a", "b"],
    }
    mesh.set_sync_state("nested", cursor="c", state=state)
    fetched = mesh.get_sync_state("nested")
    assert fetched is not None
    assert fetched.state == state


def test_set_sync_state_rejects_empty_adapter_name(mesh: Mesh) -> None:
    with pytest.raises(ValueError):
        mesh.set_sync_state("", cursor="c", state={})
    with pytest.raises(ValueError):
        mesh.set_sync_state("   ", cursor="c", state={})


def test_set_sync_state_rejects_unserializable_state(mesh: Mesh) -> None:
    with pytest.raises(ValueError):
        mesh.set_sync_state("a", cursor="c", state={"bad": object()})


def test_set_sync_state_emits_sync_pull_audit(mesh: Mesh) -> None:
    mesh.set_sync_state(
        "agent-memory",
        cursor="cur-1",
        state={"seen_hashes": ["h1", "h2"], "other": 42},
    )
    event_type, actor, metadata_json = _last_audit_row(mesh)
    assert event_type == "sync_pull"
    assert actor == "mesh"
    assert metadata_json is not None
    metadata = json.loads(metadata_json)
    assert metadata["action"] == "set_sync_state"
    assert metadata["adapter"] == "agent-memory"
    assert metadata["cursor"] == "cur-1"
    assert metadata["state_keys"] == ["other", "seen_hashes"]
    # Audit must NOT contain the raw state values.
    metadata_dump = json.dumps(metadata)
    assert "h1" not in metadata_dump
    assert "h2" not in metadata_dump
    assert "42" not in metadata_dump


def test_get_sync_state_emits_no_audit(mesh: Mesh) -> None:
    mesh.set_sync_state("a", cursor="c", state={})
    before = mesh.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    mesh.get_sync_state("a")
    mesh.get_sync_state("missing")
    after = mesh.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert before == after


def test_get_sync_state_raises_on_corrupt_state_json(mesh: Mesh) -> None:
    mesh.connection.execute(
        "INSERT INTO adapter_sync_state (adapter_name, cursor, last_synced_at, state_json) "
        "VALUES (?, ?, ?, ?)",
        ("broken", "c", 1, "not-json"),
    )
    mesh.connection.commit()
    with pytest.raises(RuntimeError):
        mesh.get_sync_state("broken")


# =============================================================================
# C. Registry
# =============================================================================


class _StubAdapter:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def discover(self) -> list[SessionReference]:
        return []

    def fetch(self, reference: SessionReference) -> SessionTranscript:
        return SessionTranscript(reference=reference, text="")

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


def test_registry_register_and_get() -> None:
    adapter = _StubAdapter("alpha")
    register_adapter(adapter)
    fetched: SourceAdapter = get_adapter("alpha")
    assert fetched is adapter


def test_registry_rejects_duplicate_name() -> None:
    register_adapter(_StubAdapter("alpha"))
    with pytest.raises(AdapterAlreadyRegisteredError):
        register_adapter(_StubAdapter("alpha"))


def test_registry_rejects_unknown_name() -> None:
    with pytest.raises(AdapterNotRegisteredError):
        get_adapter("nope")


def test_registry_list_is_sorted() -> None:
    register_adapter(_StubAdapter("zulu"))
    register_adapter(_StubAdapter("alpha"))
    register_adapter(_StubAdapter("mike"))
    assert list_adapters() == ("alpha", "mike", "zulu")


def test_registry_rejects_non_protocol_object() -> None:
    with pytest.raises(TypeError):
        register_adapter(object())  # type: ignore[arg-type]
