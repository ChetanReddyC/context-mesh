"""Tests for `Mesh.search` — public hybrid retrieval surface."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from context_mesh.api import MemoryCluster, Mesh
from context_mesh.embeddings import DeterministicEmbeddingProvider, HuggingFaceProvider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryNode, NodeKind

_PROVIDER = DeterministicEmbeddingProvider()
_MODEL = _PROVIDER.name


@pytest.fixture()
def mesh(tmp_path: Path) -> Iterator[Mesh]:
    m = Mesh.local(tmp_path / "search.db")
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


def _retrieve_audits(mesh_obj: Mesh) -> list[tuple[str, str, str, int, dict[str, Any]]]:
    rows = mesh_obj.connection.execute(
        "SELECT event_type, actor, query, result_count, metadata "
        "FROM audit WHERE event_type = 'retrieve' ORDER BY rowid"
    ).fetchall()
    out: list[tuple[str, str, str, int, dict[str, Any]]] = []
    for event_type, actor, query, result_count, metadata_json in rows:
        metadata = json.loads(metadata_json) if metadata_json else {}
        out.append((event_type, actor, query, int(result_count), metadata))
    return out


def test_search_full_flow_with_deterministic_provider(mesh: Mesh) -> None:
    bodies = ["alpha cart auth bug", "beta webhook drift", "gamma deploy command"]
    nodes = [_seed(mesh, b) for b in bodies]

    cluster = mesh.search(bodies[0], embedder=_PROVIDER, limit=3)

    assert isinstance(cluster, MemoryCluster)
    assert cluster.nodes
    assert cluster.nodes[0].node.id == nodes[0].id
    assert cluster.cluster_confidence is not None
    assert 0.0 < cluster.cluster_confidence <= 1.0


def test_search_audits_retrieve_event(mesh: Mesh) -> None:
    _seed(mesh, "alpha cart auth bug")

    mesh.search("alpha cart auth bug", embedder=_PROVIDER, limit=2, kind="semantic")

    audits = _retrieve_audits(mesh)
    assert len(audits) == 1
    event_type, actor, query, result_count, metadata = audits[0]
    assert event_type == "retrieve"
    assert actor == "mesh"
    assert query == "alpha cart auth bug"
    assert result_count == 1
    assert metadata["limit"] == 2
    assert metadata["kind"] == "semantic"
    assert metadata["scope_id"] is None
    assert metadata["embedder"] == _PROVIDER.name


def test_search_with_huggingface_provider_via_mocktransport(
    mesh: Mesh, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "alpha cart auth bug"
    node = _seed(mesh, body)
    fixed_vector = _PROVIDER.embed_text(body)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixed_vector)

    original_init = httpx.Client.__init__

    def patched_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    hf = HuggingFaceProvider(api_key="test-key")
    cluster = mesh.search(body, embedder=hf, limit=3)

    assert cluster.nodes
    assert cluster.nodes[0].node.id == node.id

    audits = _retrieve_audits(mesh)
    assert len(audits) == 1
    assert audits[0][4]["embedder"] == "huggingface/all-MiniLM-L6-v2"


def test_search_uses_provider_dimensions(mesh: Mesh) -> None:
    _seed(mesh, "alpha")
    cluster = mesh.search("alpha", embedder=_PROVIDER, limit=1)
    assert cluster.nodes
    assert _PROVIDER.dimensions == 384


def test_search_filter_propagates_to_kNN_and_graph_expansion(mesh: Mesh) -> None:
    a = _seed(mesh, "auth flow rule", kind="semantic", scope_id="scope-a")
    _seed(mesh, "auth flow episode", kind="episodic", scope_id="scope-a")
    b = _seed(mesh, "auth deploy step", kind="procedural", scope_id="scope-a")
    _seed(mesh, "auth other scope rule", kind="semantic", scope_id="scope-b")

    edge = mesh.make_edge(
        from_node_id=a.id,
        to_node_id=b.id,
        relation="generalizes",
        created_by="manual",
        confidence=0.9,
    )
    mesh.add_edge(edge)

    cluster = mesh.search(
        "auth flow rule", embedder=_PROVIDER, limit=10, kind="semantic", scope_id="scope-a"
    )
    for scored in cluster.nodes:
        assert scored.node.kind == "semantic"
        assert scored.node.scope_id == "scope-a"


def test_search_default_actor_is_mesh(mesh: Mesh) -> None:
    _seed(mesh, "alpha")
    mesh.search("alpha", embedder=_PROVIDER)

    audits = _retrieve_audits(mesh)
    assert len(audits) == 1
    assert audits[0][1] == "mesh"


def test_search_custom_actor_in_audit(mesh: Mesh) -> None:
    _seed(mesh, "alpha")
    mesh.search("alpha", embedder=_PROVIDER, actor="agent:test")

    audits = _retrieve_audits(mesh)
    assert len(audits) == 1
    assert audits[0][1] == "agent:test"


def test_search_returns_empty_cluster_for_unmatched_query(mesh: Mesh) -> None:
    _seed(mesh, "alpha")

    cluster = mesh.search(
        "totally different query string",
        embedder=_PROVIDER,
        limit=5,
        quality_threshold=99.9,
    )
    assert cluster.nodes == []
    assert cluster.edges == []
    assert cluster.cluster_confidence is None

    audits = _retrieve_audits(mesh)
    assert len(audits) == 1
    assert audits[0][3] == 0


def test_search_does_not_audit_on_embedder_exception(mesh: Mesh) -> None:
    _seed(mesh, "alpha")

    class ExplodingEmbedder:
        name = "exploding/v1"
        dimensions = 384

        def embed_text(self, text: str) -> list[float]:
            raise RuntimeError("boom")

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        mesh.search("alpha", embedder=ExplodingEmbedder())

    assert _retrieve_audits(mesh) == []


def test_search_rejects_non_string_query(mesh: Mesh) -> None:
    with pytest.raises(TypeError, match="query must be a string"):
        mesh.search(123, embedder=_PROVIDER)  # type: ignore[arg-type]


def test_search_rejects_negative_limit(mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="limit must be non-negative"):
        mesh.search("alpha", embedder=_PROVIDER, limit=-1)


def test_search_rejects_invalid_kind(mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="invalid node kind"):
        mesh.search("alpha", embedder=_PROVIDER, kind="bogus")  # type: ignore[arg-type]


def test_search_rejects_empty_actor(mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="actor must be a non-empty string"):
        mesh.search("alpha", embedder=_PROVIDER, actor="")
