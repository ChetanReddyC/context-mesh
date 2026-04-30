"""Tests for `Mesh._hybrid_retrieve` — hybrid retrieval engine."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from context_mesh.api import MemoryCluster, Mesh
from context_mesh.api.mesh import (
    _compute_recency_score,
    _compute_usage_score,
    _semantic_score_from_distance,
)
from context_mesh.embeddings.deterministic import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import EdgeRelation, MemoryNode, NodeKind

_PROVIDER = DeterministicEmbeddingProvider()
_MODEL = _PROVIDER.name

_DAY_SECONDS = 86400


@pytest.fixture()
def mesh(tmp_path: Path) -> Iterator[Mesh]:
    m = Mesh.local(tmp_path / "test.db")
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


def test_compute_recency_score_recent_is_100() -> None:
    now = int(time.time())
    assert _compute_recency_score(now, now) == pytest.approx(100.0)


def test_compute_recency_score_old_is_low() -> None:
    now = int(time.time())
    assert _compute_recency_score(now - 90 * _DAY_SECONDS, now) < 25.0


def test_compute_usage_score_zero_zero_is_zero() -> None:
    assert _compute_usage_score(0, 0) == pytest.approx(0.0)


def test_compute_usage_score_high_values_are_meaningful_and_capped() -> None:
    score = _compute_usage_score(10000, 1000)
    assert 50.0 < score <= 100.0


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(0.0, 100.0), (1.0, 50.0), (2.0, 0.0)],
)
def test_semantic_score_from_distance_endpoints(distance: float, expected: float) -> None:
    assert _semantic_score_from_distance(distance) == pytest.approx(expected)


def test_returns_memory_cluster(mesh: Mesh) -> None:
    _seed(mesh, "alpha")
    result = mesh._hybrid_retrieve(_PROVIDER.embed_text("alpha"))
    assert isinstance(result, MemoryCluster)


def test_empty_db_returns_empty_cluster(mesh: Mesh) -> None:
    result = mesh._hybrid_retrieve(_PROVIDER.embed_text("nothing here"))
    assert result.nodes == []
    assert result.edges == []
    assert result.cluster_confidence is None


def test_single_match_returns_one_scored_node(mesh: Mesh) -> None:
    target = _seed(mesh, "the unique webhook timeout investigation")
    result = mesh._hybrid_retrieve(_PROVIDER.embed_text("the unique webhook timeout investigation"))
    assert len(result.nodes) == 1
    assert result.nodes[0].node.id == target.id
    assert result.nodes[0].semantic_score == pytest.approx(100.0, abs=0.5)


def test_respects_limit(mesh: Mesh) -> None:
    for i in range(12):
        _seed(mesh, f"body-{i}")
    result = mesh._hybrid_retrieve(_PROVIDER.embed_text("body-0"), limit=3, quality_threshold=0.0)
    assert len(result.nodes) == 3


def test_results_sorted_by_composite_score_descending(mesh: Mesh) -> None:
    for i in range(8):
        _seed(mesh, f"body-{i}")
    result = mesh._hybrid_retrieve(_PROVIDER.embed_text("body-0"), limit=8, quality_threshold=0.0)
    scores = [s.composite_score for s in result.nodes]
    assert scores == sorted(scores, reverse=True)


def test_quality_threshold_drops_low_score_nodes(mesh: Mesh) -> None:
    for i in range(6):
        _seed(mesh, f"body-{i}")
    low_threshold = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"), limit=6, quality_threshold=0.0
    )
    high_threshold = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"), limit=6, quality_threshold=99.0
    )
    assert len(high_threshold.nodes) < len(low_threshold.nodes)


def test_min_content_score_drops_irrelevant(mesh: Mesh) -> None:
    _seed(mesh, "totally unrelated content xyz")
    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("a different topic entirely"),
        min_content_score=80.0,
    )
    assert result.nodes == []
    assert result.cluster_confidence is None


def test_zero_quality_threshold_returns_all_above_min_content_score(mesh: Mesh) -> None:
    for i in range(5):
        _seed(mesh, f"body-{i}")
    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"),
        limit=5,
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    assert len(result.nodes) == 5


def test_kind_filter_excludes_other_kinds(mesh: Mesh) -> None:
    for body in ("ep-1", "ep-2"):
        _seed(mesh, body, kind="episodic")
    for body in ("sem-1", "sem-2"):
        _seed(mesh, body, kind="semantic")

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("ep-1"),
        limit=10,
        kind="semantic",
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    assert len(result.nodes) == 2
    assert all(s.node.kind == "semantic" for s in result.nodes)


def test_scope_id_filter_excludes_other_scopes(mesh: Mesh) -> None:
    for body in ("a-1", "a-2"):
        _seed(mesh, body, scope_id="scope-a")
    for body in ("b-1", "b-2"):
        _seed(mesh, body, scope_id="scope-b")

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("a-1"),
        limit=10,
        scope_id="scope-b",
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    assert len(result.nodes) == 2
    assert all(s.node.scope_id == "scope-b" for s in result.nodes)


def test_kind_and_scope_id_filters_combine_with_and(mesh: Mesh) -> None:
    _seed(mesh, "x1", kind="episodic", scope_id="scope-a")
    _seed(mesh, "x2", kind="semantic", scope_id="scope-a")
    _seed(mesh, "x3", kind="semantic", scope_id="scope-b")
    _seed(mesh, "x4", kind="episodic", scope_id="scope-b")

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("x1"),
        limit=10,
        kind="semantic",
        scope_id="scope-a",
        quality_threshold=0.0,
    )
    assert len(result.nodes) == 1
    assert result.nodes[0].node.body == "x2"


def test_graph_expansion_includes_connected_node_below_min_content_score(
    mesh: Mesh,
) -> None:
    a = _seed(mesh, "the very specific webhook timeout investigation")
    b = _seed(mesh, "a wholly unrelated topic about kitchens")
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="generalizes",
        created_by="manual",
        confidence=0.9,
    )
    mesh.add_edge(edge)

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("the very specific webhook timeout investigation"),
        limit=5,
        min_content_score=80.0,
        quality_threshold=0.0,
    )
    ids = {s.node.id for s in result.nodes}
    assert b.id in ids
    b_scored = next(s for s in result.nodes if s.node.id == b.id)
    assert b_scored.relevance_score == pytest.approx(90.0, abs=0.5)
    assert b_scored.semantic_score == pytest.approx(0.0)


def test_graph_expansion_traverses_only_specified_relations(mesh: Mesh) -> None:
    a = _seed(mesh, "primary anchor node")
    included_relations: tuple[EdgeRelation, ...] = (
        "applies_to",
        "generalizes",
        "supersedes",
        "contradicts",
    )
    excluded_relations: tuple[EdgeRelation, ...] = ("caused_by", "co_occurs_with")
    included_targets = []
    excluded_targets = []
    for relation in included_relations:
        target = _seed(mesh, f"included-{relation}-target")
        included_targets.append(target)
        edge = mesh.make_edge(
            from_node_id=a.id,
            to_node_id=target.id,
            relation=relation,
            created_by="manual",
            confidence=0.9,
        )
        mesh.add_edge(edge)
    for relation in excluded_relations:
        target = _seed(mesh, f"excluded-{relation}-target")
        excluded_targets.append(target)
        edge = mesh.make_edge(
            from_node_id=a.id,
            to_node_id=target.id,
            relation=relation,
            created_by="manual",
            confidence=0.9,
        )
        mesh.add_edge(edge)

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("primary anchor node"),
        limit=20,
        min_content_score=80.0,
        quality_threshold=0.0,
    )
    ids = {s.node.id for s in result.nodes}
    for t in included_targets:
        assert t.id in ids
    for t in excluded_targets:
        assert t.id not in ids


def test_graph_expansion_does_not_duplicate_node_reached_by_multiple_paths(
    mesh: Mesh,
) -> None:
    a = _seed(mesh, "primary anchor node")
    c = _seed(mesh, "a wholly unrelated topic about kitchens")
    e1 = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=c.id,
        relation="generalizes",
        created_by="manual",
        confidence=0.5,
    )
    e2 = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=c.id,
        relation="supersedes",
        created_by="manual",
        confidence=0.9,
    )
    mesh.add_edge(e1)
    mesh.add_edge(e2)

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("primary anchor node"),
        limit=10,
        min_content_score=80.0,
        quality_threshold=0.0,
    )
    c_entries = [s for s in result.nodes if s.node.id == c.id]
    assert len(c_entries) == 1
    assert c_entries[0].relevance_score == pytest.approx(90.0, abs=0.5)


def test_graph_expansion_relevance_score_uses_edge_confidence(mesh: Mesh) -> None:
    a = _seed(mesh, "primary anchor node")
    b = _seed(mesh, "a wholly unrelated topic about kitchens")
    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
        confidence=0.6,
    )
    mesh.add_edge(edge)

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("primary anchor node"),
        limit=5,
        min_content_score=80.0,
        quality_threshold=0.0,
    )
    b_scored = next(s for s in result.nodes if s.node.id == b.id)
    assert b_scored.relevance_score == pytest.approx(60.0, abs=0.5)


def test_cluster_edges_only_contains_edges_among_returned_nodes(mesh: Mesh) -> None:
    a = _seed(mesh, "alpha-body")
    b = _seed(mesh, "beta-body")
    outsider = _seed(mesh, "totally outsider unconnected node xyz")
    edge_in = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="applies_to",
        created_by="manual",
        confidence=1.0,
    )
    edge_out = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=outsider.id,
        relation="applies_to",
        created_by="manual",
        confidence=1.0,
    )
    mesh.add_edge(edge_in)
    mesh.add_edge(edge_out)

    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("alpha-body"),
        limit=2,
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    selected_ids = {s.node.id for s in result.nodes}
    assert len(result.nodes) == 2
    for edge in result.edges:
        assert edge.from_node_id in selected_ids
        assert edge.to_node_id in selected_ids
    edge_ids = {e.id for e in result.edges}
    assert edge_out.id not in edge_ids


def test_cluster_confidence_is_mean_composite_over_100(mesh: Mesh) -> None:
    for i in range(3):
        _seed(mesh, f"body-{i}")
    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"),
        limit=3,
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    assert result.cluster_confidence is not None
    expected = sum(s.composite_score for s in result.nodes) / (len(result.nodes) * 100.0)
    assert result.cluster_confidence == pytest.approx(expected)
    assert 0.0 <= result.cluster_confidence <= 1.0


def test_cluster_confidence_none_for_empty_cluster(mesh: Mesh) -> None:
    _seed(mesh, "totally unrelated content xyz")
    result = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("a different topic entirely"),
        min_content_score=99.0,
    )
    assert result.nodes == []
    assert result.cluster_confidence is None


def test_custom_min_content_score_changes_result_count(mesh: Mesh) -> None:
    for i in range(8):
        _seed(mesh, f"body-{i}")
    permissive = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"),
        limit=8,
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    strict = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"),
        limit=8,
        min_content_score=99.0,
        quality_threshold=0.0,
    )
    assert len(strict.nodes) < len(permissive.nodes)


def test_custom_quality_threshold_changes_result_count(mesh: Mesh) -> None:
    for i in range(8):
        _seed(mesh, f"body-{i}")
    permissive = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"),
        limit=8,
        min_content_score=0.0,
        quality_threshold=0.0,
    )
    strict = mesh._hybrid_retrieve(
        _PROVIDER.embed_text("body-0"),
        limit=8,
        min_content_score=0.0,
        quality_threshold=99.0,
    )
    assert len(strict.nodes) < len(permissive.nodes)
