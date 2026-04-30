"""Retrieval-quality eval harness — 5 golden cases for `Mesh.search`.

These cases are deliberately small and deterministic. They are NOT a
substitute for a real benchmark; they are an exit gate that catches gross
regressions in ranking, filtering, quality gating, and graph expansion.
"""

from __future__ import annotations

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
    m = Mesh.local(tmp_path / "eval.db")
    m.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-a", "scope-a", "private", 100),
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
) -> MemoryNode:
    node = mesh_obj.make_node(
        kind=kind,
        body=body,
        headline=body[:80],
        scope_id="scope-a",
        source_session_id="ses-1",
        source_repo="repo",
    )
    mesh_obj.add(node)
    mesh_obj.set_vector(node.id, _PROVIDER.embed_text(body), _MODEL)
    return node


def test_eval_self_match_top_score(mesh: Mesh) -> None:
    bodies = [
        "Cart auth bug missed guest-checkout path",
        "Stripe webhook signature drift Apr 12 timestamps",
        "Deploy with ./deploy.sh staging then --promote",
    ]
    nodes = [_seed(mesh, b) for b in bodies]

    cluster = mesh.search(bodies[0], embedder=_PROVIDER, limit=3)

    assert cluster.nodes
    assert cluster.nodes[0].node.id == nodes[0].id
    assert cluster.nodes[0].composite_score > 50.0


def test_eval_semantic_neighborhood_orders_correctly(mesh: Mesh) -> None:
    bodies = [
        "Cart auth bug missed guest-checkout path",
        "Always cover both auth flows logged-in and guest",
        "Re-verify webhook timestamps against current_time",
    ]
    nodes = [_seed(mesh, b) for b in bodies]

    cluster = mesh.search(bodies[0], embedder=_PROVIDER, limit=3)

    assert cluster.nodes
    assert cluster.nodes[0].node.id == nodes[0].id


def test_eval_kind_filter_yields_only_filtered_kind(mesh: Mesh) -> None:
    _seed(mesh, "auth rule", kind="semantic")
    _seed(mesh, "auth episode", kind="episodic")
    _seed(mesh, "auth deploy", kind="procedural")
    _seed(mesh, "auth law", kind="semantic")

    cluster = mesh.search("auth", embedder=_PROVIDER, limit=10, kind="semantic")

    for scored in cluster.nodes:
        assert scored.node.kind == "semantic"


def test_eval_quality_gate_drops_irrelevant(mesh: Mesh) -> None:
    _seed(mesh, "completely unrelated body about gardening")

    cluster = mesh.search(
        "stripe webhook drift",
        embedder=_PROVIDER,
        limit=5,
        quality_threshold=99.9,
    )
    assert cluster.nodes == []


def test_eval_graph_expansion_surfaces_low_semantic_node(mesh: Mesh) -> None:
    a = _seed(mesh, "Cart auth bug missed guest-checkout path", kind="episodic")
    b = _seed(mesh, "Always cover both auth flows logged-in and guest", kind="semantic")

    edge = mesh.make_edge(
        from_node_id=b.id,
        to_node_id=a.id,
        relation="generalizes",
        created_by="manual",
        confidence=0.9,
    )
    mesh.add_edge(edge)

    cluster = mesh.search(
        "Cart auth bug missed guest-checkout path",
        embedder=_PROVIDER,
        limit=5,
        quality_threshold=0.0,
    )

    ids = {scored.node.id: scored for scored in cluster.nodes}
    assert a.id in ids
    assert b.id in ids
    assert ids[b.id].relevance_score > 0.0
