"""Tests for `Mesh.search_by_vector` — vector kNN primitive."""

from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING

import pytest

from context_mesh.api import Mesh, VectorSearchResult
from context_mesh.embeddings.deterministic import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryNode, NodeKind

_PROVIDER = DeterministicEmbeddingProvider()
_MODEL = _PROVIDER.name


@pytest.fixture()
def empty_mesh(tmp_path: Path) -> Iterator[Mesh]:
    mesh = Mesh.local(tmp_path / "test.db")
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-a", "scope-a", "private", 100),
    )
    mesh.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-b", "scope-b", "team", 100),
    )
    mesh.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("ses-1", "repo", "claude-code", 100),
    )
    mesh.connection.commit()
    try:
        yield mesh
    finally:
        mesh.close()


def _seed(
    mesh: Mesh,
    body: str,
    *,
    kind: NodeKind = "semantic",
    scope_id: str = "scope-a",
) -> MemoryNode:
    node = mesh.make_node(
        kind=kind,
        body=body,
        headline=body[:80],
        scope_id=scope_id,
        source_session_id="ses-1",
        source_repo="repo",
    )
    mesh.add(node)
    mesh.set_vector(node.id, _PROVIDER.embed_text(body), _MODEL)
    return node


def test_returns_empty_when_no_vectors_stored(empty_mesh: Mesh) -> None:
    query = _PROVIDER.embed_text("anything")
    assert empty_mesh.search_by_vector(query, k=5) == []


def test_returns_at_most_k_results(empty_mesh: Mesh) -> None:
    for body in ("alpha", "beta", "gamma", "delta", "epsilon"):
        _seed(empty_mesh, body)

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("alpha"), k=3)
    assert len(results) == 3


def test_returns_results_sorted_by_distance_ascending(empty_mesh: Mesh) -> None:
    for body in ("alpha", "beta", "gamma", "delta", "epsilon"):
        _seed(empty_mesh, body)

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("alpha"), k=5)
    distances = [r.distance for r in results]
    assert distances == sorted(distances)


def test_query_text_finds_its_own_vector_first(empty_mesh: Mesh) -> None:
    target = _seed(empty_mesh, "the unique webhook timeout investigation")
    for body in ("redis cache eviction", "deploy script broken", "cli arg parsing"):
        _seed(empty_mesh, body)

    results = empty_mesh.search_by_vector(
        _PROVIDER.embed_text("the unique webhook timeout investigation"), k=4
    )
    assert results[0].node_id == target.id
    assert results[0].distance == pytest.approx(0.0, abs=1e-5)


def test_top_result_matches_query_text_among_others(empty_mesh: Mesh) -> None:
    bodies = (
        "rule about webhook signature verification",
        "deploy procedure for payments service",
        "redis cache eviction policy",
        "cli arg parser bug fix",
        "structured log fields convention",
    )
    nodes = {body: _seed(empty_mesh, body) for body in bodies}
    pick = "redis cache eviction policy"

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text(pick), k=5)
    assert results[0].node.body == pick
    assert results[0].node_id == nodes[pick].id


def test_filter_by_kind_excludes_other_kinds(empty_mesh: Mesh) -> None:
    for body in ("ep-1", "ep-2", "ep-3"):
        _seed(empty_mesh, body, kind="episodic")
    for body in ("sem-1", "sem-2"):
        _seed(empty_mesh, body, kind="semantic")

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("ep-1"), k=10, kind="semantic")
    assert len(results) == 2
    assert all(r.node.kind == "semantic" for r in results)


def test_filter_by_scope_id_excludes_other_scopes(empty_mesh: Mesh) -> None:
    for body in ("a-1", "a-2", "a-3"):
        _seed(empty_mesh, body, scope_id="scope-a")
    for body in ("b-1", "b-2"):
        _seed(empty_mesh, body, scope_id="scope-b")

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("a-1"), k=10, scope_id="scope-b")
    assert len(results) == 2
    assert all(r.node.scope_id == "scope-b" for r in results)


def test_filter_by_kind_and_scope_combined(empty_mesh: Mesh) -> None:
    _seed(empty_mesh, "x1", kind="episodic", scope_id="scope-a")
    _seed(empty_mesh, "x2", kind="semantic", scope_id="scope-a")
    _seed(empty_mesh, "x3", kind="semantic", scope_id="scope-b")
    _seed(empty_mesh, "x4", kind="episodic", scope_id="scope-b")

    results = empty_mesh.search_by_vector(
        _PROVIDER.embed_text("x1"),
        k=10,
        kind="semantic",
        scope_id="scope-a",
    )
    assert len(results) == 1
    assert results[0].node.kind == "semantic"
    assert results[0].node.scope_id == "scope-a"
    assert results[0].node.body == "x2"


def test_filter_returns_fewer_than_k_when_filter_excludes_all(empty_mesh: Mesh) -> None:
    for body in ("ep-1", "ep-2"):
        _seed(empty_mesh, body, kind="episodic")
    for body in ("sem-1", "sem-2"):
        _seed(empty_mesh, body, kind="semantic")

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("ep-1"), k=5, kind="procedural")
    assert results == []


def test_accepts_list_of_floats(empty_mesh: Mesh) -> None:
    target = _seed(empty_mesh, "alpha")
    query = _PROVIDER.embed_text("alpha")
    assert isinstance(query, list)

    results = empty_mesh.search_by_vector(query, k=1)
    assert len(results) == 1
    assert results[0].node_id == target.id


def test_accepts_bytes_input(empty_mesh: Mesh) -> None:
    target = _seed(empty_mesh, "alpha")
    query_floats = _PROVIDER.embed_text("alpha")
    query_bytes = struct.pack("<384f", *query_floats)

    results = empty_mesh.search_by_vector(query_bytes, k=1)
    assert len(results) == 1
    assert results[0].node_id == target.id


def test_rejects_wrong_dimension_list(empty_mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="embedding dimension"):
        empty_mesh.search_by_vector([0.0] * 100, k=3)


def test_rejects_wrong_dimension_bytes(empty_mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="embedding bytes length"):
        empty_mesh.search_by_vector(b"\x00" * 100, k=3)


def test_returns_vector_search_result_with_full_node(empty_mesh: Mesh) -> None:
    body = "rich semantic memory about webhook timeouts"
    rich_node = empty_mesh.make_node(
        kind="semantic",
        body=body,
        headline="webhook timeouts",
        scope_id="scope-a",
        source_session_id="ses-1",
        source_repo="repo",
        decisions=["never trust first webhook ts", "always reverify with current_time"],
        tags=["webhook", "stripe", "payments"],
        key_insight="Stripe webhook timestamps drift; always reverify.",
    )
    empty_mesh.add(rich_node)
    empty_mesh.set_vector(rich_node.id, _PROVIDER.embed_text(body), _MODEL)

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text(body), k=1)
    assert len(results) == 1
    hit = results[0]
    assert isinstance(hit, VectorSearchResult)
    assert hit.node_id == rich_node.id
    assert hit.node.id == rich_node.id
    assert hit.node.body == body
    assert hit.node.decisions == [
        "never trust first webhook ts",
        "always reverify with current_time",
    ]
    assert hit.node.tags == ["webhook", "stripe", "payments"]
    assert hit.node.key_insight == "Stripe webhook timestamps drift; always reverify."


def test_distance_is_float(empty_mesh: Mesh) -> None:
    _seed(empty_mesh, "alpha")
    _seed(empty_mesh, "beta")

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("alpha"), k=2)
    assert len(results) == 2
    for r in results:
        assert isinstance(r.distance, float)
        assert math.isfinite(r.distance)


def test_node_without_vector_not_returned(empty_mesh: Mesh) -> None:
    for body in ("with-vec-1", "with-vec-2", "with-vec-3"):
        _seed(empty_mesh, body)
    for body in ("no-vec-1", "no-vec-2"):
        node = empty_mesh.make_node(
            kind="semantic",
            body=body,
            headline=body,
            scope_id="scope-a",
            source_session_id="ses-1",
            source_repo="repo",
        )
        empty_mesh.add(node)

    results = empty_mesh.search_by_vector(_PROVIDER.embed_text("with-vec-1"), k=10)
    assert len(results) == 3
    bodies = {r.node.body for r in results}
    assert bodies == {"with-vec-1", "with-vec-2", "with-vec-3"}


def test_k_zero_returns_empty(empty_mesh: Mesh) -> None:
    _seed(empty_mesh, "alpha")
    assert empty_mesh.search_by_vector(_PROVIDER.embed_text("alpha"), k=0) == []


def test_k_negative_raises_value_error(empty_mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="k must be non-negative"):
        empty_mesh.search_by_vector(_PROVIDER.embed_text("alpha"), k=-1)


def test_search_emits_no_audit_rows(empty_mesh: Mesh) -> None:
    for body in ("alpha", "beta", "gamma"):
        _seed(empty_mesh, body)

    before = empty_mesh.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    empty_mesh.search_by_vector(_PROVIDER.embed_text("alpha"), k=3)
    empty_mesh.search_by_vector(
        _PROVIDER.embed_text("beta"), k=2, kind="semantic", scope_id="scope-a"
    )
    after = empty_mesh.connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert after - before == 0
