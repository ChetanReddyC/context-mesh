"""Latency smoke test for `Mesh.search` — Phase 2 ROADMAP exit gate."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from context_mesh.api import Mesh
from context_mesh.embeddings import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

_PROVIDER = DeterministicEmbeddingProvider()
_MODEL = _PROVIDER.name

_NUM_NODES = 100
_NUM_QUERIES = 20
_P95_INDEX = 18
_P95_BUDGET_SECONDS = 0.5


def test_search_p95_under_500ms_on_100_nodes(tmp_path: Path) -> None:
    """20 search calls over 100 nodes; p95 latency must stay under 500ms on dev hardware."""
    mesh = Mesh.local(tmp_path / "perf.db")
    try:
        mesh.connection.execute(
            "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
            ("scope-a", "scope-a", "private", 100),
        )
        mesh.connection.execute(
            "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
            ("ses-1", "repo", "claude-code", 100),
        )
        mesh.connection.commit()

        for i in range(_NUM_NODES):
            body = f"perf node {i} payload"
            node = mesh.make_node(
                kind="semantic",
                body=body,
                headline=body[:80],
                scope_id="scope-a",
                source_session_id="ses-1",
                source_repo="repo",
            )
            mesh.add(node)
            mesh.set_vector(node.id, _PROVIDER.embed_text(body), _MODEL)

        latencies: list[float] = []
        for i in range(_NUM_QUERIES):
            query = f"perf node {i} payload"
            start = time.perf_counter()
            mesh.search(query, embedder=_PROVIDER, limit=5)
            latencies.append(time.perf_counter() - start)

        p95 = sorted(latencies)[_P95_INDEX]
        assert p95 < _P95_BUDGET_SECONDS, (
            f"p95 latency {p95 * 1000:.1f}ms exceeds budget "
            f"{_P95_BUDGET_SECONDS * 1000:.0f}ms; sample={latencies}"
        )
    finally:
        mesh.close()
