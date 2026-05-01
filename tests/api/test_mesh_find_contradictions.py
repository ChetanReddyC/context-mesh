"""Tests for `Mesh.find_contradictions` — flat similarity-based v1 surface."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from context_mesh.api import Mesh
from context_mesh.embeddings import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryNode, NodeKind


_PROVIDER = DeterministicEmbeddingProvider()
_MODEL = _PROVIDER.name


@pytest.fixture()
def mesh(tmp_path: Path) -> Iterator[Mesh]:
    m = Mesh.local(tmp_path / "fc.db")
    m.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-a", "scope-a", "private", 100),
    )
    m.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-b", "scope-b", "team", 100),
    )
    m.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("ses-1", "repo", "claude-code", 100),
    )
    m.connection.commit()
    try:
        yield m
    finally:
        m.close()


def _seed(
    mesh_obj: Mesh,
    body: str,
    *,
    kind: NodeKind = "semantic",
    scope_id: str = "scope-a",
    with_vector: bool = True,
) -> MemoryNode:
    node = mesh_obj.make_node(
        kind=kind,
        body=body,
        headline=body[:80],
        scope_id=scope_id,
        source_session_id="ses-1",
        source_repo="repo",
    )
    mesh_obj.add(node)
    if with_vector:
        mesh_obj.set_vector(node.id, _PROVIDER.embed_text(body), _MODEL)
    return node


def test_find_contradictions_returns_results_in_similarity_order(mesh: Mesh) -> None:
    target = _seed(mesh, "always use Redis for sessions")
    _seed(mesh, "completely unrelated kafka topic config")
    _seed(mesh, "another wholly different deployment notice")

    out = mesh.find_contradictions("always use Redis for sessions", embedder=_PROVIDER, limit=3)

    assert len(out) >= 1
    assert out[0].id == target.id


def test_find_contradictions_filters_to_kind_default_semantic(mesh: Mesh) -> None:
    sem = _seed(mesh, "always use Redis for sessions [semantic]", kind="semantic")
    _seed(mesh, "always use Redis for sessions [episodic]", kind="episodic")
    _seed(mesh, "always use Redis for sessions [procedural]", kind="procedural")

    out = mesh.find_contradictions("always use Redis for sessions", embedder=_PROVIDER, limit=10)

    assert all(n.kind == "semantic" for n in out)
    assert sem.id in {n.id for n in out}


def test_find_contradictions_filters_to_kind_procedural(mesh: Mesh) -> None:
    _seed(mesh, "always use Redis for sessions [semantic]", kind="semantic")
    proc = _seed(mesh, "always use Redis for sessions [procedural]", kind="procedural")

    out = mesh.find_contradictions(
        "always use Redis for sessions",
        embedder=_PROVIDER,
        kind="procedural",
        limit=10,
    )

    assert all(n.kind == "procedural" for n in out)
    assert proc.id in {n.id for n in out}


def test_find_contradictions_respects_limit(mesh: Mesh) -> None:
    for i in range(5):
        _seed(mesh, f"sessions rule variant {i}")

    out = mesh.find_contradictions("sessions rule variant 0", embedder=_PROVIDER, limit=2)

    assert len(out) == 2


def test_find_contradictions_respects_scope_id_filter(mesh: Mesh) -> None:
    a = _seed(mesh, "always use Redis for sessions [a]", scope_id="scope-a")
    _seed(mesh, "always use Redis for sessions [b]", scope_id="scope-b")

    out = mesh.find_contradictions(
        "always use Redis for sessions",
        embedder=_PROVIDER,
        limit=10,
        scope_id="scope-a",
    )

    assert all(n.scope_id == "scope-a" for n in out)
    assert a.id in {n.id for n in out}


def test_find_contradictions_empty_db_returns_empty(mesh: Mesh) -> None:
    out = mesh.find_contradictions("anything", embedder=_PROVIDER, limit=5)
    assert out == []


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_find_contradictions_empty_content_raises(mesh: Mesh, bad: str) -> None:
    with pytest.raises(ValueError, match="content"):
        mesh.find_contradictions(bad, embedder=_PROVIDER)


def test_find_contradictions_invalid_kind_raises(mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="kind must be 'semantic' or 'procedural'"):
        mesh.find_contradictions(
            "x",
            embedder=_PROVIDER,
            kind="episodic",
        )


def test_find_contradictions_zero_limit_raises(mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="limit"):
        mesh.find_contradictions("x", embedder=_PROVIDER, limit=0)


@pytest.mark.parametrize("bad", ["", "   "])
def test_find_contradictions_empty_actor_raises(mesh: Mesh, bad: str) -> None:
    with pytest.raises(ValueError, match="actor"):
        mesh.find_contradictions("x", embedder=_PROVIDER, actor=bad)


def test_find_contradictions_audits_with_correct_metadata(mesh: Mesh) -> None:
    _seed(mesh, "always use Redis for sessions")

    mesh.find_contradictions(
        "always use Redis for sessions",
        embedder=_PROVIDER,
        limit=3,
        scope_id="scope-a",
    )

    rows = mesh.connection.execute(
        "SELECT actor, query, result_count, metadata FROM audit "
        "WHERE event_type = 'retrieve' "
        "AND json_extract(metadata, '$.action') = 'find_contradictions' "
        "ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 1
    actor, query, result_count, metadata_json = rows[0]
    assert actor == "mesh"
    assert query == "always use Redis for sessions"
    assert result_count >= 1
    metadata = json.loads(metadata_json)
    assert metadata["action"] == "find_contradictions"
    assert metadata["kind"] == "semantic"
    assert metadata["scope_id"] == "scope-a"
    assert metadata["limit"] == 3
    assert metadata["embedder"] == _PROVIDER.name


def test_find_contradictions_custom_actor_propagates(mesh: Mesh) -> None:
    _seed(mesh, "alpha")

    mesh.find_contradictions("alpha", embedder=_PROVIDER, actor="agent:test")

    row = mesh.connection.execute(
        "SELECT actor FROM audit "
        "WHERE event_type = 'retrieve' "
        "AND json_extract(metadata, '$.action') = 'find_contradictions'"
    ).fetchone()
    assert row[0] == "agent:test"
