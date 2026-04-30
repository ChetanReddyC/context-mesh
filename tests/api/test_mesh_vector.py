"""Tests for `context_mesh.api.mesh.Mesh` vector insert path methods."""

from __future__ import annotations

import json
import random
import sqlite3
import struct
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryNode


@pytest.fixture()
def mesh_with_vector_seed(tmp_path: Path) -> Iterator[tuple[Mesh, MemoryNode]]:
    mesh = Mesh.local(tmp_path / "test.db")
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-1", "test", "private", 100),
    )
    mesh.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("ses-1", "test-repo", "claude-code", 100),
    )
    mesh.connection.commit()
    node = mesh.make_node(
        kind="semantic",
        body="test",
        headline="h",
        scope_id="scope-1",
        source_session_id="ses-1",
        source_repo="repo",
    )
    mesh.add(node)
    try:
        yield mesh, node
    finally:
        mesh.close()


def _random_embedding(dim: int = 384, seed: int = 42) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


_MODEL = "huggingface/all-MiniLM-L6-v2"


def _audit_rows_for_node(
    conn: sqlite3.Connection, node_id: str, action: str | None = None
) -> list[tuple[Any, ...]]:
    rows = list(
        conn.execute(
            "SELECT event_type, node_ids, metadata FROM audit WHERE node_ids LIKE ? ORDER BY rowid",
            (f'%"{node_id}"%',),
        ).fetchall()
    )
    if action is None:
        return rows
    out: list[tuple[Any, ...]] = []
    for row in rows:
        meta = row[2]
        if meta is None:
            continue
        parsed = json.loads(meta)
        if parsed.get("action") == action:
            out.append(row)
    return out


# ----- set_vector -----------------------------------------------------------


def test_set_vector_inserts_into_vec_nodes(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)

    count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vec_nodes WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    assert count == 1


def test_set_vector_inserts_into_vector_meta(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)

    row = mesh.connection.execute(
        "SELECT model, dimensions, embedded_at FROM vector_meta WHERE node_id = ?",
        (node.id,),
    ).fetchone()
    assert row is not None
    model, dims, embedded_at = row
    assert model == _MODEL
    assert dims == 384
    assert isinstance(embedded_at, int)
    assert embedded_at > 0


def test_set_vector_atomic_both_tables(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)

    vec_count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vec_nodes WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    meta_count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vector_meta WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    assert vec_count == 1
    assert meta_count == 1


def test_set_vector_accepts_list_floats(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    embedding = _random_embedding()
    mesh.set_vector(node.id, embedding, _MODEL)

    fetched = mesh.get_vector(node.id)
    assert fetched is not None
    blob, model, dims = fetched
    assert struct.unpack(f"<{dims}f", blob) == pytest.approx(embedding)
    assert model == _MODEL


def test_set_vector_accepts_bytes(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    floats = _random_embedding()
    payload = struct.pack("<384f", *floats)
    mesh.set_vector(node.id, payload, _MODEL)

    fetched = mesh.get_vector(node.id)
    assert fetched is not None
    blob, _, _ = fetched
    assert blob == payload


def test_set_vector_rejects_wrong_dimension_list(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    with pytest.raises(ValueError, match="embedding dimension"):
        mesh.set_vector(node.id, [0.0] * 100, _MODEL)


def test_set_vector_rejects_wrong_dimension_bytes(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    with pytest.raises(ValueError, match="embedding bytes length"):
        mesh.set_vector(node.id, b"\x00" * 100, _MODEL)


def test_set_vector_rejects_missing_node_fk(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, _ = mesh_with_vector_seed
    with pytest.raises(sqlite3.IntegrityError):
        mesh.set_vector("non-existent-node-id", _random_embedding(), _MODEL)


# ----- re-embed -------------------------------------------------------------


def test_set_vector_rejects_existing_without_replace_flag(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(seed=1), _MODEL)
    with pytest.raises(ValueError, match="vector already exists"):
        mesh.set_vector(node.id, _random_embedding(seed=2), _MODEL)


def test_set_vector_replace_true_overwrites(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    first = _random_embedding(seed=1)
    second = _random_embedding(seed=2)
    mesh.set_vector(node.id, first, _MODEL)
    mesh.set_vector(node.id, second, "other-model", replace=True)

    fetched = mesh.get_vector(node.id)
    assert fetched is not None
    blob, model, dims = fetched
    assert struct.unpack(f"<{dims}f", blob) == pytest.approx(second)
    assert model == "other-model"


def test_set_vector_replace_emits_edit_audit_not_add(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(seed=1), _MODEL)
    mesh.set_vector(node.id, _random_embedding(seed=2), _MODEL, replace=True)

    rows = _audit_rows_for_node(mesh.connection, node.id, action="set_vector")
    event_types = [r[0] for r in rows]
    assert event_types == ["add", "edit"]
    edit_meta = json.loads(rows[1][2])
    assert edit_meta["replace"] is True


class _FlakyConn:
    """sqlite3.Connection proxy that fails on the first vec_nodes INSERT after a delete."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self._deletes_seen = 0

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "DELETE FROM vec_nodes" in sql:
            self._deletes_seen += 1
        if "INSERT INTO vec_nodes" in sql and self._deletes_seen >= 1:
            raise sqlite3.OperationalError("simulated mid-replace failure")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        self._real.commit()

    def rollback(self) -> None:
        self._real.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_set_vector_replace_atomic_on_failure(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace mid-flight failure must leave original vector intact."""
    mesh, node = mesh_with_vector_seed
    original = _random_embedding(seed=1)
    mesh.set_vector(node.id, original, _MODEL)

    real_conn = mesh.connection
    flaky = _FlakyConn(real_conn)
    monkeypatch.setattr(mesh.backend, "_conn", flaky)

    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        mesh.set_vector(node.id, _random_embedding(seed=2), _MODEL, replace=True)

    monkeypatch.undo()

    fetched = mesh.get_vector(node.id)
    assert fetched is not None
    blob, model, dims = fetched
    assert struct.unpack(f"<{dims}f", blob) == pytest.approx(original)
    assert model == _MODEL


# ----- get_vector / has_vector ----------------------------------------------


def test_get_vector_returns_tuple(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)

    fetched = mesh.get_vector(node.id)
    assert fetched is not None
    blob, model, dims = fetched
    assert isinstance(blob, bytes)
    assert len(blob) == 384 * 4
    assert model == _MODEL
    assert dims == 384


def test_get_vector_returns_none_for_missing(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    assert mesh.get_vector(node.id) is None
    assert mesh.get_vector("nope") is None


def test_has_vector_returns_correct_bool(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    assert mesh.has_vector(node.id) is False
    mesh.set_vector(node.id, _random_embedding(), _MODEL)
    assert mesh.has_vector(node.id) is True
    assert mesh.has_vector("missing") is False


# ----- delete_vector --------------------------------------------------------


def test_delete_vector_returns_true_for_existing(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)
    assert mesh.delete_vector(node.id) is True


def test_delete_vector_returns_false_for_missing(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    assert mesh.delete_vector(node.id) is False
    rows = _audit_rows_for_node(mesh.connection, node.id, action="delete_vector")
    assert rows == []


def test_delete_vector_removes_from_both_tables(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)
    mesh.delete_vector(node.id)

    vec_count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vec_nodes WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    meta_count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vector_meta WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    assert vec_count == 0
    assert meta_count == 0


def test_delete_vector_emits_audit(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)
    mesh.delete_vector(node.id)

    rows = _audit_rows_for_node(mesh.connection, node.id, action="delete_vector")
    assert len(rows) == 1
    assert rows[0][0] == "delete"


# ----- audit + cascade ------------------------------------------------------


def test_set_vector_audit_metadata_shape(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)

    rows = _audit_rows_for_node(mesh.connection, node.id, action="set_vector")
    assert len(rows) == 1
    event_type, node_ids_json, metadata_json = rows[0]
    assert event_type == "add"
    assert json.loads(node_ids_json) == [node.id]
    metadata = json.loads(metadata_json)
    assert metadata == {
        "action": "set_vector",
        "model": _MODEL,
        "dimensions": 384,
        "replace": False,
    }


def test_node_delete_cascades_vector_meta(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)
    mesh.delete(node.id)

    meta_count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vector_meta WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    assert meta_count == 0


def test_node_delete_vec_nodes_orphan_check(
    mesh_with_vector_seed: tuple[Mesh, MemoryNode],
) -> None:
    """Branch B applied: sqlite-vec virtual tables don't honor declarative FK
    cascade, so `Mesh.delete()` explicitly cleans `vec_nodes` first. After
    deleting a node we expect zero rows in `vec_nodes`."""
    mesh, node = mesh_with_vector_seed
    mesh.set_vector(node.id, _random_embedding(), _MODEL)
    mesh.delete(node.id)

    vec_count = mesh.connection.execute(
        "SELECT COUNT(*) FROM vec_nodes WHERE node_id = ?", (node.id,)
    ).fetchone()[0]
    assert vec_count == 0
