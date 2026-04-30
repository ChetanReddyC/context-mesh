"""Tests for `context_mesh.api.mesh.Mesh` Edge CRUD methods."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryEdge, MemoryNode


@pytest.fixture()
def mesh_with_edge_seed(
    tmp_path: Path,
) -> Iterator[tuple[Mesh, MemoryNode, MemoryNode, MemoryNode]]:
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
    a = mesh.make_node(
        kind="semantic",
        body="node-a",
        headline="ha",
        scope_id="scope-1",
        source_session_id="ses-1",
        source_repo="repo",
    )
    b = mesh.make_node(
        kind="semantic",
        body="node-b",
        headline="hb",
        scope_id="scope-1",
        source_session_id="ses-1",
        source_repo="repo",
    )
    c = mesh.make_node(
        kind="semantic",
        body="node-c",
        headline="hc",
        scope_id="scope-1",
        source_session_id="ses-1",
        source_repo="repo",
    )
    mesh.add(a)
    mesh.add(b)
    mesh.add(c)
    try:
        yield mesh, a, b, c
    finally:
        mesh.close()


def _edge_audit_rows(
    conn: sqlite3.Connection, event_type: str, edge_id: str
) -> list[tuple[Any, ...]]:
    return list(
        conn.execute(
            "SELECT node_ids, metadata FROM audit "
            "WHERE event_type = ? AND json_extract(metadata, '$.edge_id') = ?",
            (event_type, edge_id),
        ).fetchall()
    )


# ----- add_edge -------------------------------------------------------------


def test_add_edge_inserts_row(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)

    row = mesh.connection.execute(
        "SELECT id, from_node_id, to_node_id, relation FROM edges WHERE id = ?",
        (edge.id,),
    ).fetchone()
    assert row == (edge.id, a.id, b.id, "applies_to")


def test_add_edge_returns_id(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    returned = mesh.add_edge(edge)
    assert returned == edge.id


def test_add_edge_emits_audit(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="caused_by",
        created_by="agent",
        confidence=0.42,
    )
    mesh.add_edge(edge)

    rows = _edge_audit_rows(mesh.connection, "add", edge.id)
    assert len(rows) == 1
    node_ids_json, metadata_json = rows[0]
    assert json.loads(node_ids_json) == [a.id, b.id]
    metadata = json.loads(metadata_json)
    assert metadata == {
        "edge_id": edge.id,
        "relation": "caused_by",
        "confidence": 0.42,
    }


def test_add_edge_rejects_invalid_relation(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    edge.relation = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="invalid edge relation"):
        mesh.add_edge(edge)


def test_add_edge_rejects_invalid_created_by(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    edge.created_by = "ghost"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="invalid edge created_by"):
        mesh.add_edge(edge)


def test_add_edge_rejects_missing_from_node_fk(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, _, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id="does-not-exist",
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    with pytest.raises(sqlite3.IntegrityError):
        mesh.add_edge(edge)


def test_add_edge_rejects_duplicate_unique_triple(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    first = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(first)

    duplicate = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="agent",
    )
    with pytest.raises(sqlite3.IntegrityError):
        mesh.add_edge(duplicate)


# ----- get_edge -------------------------------------------------------------


def test_get_edge_returns_edge(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
    )
    mesh.add_edge(edge)

    fetched = mesh.get_edge(edge.id)
    assert fetched == edge


def test_get_edge_returns_none_for_missing(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, _, _, _ = mesh_with_edge_seed
    assert mesh.get_edge("ghost") is None


def test_get_edge_round_trips_metadata(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    payload = {"reason": "inferred from cause-chain", "score": 0.8, "tags": ["x", "y"]}
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="caused_by",
        created_by="auto",
        confidence=0.55,
        metadata=payload,
    )
    mesh.add_edge(edge)

    fetched = mesh.get_edge(edge.id)
    assert fetched is not None
    assert fetched.metadata == payload
    assert fetched.confidence == 0.55


# ----- get_edges ------------------------------------------------------------


def test_get_edges_out_direction(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, c = mesh_with_edge_seed
    e_ab = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    e_ac = mesh.make_edge(
        from_node_id=a.id, to_node_id=c.id, relation="caused_by", created_by="manual"
    )
    e_ba = mesh.make_edge(
        from_node_id=b.id, to_node_id=a.id, relation="contradicts", created_by="manual"
    )
    mesh.add_edge(e_ab)
    mesh.add_edge(e_ac)
    mesh.add_edge(e_ba)

    out = mesh.get_edges(a.id, direction="out")
    assert {edge.id for edge in out} == {e_ab.id, e_ac.id}


def test_get_edges_in_direction(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, c = mesh_with_edge_seed
    e_ab = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    e_cb = mesh.make_edge(
        from_node_id=c.id, to_node_id=b.id, relation="caused_by", created_by="manual"
    )
    e_ba = mesh.make_edge(
        from_node_id=b.id, to_node_id=a.id, relation="contradicts", created_by="manual"
    )
    mesh.add_edge(e_ab)
    mesh.add_edge(e_cb)
    mesh.add_edge(e_ba)

    incoming = mesh.get_edges(b.id, direction="in")
    assert {edge.id for edge in incoming} == {e_ab.id, e_cb.id}


def test_get_edges_both_direction(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    self_loop = mesh.make_edge(
        from_node_id=a.id, to_node_id=a.id, relation="contradicts", created_by="manual"
    )
    out_edge = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    in_edge = mesh.make_edge(
        from_node_id=b.id, to_node_id=a.id, relation="caused_by", created_by="manual"
    )
    mesh.add_edge(self_loop)
    mesh.add_edge(out_edge)
    mesh.add_edge(in_edge)

    both = mesh.get_edges(a.id, direction="both")
    ids = [edge.id for edge in both]
    assert set(ids) == {self_loop.id, out_edge.id, in_edge.id}
    assert len(ids) == 3


def test_get_edges_filter_by_relation(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, c = mesh_with_edge_seed
    e_applies = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    e_caused = mesh.make_edge(
        from_node_id=a.id, to_node_id=c.id, relation="caused_by", created_by="manual"
    )
    mesh.add_edge(e_applies)
    mesh.add_edge(e_caused)

    only_applies = mesh.get_edges(a.id, direction="out", relation="applies_to")
    assert [edge.id for edge in only_applies] == [e_applies.id]


def test_get_edges_returns_empty_for_isolated_node(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, _, _, c = mesh_with_edge_seed
    assert mesh.get_edges(c.id) == []


# ----- list_edges -----------------------------------------------------------


def test_list_edges_empty_returns_empty(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, _, _, _ = mesh_with_edge_seed
    assert mesh.list_edges() == []


def test_list_edges_filter_by_relation(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, c = mesh_with_edge_seed
    e_applies = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    e_caused = mesh.make_edge(
        from_node_id=a.id, to_node_id=c.id, relation="caused_by", created_by="manual"
    )
    e_caused2 = mesh.make_edge(
        from_node_id=b.id, to_node_id=c.id, relation="caused_by", created_by="manual"
    )
    mesh.add_edge(e_applies)
    mesh.add_edge(e_caused)
    mesh.add_edge(e_caused2)

    caused = mesh.list_edges(relation="caused_by")
    assert {edge.id for edge in caused} == {e_caused.id, e_caused2.id}


def test_list_edges_filter_by_created_by(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, c = mesh_with_edge_seed
    e_manual = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    e_agent = mesh.make_edge(
        from_node_id=a.id, to_node_id=c.id, relation="caused_by", created_by="agent"
    )
    e_auto = mesh.make_edge(
        from_node_id=b.id, to_node_id=c.id, relation="generalizes", created_by="auto"
    )
    mesh.add_edge(e_manual)
    mesh.add_edge(e_agent)
    mesh.add_edge(e_auto)

    auto_only = mesh.list_edges(created_by="auto")
    assert [edge.id for edge in auto_only] == [e_auto.id]


def test_list_edges_limit_offset(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, c = mesh_with_edge_seed
    edges: list[MemoryEdge] = []
    pairs: list[tuple[str, str, str]] = [
        (a.id, b.id, "applies_to"),
        (a.id, c.id, "caused_by"),
        (b.id, c.id, "generalizes"),
        (b.id, a.id, "contradicts"),
        (c.id, a.id, "supersedes"),
    ]
    for i, (src, dst, rel) in enumerate(pairs):
        edge = mesh.make_edge(
            from_node_id=src,
            to_node_id=dst,
            relation=rel,  # type: ignore[arg-type]
            created_by="manual",
        )
        edge.created_at = 1000 + i
        edges.append(edge)
        mesh.add_edge(edge)

    page = mesh.list_edges(limit=2, offset=1)
    assert [edge.id for edge in page] == [edges[1].id, edges[2].id]


# ----- delete_edge ----------------------------------------------------------


def test_delete_edge_returns_true_for_existing(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    mesh.add_edge(edge)
    assert mesh.delete_edge(edge.id) is True


def test_delete_edge_returns_false_for_missing_no_audit(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, _, _, _ = mesh_with_edge_seed
    assert mesh.delete_edge("ghost") is False

    rows = list(
        mesh.connection.execute("SELECT id FROM audit WHERE event_type = 'delete'").fetchall()
    )
    assert rows == []


def test_delete_edge_removes_row(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    mesh.add_edge(edge)
    mesh.delete_edge(edge.id)

    assert mesh.get_edge(edge.id) is None
    count = mesh.connection.execute(
        "SELECT COUNT(*) FROM edges WHERE id = ?", (edge.id,)
    ).fetchone()[0]
    assert count == 0


def test_delete_edge_emits_audit_with_endpoint_node_ids(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    mesh.add_edge(edge)
    mesh.delete_edge(edge.id)

    rows = _edge_audit_rows(mesh.connection, "delete", edge.id)
    assert len(rows) == 1
    node_ids_json, metadata_json = rows[0]
    assert json.loads(node_ids_json) == [a.id, b.id]
    assert json.loads(metadata_json) == {"edge_id": edge.id}


# ----- make_edge ------------------------------------------------------------


def test_make_edge_generates_unique_ids(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    e1 = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    e2 = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    assert e1.id != e2.id


def test_make_edge_stamps_created_at(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    before = int(time.time())
    edge = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    after = int(time.time())
    assert before - 2 <= edge.created_at <= after + 2


def test_make_edge_default_confidence_is_one(
    mesh_with_edge_seed: tuple[Mesh, MemoryNode, MemoryNode, MemoryNode],
) -> None:
    mesh, a, b, _ = mesh_with_edge_seed
    edge = mesh.make_edge(
        from_node_id=a.id, to_node_id=b.id, relation="applies_to", created_by="manual"
    )
    assert edge.confidence == 1.0


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (
            {
                "from_node_id": "a",
                "to_node_id": "b",
                "relation": "bogus",
                "created_by": "manual",
            },
            "invalid edge relation",
        ),
        (
            {
                "from_node_id": "a",
                "to_node_id": "b",
                "relation": "applies_to",
                "created_by": "ghost",
            },
            "invalid edge created_by",
        ),
    ],
)
def test_make_edge_rejects_invalid_relation_or_created_by(
    kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        Mesh.make_edge(**kwargs)
