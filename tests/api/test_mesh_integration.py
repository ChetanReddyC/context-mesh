"""Cross-cutting integration tests for `Mesh`.

These verify that node / edge / vector primitives compose correctly. Per-method
unit tests live in `tests/api/test_mesh_node_crud.py`, `test_mesh_edge_crud.py`,
and `test_mesh_vector.py` — this file deliberately avoids duplicating those.
"""

from __future__ import annotations

import json
import random
import struct
import time
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryNode


_MODEL = "test/random-384"


def _random_embedding(seed: int = 42) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(384)]


def _audit_summary(mesh: Mesh) -> list[tuple[str, str, list[str], dict[str, Any] | None]]:
    """Return [(event_type, actor, node_ids, metadata), ...] in insertion order."""
    rows = mesh.connection.execute(
        "SELECT event_type, actor, node_ids, metadata FROM audit ORDER BY rowid"
    ).fetchall()
    out: list[tuple[str, str, list[str], dict[str, Any] | None]] = []
    for event_type, actor, node_ids_json, metadata_json in rows:
        node_ids = json.loads(node_ids_json) if node_ids_json else []
        metadata = json.loads(metadata_json) if metadata_json else None
        out.append((event_type, actor, node_ids, metadata))
    return out


def _seed_scope_and_session(mesh: Mesh) -> None:
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-int", "integration", "private", 100),
    )
    mesh.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("ses-int", "int-repo", "claude-code", 100),
    )
    mesh.connection.commit()


def _make(mesh: Mesh, **overrides: Any) -> MemoryNode:
    kwargs: dict[str, Any] = {
        "kind": "semantic",
        "body": "default body",
        "headline": "default headline",
        "scope_id": "scope-int",
        "source_session_id": "ses-int",
        "source_repo": "int-repo",
    }
    kwargs.update(overrides)
    return mesh.make_node(**kwargs)


@pytest.fixture()
def mesh(tmp_path: Path) -> Iterator[Mesh]:
    m = Mesh.local(tmp_path / "integration.db")
    _seed_scope_and_session(m)
    try:
        yield m
    finally:
        m.close()


def test_full_lifecycle_single_node(mesh: Mesh) -> None:
    n1 = _make(mesh, body="alpha", key_insight="ki-1")
    n2 = _make(mesh, body="beta", key_insight="ki-2")
    mesh.add(n1)
    mesh.add(n2)

    mesh.set_vector(n1.id, _random_embedding(seed=1), _MODEL)
    mesh.set_vector(n2.id, _random_embedding(seed=2), _MODEL)

    edge = mesh.make_edge(
        from_node_id=n1.id,
        to_node_id=n2.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)

    assert mesh.get(n1.id) is not None
    assert mesh.get(n2.id) is not None
    assert mesh.has_vector(n1.id) is True
    assert mesh.has_vector(n2.id) is True
    assert mesh.get_edge(edge.id) is not None
    assert {e.id for e in mesh.get_edges(n1.id)} == {edge.id}

    refreshed = mesh.update(n1.id, {"headline": "alpha-updated", "importance": 0.9})
    assert refreshed.headline == "alpha-updated"
    assert refreshed.importance == 0.9

    assert mesh.delete(n1.id) is True
    assert mesh.get(n1.id) is None
    assert mesh.has_vector(n1.id) is False
    assert mesh.get_edge(edge.id) is None

    assert mesh.get(n2.id) is not None
    assert mesh.has_vector(n2.id) is True


def test_multi_node_graph_traversal(mesh: Mesh) -> None:
    nodes = [_make(mesh, body=f"n{i}", key_insight=f"ki-{i}") for i in range(5)]
    for n in nodes:
        mesh.add(n)
    n0, n1, n2, n3, n4 = nodes

    edges_spec: list[tuple[str, str, str]] = [
        (n0.id, n1.id, "applies_to"),
        (n0.id, n2.id, "caused_by"),
        (n1.id, n2.id, "applies_to"),
        (n2.id, n3.id, "generalizes"),
        (n3.id, n0.id, "contradicts"),
        (n4.id, n0.id, "supersedes"),
    ]
    edge_ids: dict[tuple[str, str, str], str] = {}
    for src, dst, rel in edges_spec:
        e = mesh.make_edge(
            from_node_id=src,
            to_node_id=dst,
            relation=rel,  # type: ignore[arg-type]
            created_by="manual",
        )
        mesh.add_edge(e)
        edge_ids[(src, dst, rel)] = e.id

    out_n0 = mesh.get_edges(n0.id, direction="out")
    assert {e.to_node_id for e in out_n0} == {n1.id, n2.id}

    in_n0 = mesh.get_edges(n0.id, direction="in")
    assert {e.from_node_id for e in in_n0} == {n3.id, n4.id}

    both_n0 = mesh.get_edges(n0.id, direction="both")
    assert len(both_n0) == 4

    applies_out_n0 = mesh.get_edges(n0.id, direction="out", relation="applies_to")
    assert [e.id for e in applies_out_n0] == [edge_ids[(n0.id, n1.id, "applies_to")]]

    in_n2_applies = mesh.get_edges(n2.id, direction="in", relation="applies_to")
    assert {e.from_node_id for e in in_n2_applies} == {n1.id}

    in_n2_all = mesh.get_edges(n2.id, direction="in")
    assert {e.from_node_id for e in in_n2_all} == {n0.id, n1.id}


def test_vector_node_one_to_one_coordination(mesh: Mesh) -> None:
    nodes = [_make(mesh, body=f"v{i}", key_insight=f"ki-{i}") for i in range(4)]
    for n in nodes:
        mesh.add(n)
        mesh.set_vector(n.id, _random_embedding(seed=hash(n.id) & 0xFFFF), _MODEL)

    vec_count = mesh.connection.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()[0]
    meta_count = mesh.connection.execute("SELECT COUNT(*) FROM vector_meta").fetchone()[0]
    assert vec_count == 4
    assert meta_count == 4

    assert mesh.delete(nodes[0].id) is True
    assert mesh.delete_vector(nodes[1].id) is True

    vec_count = mesh.connection.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()[0]
    meta_count = mesh.connection.execute("SELECT COUNT(*) FROM vector_meta").fetchone()[0]
    assert vec_count == 2
    assert meta_count == 2

    assert mesh.has_vector(nodes[2].id) is True
    assert mesh.has_vector(nodes[3].id) is True


def test_node_delete_cascades_edges_meta_and_vec_nodes(mesh: Mesh) -> None:
    a = _make(mesh, body="cascade-a", key_insight="ki-a")
    b = _make(mesh, body="cascade-b", key_insight="ki-b")
    mesh.add(a)
    mesh.add(b)
    mesh.set_vector(a.id, _random_embedding(seed=1), _MODEL)
    mesh.set_vector(b.id, _random_embedding(seed=2), _MODEL)

    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)

    assert mesh.delete(a.id) is True

    assert mesh.get(a.id) is None
    assert mesh.has_vector(a.id) is False
    assert (
        mesh.connection.execute(
            "SELECT COUNT(*) FROM vector_meta WHERE node_id = ?", (a.id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        mesh.connection.execute(
            "SELECT COUNT(*) FROM vec_nodes WHERE node_id = ?", (a.id,)
        ).fetchone()[0]
        == 0
    )
    assert mesh.get_edge(edge.id) is None

    assert mesh.get(b.id) is not None
    assert mesh.has_vector(b.id) is True


def test_audit_trail_integrity_full_workflow(mesh: Mesh, monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"t": 1_700_000_000}

    def fake_time() -> float:
        counter["t"] += 1
        return float(counter["t"])

    monkeypatch.setattr("context_mesh.api.mesh.time.time", fake_time)
    monkeypatch.setattr("context_mesh._audit.time.time", fake_time)

    a = _make(mesh, body="audit-a", key_insight="ki-a")
    b = _make(mesh, body="audit-b", key_insight="ki-b")
    mesh.add(a)
    mesh.add(b)

    mesh.set_vector(a.id, _random_embedding(seed=1), _MODEL)
    mesh.set_vector(b.id, _random_embedding(seed=2), _MODEL)

    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)

    mesh.update(a.id, {"headline": "h-new"})
    mesh.set_vector(a.id, _random_embedding(seed=3), _MODEL, replace=True)
    mesh.delete_vector(b.id)
    mesh.delete_edge(edge.id)
    mesh.delete(a.id)

    summary = _audit_summary(mesh)
    event_types = [s[0] for s in summary]
    assert event_types == [
        "add",
        "add",
        "add",
        "add",
        "add",
        "edit",
        "edit",
        "delete",
        "delete",
        "delete",
    ]

    for _, actor, _, _ in summary:
        assert actor == "mesh"

    assert summary[0][2] == [a.id]
    assert summary[1][2] == [b.id]
    assert summary[2][2] == [a.id]
    assert summary[3][2] == [b.id]
    assert summary[4][2] == [a.id, b.id]
    assert summary[5][2] == [a.id]
    assert summary[6][2] == [a.id]
    assert summary[7][2] == [b.id]
    assert summary[8][2] == [a.id, b.id]
    assert summary[9][2] == [a.id]

    vector_actions = [s[3].get("action") for s in summary if s[3] is not None]
    assert "set_vector" in vector_actions
    assert "delete_vector" in vector_actions

    timestamps = [
        row[0]
        for row in mesh.connection.execute("SELECT timestamp FROM audit ORDER BY rowid").fetchall()
    ]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) >= 1


def test_round_trip_close_and_reopen_persists_all_data(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"
    embedding = _random_embedding(seed=7)

    m1 = Mesh.local(db_path)
    try:
        _seed_scope_and_session(m1)
        a = _make(m1, body="persist-a", key_insight="ki-a")
        b = _make(m1, body="persist-b", key_insight="ki-b")
        m1.add(a)
        m1.add(b)
        m1.set_vector(a.id, embedding, _MODEL)
        edge = m1.make_edge(
            from_node_id=a.id,
            to_node_id=b.id,
            relation="caused_by",
            created_by="agent",
            confidence=0.7,
        )
        m1.add_edge(edge)
        a_id, b_id, edge_id = a.id, b.id, edge.id
        audit_count_before = m1.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    finally:
        m1.close()

    m2 = Mesh.local(db_path)
    try:
        a2 = m2.get(a_id)
        b2 = m2.get(b_id)
        assert a2 is not None
        assert b2 is not None
        assert a2.body == "persist-a"
        assert b2.body == "persist-b"

        e2 = m2.get_edge(edge_id)
        assert e2 is not None
        assert e2.from_node_id == a_id
        assert e2.to_node_id == b_id
        assert e2.relation == "caused_by"
        assert e2.confidence == 0.7

        fetched = m2.get_vector(a_id)
        assert fetched is not None
        blob, model, dims = fetched
        assert struct.unpack(f"<{dims}f", blob) == pytest.approx(embedding)
        assert model == _MODEL

        audit_count_after = m2.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        assert audit_count_after == audit_count_before
    finally:
        m2.close()


def test_interleaved_reads_consistent(mesh: Mesh) -> None:
    nodes = [_make(mesh, body=f"r{i}", key_insight=f"ki-{i}") for i in range(3)]
    for n in nodes:
        mesh.add(n)
    edge = mesh.make_edge(
        from_node_id=nodes[0].id,
        to_node_id=nodes[1].id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)
    mesh.set_vector(nodes[0].id, _random_embedding(seed=1), _MODEL)

    for _ in range(3):
        listed = mesh.list_nodes()
        assert {n.id for n in listed} == {n.id for n in nodes}

        fetched = mesh.get(nodes[0].id)
        assert fetched is not None
        assert fetched.body == "r0"

        edges = mesh.list_edges()
        assert [e.id for e in edges] == [edge.id]

        out = mesh.get_edges(nodes[0].id, direction="out")
        assert [e.id for e in out] == [edge.id]

        vec = mesh.get_vector(nodes[0].id)
        assert vec is not None
        assert mesh.has_vector(nodes[0].id) is True
        assert mesh.has_vector(nodes[1].id) is False


def test_empty_mesh_read_methods_return_empty_or_none(mesh: Mesh) -> None:
    assert mesh.list_nodes() == []
    assert mesh.list_edges() == []
    assert mesh.get("ghost") is None
    assert mesh.get_edge("ghost") is None
    assert mesh.get_edges("ghost") == []
    assert mesh.get_vector("ghost") is None
    assert mesh.has_vector("ghost") is False
    assert mesh.delete("ghost") is False
    assert mesh.delete_edge("ghost") is False
    assert mesh.delete_vector("ghost") is False


def test_node_body_update_does_not_disturb_vector(mesh: Mesh) -> None:
    n = _make(mesh, body="original", key_insight="ki")
    mesh.add(n)
    embedding = _random_embedding(seed=5)
    mesh.set_vector(n.id, embedding, _MODEL)

    before = mesh.get_vector(n.id)
    assert before is not None

    mesh.update(n.id, {"body": "rewritten body"})

    after = mesh.get_vector(n.id)
    assert after is not None
    assert after == before
    blob, model, dims = after
    assert struct.unpack(f"<{dims}f", blob) == pytest.approx(embedding)
    assert model == _MODEL


def test_delete_idempotency_returns_false_no_audit_no_exception(mesh: Mesh) -> None:
    a = _make(mesh, body="idem-a", key_insight="ki-a")
    b = _make(mesh, body="idem-b", key_insight="ki-b")
    mesh.add(a)
    mesh.add(b)
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)
    mesh.set_vector(a.id, _random_embedding(seed=1), _MODEL)

    assert mesh.delete_vector(a.id) is True
    assert mesh.delete_vector(a.id) is False
    assert mesh.delete_vector(a.id) is False

    assert mesh.delete_edge(edge.id) is True
    assert mesh.delete_edge(edge.id) is False
    assert mesh.delete_edge(edge.id) is False

    assert mesh.delete(a.id) is True
    assert mesh.delete(a.id) is False
    assert mesh.delete(a.id) is False

    delete_node_audits = mesh.connection.execute(
        "SELECT COUNT(*) FROM audit "
        "WHERE event_type = 'delete' "
        "AND (metadata IS NULL OR json_extract(metadata, '$.action') IS NULL) "
        "AND json_extract(metadata, '$.edge_id') IS NULL"
    ).fetchone()[0]
    assert delete_node_audits == 1


def test_list_nodes_combined_filters(mesh: Mesh) -> None:
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-other", "other", "team", 100),
    )
    mesh.connection.commit()

    matches: list[MemoryNode] = []
    for i in range(3):
        n = _make(
            mesh,
            kind="semantic",
            scope_id="scope-int",
            source_repo="int-repo",
            body=f"match-{i + 1}",
            key_insight=f"match-ki-{i}",
        )
        n.created_at = 1000 + i
        n.updated_at = 1000 + i
        matches.append(n)
        mesh.add(n)

    extra: list[MemoryNode] = []
    for i in range(3):
        n = _make(
            mesh,
            kind="semantic",
            scope_id="scope-int",
            source_repo="int-repo",
            body=f"match-extra-{i + 1}",
            key_insight=f"match-extra-ki-{i}",
        )
        n.created_at = 2000 + i
        n.updated_at = 2000 + i
        extra.append(n)
        mesh.add(n)

    non_match_specs: list[dict[str, Any]] = [
        {
            "kind": "episodic",
            "scope_id": "scope-int",
            "source_repo": "int-repo",
            "body": "nm-kind",
            "key_insight": "nm-ki-1",
        },
        {
            "kind": "semantic",
            "scope_id": "scope-other",
            "source_repo": "int-repo",
            "body": "nm-scope",
            "key_insight": "nm-ki-2",
        },
        {
            "kind": "semantic",
            "scope_id": "scope-int",
            "source_repo": "other-repo",
            "body": "nm-repo",
            "key_insight": "nm-ki-3",
        },
    ]
    for spec in non_match_specs:
        n = _make(mesh, **spec)
        mesh.add(n)

    decayed = _make(
        mesh,
        kind="semantic",
        scope_id="scope-int",
        source_repo="int-repo",
        body="decayed-one",
        key_insight="nm-ki-4",
        decayed_at=500,
    )
    mesh.add(decayed)

    page = mesh.list_nodes(
        kind="semantic",
        scope_id="scope-int",
        source_repo="int-repo",
        decayed=False,
        limit=3,
        offset=0,
    )
    assert [n.body for n in page] == ["match-1", "match-2", "match-3"]


def test_self_loop_edge_with_vector_and_cascade(mesh: Mesh) -> None:
    a = _make(mesh, body="self-loop", key_insight="ki-self")
    mesh.add(a)
    mesh.set_vector(a.id, _random_embedding(seed=9), _MODEL)

    loop = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=a.id,
        relation="contradicts",
        created_by="manual",
    )
    mesh.add_edge(loop)

    both = mesh.get_edges(a.id, direction="both")
    assert len(both) == 1
    assert both[0].id == loop.id

    assert mesh.delete(a.id) is True
    assert mesh.get(a.id) is None
    assert mesh.has_vector(a.id) is False
    assert mesh.get_edge(loop.id) is None
    assert (
        mesh.connection.execute("SELECT COUNT(*) FROM edges WHERE id = ?", (loop.id,)).fetchone()[0]
        == 0
    )


def test_re_embedding_changes_audit_and_bytes(mesh: Mesh) -> None:
    n = _make(mesh, body="re-embed", key_insight="ki")
    mesh.add(n)

    v1 = _random_embedding(seed=1)
    v2 = _random_embedding(seed=2)
    mesh.set_vector(n.id, v1, _MODEL)
    mesh.set_vector(n.id, v2, _MODEL, replace=True)

    rows = mesh.connection.execute(
        "SELECT event_type, metadata FROM audit WHERE node_ids = ? ORDER BY rowid",
        (json.dumps([n.id]),),
    ).fetchall()
    vector_events = [
        (et, json.loads(meta))
        for et, meta in rows
        if meta is not None and json.loads(meta).get("action") == "set_vector"
    ]
    assert [et for et, _ in vector_events] == ["add", "edit"]
    assert vector_events[0][1]["replace"] is False
    assert vector_events[1][1]["replace"] is True

    fetched = mesh.get_vector(n.id)
    assert fetched is not None
    blob, _, dims = fetched
    assert struct.unpack(f"<{dims}f", blob) == pytest.approx(v2)


def test_set_vector_without_replace_rejects_existing(mesh: Mesh) -> None:
    n = _make(mesh, body="no-replace", key_insight="ki")
    mesh.add(n)
    v1 = _random_embedding(seed=1)
    mesh.set_vector(n.id, v1, _MODEL)

    with pytest.raises(ValueError, match="vector already exists"):
        mesh.set_vector(n.id, _random_embedding(seed=2), _MODEL)

    fetched = mesh.get_vector(n.id)
    assert fetched is not None
    blob, model, dims = fetched
    assert struct.unpack(f"<{dims}f", blob) == pytest.approx(v1)
    assert model == _MODEL


def test_listing_order_is_deterministic_across_calls(
    mesh: Mesh, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = {"t": 1_800_000_000}

    def fake_time() -> float:
        counter["t"] += 1
        return float(counter["t"])

    monkeypatch.setattr("context_mesh.api.mesh.time.time", fake_time)
    monkeypatch.setattr("context_mesh._audit.time.time", fake_time)

    nodes = [_make(mesh, body=f"det-{i}", key_insight=f"ki-{i}") for i in range(5)]
    for n in nodes:
        mesh.add(n)

    edges = []
    for i in range(4):
        e = mesh.make_edge(
            from_node_id=nodes[i].id,
            to_node_id=nodes[i + 1].id,
            relation="applies_to",
            created_by="manual",
        )
        mesh.add_edge(e)
        edges.append(e)

    first_nodes = mesh.list_nodes()
    second_nodes = mesh.list_nodes()
    assert [n.id for n in first_nodes] == [n.id for n in second_nodes]

    first_edges = mesh.list_edges()
    second_edges = mesh.list_edges()
    assert [e.id for e in first_edges] == [e.id for e in second_edges]

    page_a = mesh.list_nodes(limit=3, offset=1)
    page_b = mesh.list_nodes(limit=3, offset=1)
    assert [n.id for n in page_a] == [n.id for n in page_b]

    time.sleep(0)
