"""Tests for context_mesh._audit."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING

import pytest

from context_mesh._audit import log
from context_mesh.storage.backend import SqliteVecBackend

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture()
def backend() -> Iterator[SqliteVecBackend]:
    b = SqliteVecBackend(":memory:")
    try:
        yield b
    finally:
        b.close()


def test_log_inserts_minimal_event(backend: SqliteVecBackend) -> None:
    aid = log(backend.connection, "retrieve", "agent-001")
    rows = backend.connection.execute(
        "SELECT id, event_type, actor, node_ids, query, result_count, metadata, timestamp "
        "FROM audit"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == aid
    assert row[1] == "retrieve"
    assert row[2] == "agent-001"
    assert row[3] is None
    assert row[4] is None
    assert row[5] is None
    assert row[6] is None
    assert row[7] > 0


def test_log_inserts_full_event(backend: SqliteVecBackend) -> None:
    aid = log(
        backend.connection,
        "retrieve",
        "agent-007",
        node_ids=["a", "b"],
        query="how do we deploy?",
        result_count=2,
        metadata={"latency_ms": 42, "cluster": [1, 2]},
    )
    row = backend.connection.execute(
        "SELECT id, event_type, actor, node_ids, query, result_count, metadata, timestamp "
        "FROM audit WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row is not None
    assert row[1] == "retrieve"
    assert row[2] == "agent-007"
    assert json.loads(row[3]) == ["a", "b"]
    assert row[4] == "how do we deploy?"
    assert row[5] == 2
    assert json.loads(row[6]) == {"latency_ms": 42, "cluster": [1, 2]}
    assert row[7] > 0


def test_log_returns_id(backend: SqliteVecBackend) -> None:
    aid = log(backend.connection, "add", "system")
    assert isinstance(aid, str)
    assert len(aid) == 36
    row = backend.connection.execute("SELECT id FROM audit").fetchone()
    assert row[0] == aid


def test_log_id_is_unique(backend: SqliteVecBackend) -> None:
    a1 = log(backend.connection, "add", "system")
    a2 = log(backend.connection, "add", "system")
    assert a1 != a2
    rows = backend.connection.execute("SELECT id FROM audit ORDER BY id").fetchall()
    ids = {row[0] for row in rows}
    assert ids == {a1, a2}


def test_log_rejects_invalid_event_type(backend: SqliteVecBackend) -> None:
    with pytest.raises(ValueError):
        log(backend.connection, "not_a_thing", "actor")  # type: ignore[arg-type]
    count = backend.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert count == 0


def test_log_rejects_empty_actor(backend: SqliteVecBackend) -> None:
    with pytest.raises(ValueError):
        log(backend.connection, "retrieve", "")


def test_log_emits_structured_log_record(
    backend: SqliteVecBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="context_mesh.audit"):
        aid = log(
            backend.connection,
            "add",
            "system",
            node_ids=["n1"],
            metadata={"k": "v"},
        )

    matching = [r for r in caplog.records if r.name == "context_mesh.audit"]
    assert len(matching) == 1
    record = matching[0]
    assert record.audit_id == aid  # type: ignore[attr-defined]
    assert record.event_type == "add"  # type: ignore[attr-defined]
    assert record.actor == "system"  # type: ignore[attr-defined]
    assert record.node_ids == ["n1"]  # type: ignore[attr-defined]


def test_log_commits_immediately(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    backend = SqliteVecBackend(db_path)
    try:
        aid = log(backend.connection, "add", "system")
    finally:
        observer = sqlite3.connect(db_path)
        try:
            row = observer.execute("SELECT id FROM audit WHERE id = ?", (aid,)).fetchone()
            assert row is not None
            assert row[0] == aid
        finally:
            observer.close()
        backend.close()
