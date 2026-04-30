"""Tests for `context_mesh.api.mesh.Mesh` Node CRUD methods."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.api import Mesh
from context_mesh.api.types import MemoryNode

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture()
def mesh_with_seed(tmp_path: Path) -> Iterator[Mesh]:
    mesh = Mesh.local(tmp_path / "test.db")
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-1", "test", "private", 100),
    )
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-2", "team-test", "team", 100),
    )
    mesh.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("ses-1", "test-repo", "claude-code", 100),
    )
    mesh.connection.commit()
    try:
        yield mesh
    finally:
        mesh.close()


def _seeded_node(mesh: Mesh, **overrides: Any) -> MemoryNode:
    kwargs: dict[str, Any] = {
        "kind": "semantic",
        "body": "Always re-verify webhook timestamps.",
        "headline": "Re-verify webhook timestamps",
        "scope_id": "scope-1",
        "source_session_id": "ses-1",
        "source_repo": "payments",
    }
    kwargs.update(overrides)
    return mesh.make_node(**kwargs)


def _audit_rows(conn: sqlite3.Connection, event_type: str | None = None) -> list[tuple[Any, ...]]:
    if event_type is None:
        return list(conn.execute("SELECT * FROM audit").fetchall())
    return list(conn.execute("SELECT * FROM audit WHERE event_type = ?", (event_type,)).fetchall())


# ----- add ------------------------------------------------------------------


def test_add_inserts_node(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    row = mesh_with_seed.connection.execute(
        "SELECT id, body FROM nodes WHERE id = ?", (node.id,)
    ).fetchone()
    assert row == (node.id, node.body)


def test_add_returns_node_id(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    returned = mesh_with_seed.add(node)
    assert returned == node.id


def test_add_emits_audit_event(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    rows = _audit_rows(mesh_with_seed.connection, "add")
    assert len(rows) == 1
    row = mesh_with_seed.connection.execute(
        "SELECT event_type, actor, node_ids, metadata FROM audit WHERE event_type = 'add'"
    ).fetchone()
    event_type, actor, node_ids_json, metadata_json = row
    assert event_type == "add"
    assert actor == "mesh"
    assert json.loads(node_ids_json) == [node.id]
    metadata = json.loads(metadata_json)
    assert metadata == {"kind": "semantic", "scope_id": "scope-1"}


def test_add_rejects_invalid_kind(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    node.kind = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="invalid node kind"):
        mesh_with_seed.add(node)


def test_add_rejects_missing_scope_id_fk(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed, scope_id="does-not-exist")
    with pytest.raises(sqlite3.IntegrityError):
        mesh_with_seed.add(node)


def test_add_rejects_duplicate_content_hash(mesh_with_seed: Mesh) -> None:
    first = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(first)

    second = _seeded_node(mesh_with_seed)
    assert second.content_hash == first.content_hash
    assert second.id != first.id
    with pytest.raises(sqlite3.IntegrityError):
        mesh_with_seed.add(second)


# ----- get ------------------------------------------------------------------


def test_get_returns_added_node(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    fetched = mesh_with_seed.get(node.id)
    assert fetched is not None
    assert fetched.id == node.id
    assert fetched.body == node.body
    assert fetched.headline == node.headline


def test_get_returns_none_for_missing(mesh_with_seed: Mesh) -> None:
    assert mesh_with_seed.get("nope") is None


def test_get_round_trips_all_optional_fields(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(
        mesh_with_seed,
        summary="Short summary.",
        decisions=["d1", "d2"],
        failed_approaches=["f1"],
        warnings=["w1", "w2"],
        error_signatures=["E001"],
        cause_chain=["c1", "c2"],
        file_dependencies=["a.py", "b.py"],
        key_insight="The insight.",
        tags=["t1", "t2"],
        source_branch="main",
        source_commit="abc1234",
        decayed_at=999,
        importance=0.75,
        usage_count=3,
        helpful_count=2,
    )
    mesh_with_seed.add(node)

    fetched = mesh_with_seed.get(node.id)
    assert fetched == node


# ----- list_nodes -----------------------------------------------------------


def test_list_nodes_empty_returns_empty_list(mesh_with_seed: Mesh) -> None:
    assert mesh_with_seed.list_nodes() == []


def test_list_nodes_returns_all(mesh_with_seed: Mesh) -> None:
    for body in ("body one", "body two", "body three"):
        mesh_with_seed.add(_seeded_node(mesh_with_seed, body=body))

    nodes = mesh_with_seed.list_nodes()
    assert len(nodes) == 3
    assert {n.body for n in nodes} == {"body one", "body two", "body three"}


def test_list_nodes_filter_by_kind(mesh_with_seed: Mesh) -> None:
    mesh_with_seed.add(_seeded_node(mesh_with_seed, kind="semantic", body="rule a"))
    mesh_with_seed.add(_seeded_node(mesh_with_seed, kind="episodic", body="event b"))
    mesh_with_seed.add(_seeded_node(mesh_with_seed, kind="procedural", body="proc c"))

    semantics = mesh_with_seed.list_nodes(kind="semantic")
    assert [n.body for n in semantics] == ["rule a"]

    episodics = mesh_with_seed.list_nodes(kind="episodic")
    assert [n.body for n in episodics] == ["event b"]


def test_list_nodes_filter_by_scope_id(mesh_with_seed: Mesh) -> None:
    mesh_with_seed.add(_seeded_node(mesh_with_seed, scope_id="scope-1", body="s1"))
    mesh_with_seed.add(_seeded_node(mesh_with_seed, scope_id="scope-2", body="s2"))

    scoped = mesh_with_seed.list_nodes(scope_id="scope-2")
    assert [n.body for n in scoped] == ["s2"]


def test_list_nodes_filter_by_source_repo(mesh_with_seed: Mesh) -> None:
    mesh_with_seed.add(_seeded_node(mesh_with_seed, source_repo="payments", body="p1"))
    mesh_with_seed.add(_seeded_node(mesh_with_seed, source_repo="auth", body="a1"))

    repo = mesh_with_seed.list_nodes(source_repo="auth")
    assert [n.body for n in repo] == ["a1"]


def test_list_nodes_filter_decayed_excludes_when_false(mesh_with_seed: Mesh) -> None:
    fresh = _seeded_node(mesh_with_seed, body="fresh")
    decayed = _seeded_node(mesh_with_seed, body="decayed", decayed_at=500)
    mesh_with_seed.add(fresh)
    mesh_with_seed.add(decayed)

    alive = mesh_with_seed.list_nodes(decayed=False)
    assert [n.body for n in alive] == ["fresh"]


def test_list_nodes_filter_decayed_only_when_true(mesh_with_seed: Mesh) -> None:
    fresh = _seeded_node(mesh_with_seed, body="fresh")
    decayed = _seeded_node(mesh_with_seed, body="decayed", decayed_at=500)
    mesh_with_seed.add(fresh)
    mesh_with_seed.add(decayed)

    only_decayed = mesh_with_seed.list_nodes(decayed=True)
    assert [n.body for n in only_decayed] == ["decayed"]


def test_list_nodes_limit_and_offset(mesh_with_seed: Mesh) -> None:
    for i in range(5):
        node = _seeded_node(mesh_with_seed, body=f"body-{i}")
        node.created_at = 1000 + i
        node.updated_at = 1000 + i
        mesh_with_seed.add(node)

    page = mesh_with_seed.list_nodes(limit=2, offset=1)
    assert [n.body for n in page] == ["body-1", "body-2"]


def test_list_nodes_rejects_invalid_kind(mesh_with_seed: Mesh) -> None:
    with pytest.raises(ValueError, match="invalid node kind"):
        mesh_with_seed.list_nodes(kind="bogus")  # type: ignore[arg-type]


# ----- update ---------------------------------------------------------------


def test_update_modifies_field(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    refreshed = mesh_with_seed.update(node.id, {"headline": "Updated headline"})
    assert refreshed.headline == "Updated headline"

    direct = mesh_with_seed.get(node.id)
    assert direct is not None
    assert direct.headline == "Updated headline"


def test_update_bumps_updated_at(mesh_with_seed: Mesh, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_create = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: fixed_create)
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)
    assert node.updated_at == fixed_create

    fixed_update = 1_700_000_500
    monkeypatch.setattr(time, "time", lambda: fixed_update)
    refreshed = mesh_with_seed.update(node.id, {"headline": "Newer"})
    assert refreshed.updated_at == fixed_update
    assert refreshed.created_at == fixed_create


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other-id"),
        ("created_at", 0),
        ("content_hash", "deadbeef"),
        ("source_session_id", "ses-other"),
        ("source_repo", "other-repo"),
        ("scope_id", "scope-2"),
    ],
)
def test_update_rejects_immutable_field(mesh_with_seed: Mesh, field: str, value: Any) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    with pytest.raises(ValueError, match="unknown or immutable"):
        mesh_with_seed.update(node.id, {field: value})


def test_update_rejects_unknown_field(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    with pytest.raises(ValueError, match="unknown or immutable"):
        mesh_with_seed.update(node.id, {"not_a_field": 1})


def test_update_rejects_empty_fields(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    with pytest.raises(ValueError, match="no fields to update"):
        mesh_with_seed.update(node.id, {})


def test_update_returns_fresh_node(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    refreshed = mesh_with_seed.update(node.id, {"importance": 0.9, "usage_count": 7})
    assert refreshed.importance == 0.9
    assert refreshed.usage_count == 7
    assert refreshed.id == node.id


def test_update_emits_audit_event(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    mesh_with_seed.update(node.id, {"headline": "x", "importance": 0.5})

    rows = _audit_rows(mesh_with_seed.connection, "edit")
    assert len(rows) == 1
    row = mesh_with_seed.connection.execute(
        "SELECT actor, node_ids, metadata FROM audit WHERE event_type = 'edit'"
    ).fetchone()
    actor, node_ids_json, metadata_json = row
    assert actor == "mesh"
    assert json.loads(node_ids_json) == [node.id]
    assert json.loads(metadata_json) == {"fields": ["headline", "importance"]}


def test_update_validates_kind_value(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    with pytest.raises(ValueError, match="invalid node kind"):
        mesh_with_seed.update(node.id, {"kind": "bogus"})


def test_update_unknown_id_raises_no_audit(mesh_with_seed: Mesh) -> None:
    with pytest.raises(ValueError, match="not found"):
        mesh_with_seed.update("ghost", {"headline": "x"})

    rows = _audit_rows(mesh_with_seed.connection, "edit")
    assert rows == []


def test_update_serializes_json_list_fields(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)

    refreshed = mesh_with_seed.update(
        node.id,
        {
            "decisions": ["new-d1", "new-d2"],
            "tags": ["alpha", "beta"],
            "warnings": [],
        },
    )
    assert refreshed.decisions == ["new-d1", "new-d2"]
    assert refreshed.tags == ["alpha", "beta"]
    assert refreshed.warnings == []

    raw = mesh_with_seed.connection.execute(
        "SELECT decisions, tags, warnings FROM nodes WHERE id = ?", (node.id,)
    ).fetchone()
    decisions_raw, tags_raw, warnings_raw = raw
    assert json.loads(decisions_raw) == ["new-d1", "new-d2"]
    assert json.loads(tags_raw) == ["alpha", "beta"]
    assert json.loads(warnings_raw) == []


# ----- delete ---------------------------------------------------------------


def test_delete_returns_true_for_existing(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)
    assert mesh_with_seed.delete(node.id) is True


def test_delete_returns_false_for_missing(mesh_with_seed: Mesh) -> None:
    assert mesh_with_seed.delete("nope") is False
    rows = _audit_rows(mesh_with_seed.connection, "delete")
    assert rows == []


def test_delete_removes_row(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)
    mesh_with_seed.delete(node.id)

    assert mesh_with_seed.get(node.id) is None
    count = mesh_with_seed.connection.execute(
        "SELECT COUNT(*) FROM nodes WHERE id = ?", (node.id,)
    ).fetchone()[0]
    assert count == 0


def test_delete_emits_audit_event(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(mesh_with_seed)
    mesh_with_seed.add(node)
    mesh_with_seed.delete(node.id)

    rows = _audit_rows(mesh_with_seed.connection, "delete")
    assert len(rows) == 1
    row = mesh_with_seed.connection.execute(
        "SELECT actor, node_ids FROM audit WHERE event_type = 'delete'"
    ).fetchone()
    actor, node_ids_json = row
    assert actor == "mesh"
    assert json.loads(node_ids_json) == [node.id]


def test_delete_cascades_to_edges(mesh_with_seed: Mesh) -> None:
    a = _seeded_node(mesh_with_seed, body="node-a")
    b = _seeded_node(mesh_with_seed, body="node-b")
    mesh_with_seed.add(a)
    mesh_with_seed.add(b)

    mesh_with_seed.connection.execute(
        "INSERT INTO edges "
        "(id, from_node_id, to_node_id, relation, confidence, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("edge-1", a.id, b.id, "applies_to", 1.0, 100, "manual"),
    )
    mesh_with_seed.connection.commit()

    pre = mesh_with_seed.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert pre == 1

    mesh_with_seed.delete(a.id)

    post = mesh_with_seed.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert post == 0


# ----- make_node ------------------------------------------------------------


def test_make_node_generates_unique_ids(mesh_with_seed: Mesh) -> None:
    a = _seeded_node(mesh_with_seed)
    b = _seeded_node(mesh_with_seed)
    assert a.id != b.id


def test_make_node_stamps_timestamps(mesh_with_seed: Mesh) -> None:
    before = int(time.time())
    node = _seeded_node(mesh_with_seed)
    after = int(time.time())

    assert node.created_at == node.updated_at
    assert before - 2 <= node.created_at <= after + 2


def test_make_node_computes_content_hash(mesh_with_seed: Mesh) -> None:
    node = _seeded_node(
        mesh_with_seed,
        body="hash body",
        key_insight="the insight",
        decisions=["d1"],
    )
    expected = MemoryNode.compute_content_hash("hash body", "the insight", ["d1"])
    assert node.content_hash == expected


def test_make_node_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError, match="invalid node kind"):
        Mesh.make_node(
            kind="bogus",  # type: ignore[arg-type]
            body="b",
            headline="h",
            scope_id="scope-1",
            source_session_id="ses-1",
            source_repo="r",
        )
