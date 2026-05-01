"""Shared fixtures for CLI unit tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from context_mesh.api import Mesh
from context_mesh.embeddings import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    """Initialized memory.db with no nodes/edges/vectors."""
    db_path = tmp_path / "memory.db"
    mesh = Mesh.local(db_path)
    try:
        pass
    finally:
        mesh.close()
    return db_path


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """memory.db seeded with three nodes (one of each kind), two edges, vectors on every node."""
    db_path = tmp_path / "memory.db"
    mesh = Mesh.local(db_path)
    try:
        embedder = DeterministicEmbeddingProvider()
        nodes = [
            mesh.make_node(
                kind="semantic",
                body="auth requires JWT verification on every request",
                headline="JWT auth requirement",
                scope_id="test",
                source_session_id="s1",
                source_repo="repo-a",
                tags=["auth"],
            ),
            mesh.make_node(
                kind="episodic",
                body="fixed login bug by re-validating tokens",
                headline="login fix",
                scope_id="test",
                source_session_id="s1",
                source_repo="repo-a",
                tags=["bug"],
            ),
            mesh.make_node(
                kind="procedural",
                body="run pytest -x to stop on first failure",
                headline="how to test",
                scope_id="other",
                source_session_id="s2",
                source_repo="repo-b",
            ),
        ]
        _seed_scope_session(mesh, "test", "s1", "repo-a")
        _seed_scope_session(mesh, "other", "s2", "repo-b")
        for n in nodes:
            mesh.add(n)
            mesh.set_vector(n.id, embedder.embed_text(n.body), embedder.name)
        mesh.add_edge(
            mesh.make_edge(
                from_node_id=nodes[0].id,
                to_node_id=nodes[1].id,
                relation="generalizes",
                created_by="auto",
                confidence=0.6,
            )
        )
        mesh.add_edge(
            mesh.make_edge(
                from_node_id=nodes[2].id,
                to_node_id=nodes[1].id,
                relation="applies_to",
                created_by="manual",
                confidence=0.9,
            )
        )
    finally:
        mesh.close()
    return db_path


def _seed_scope_session(mesh: Mesh, scope_id: str, session_id: str, repo: str) -> None:
    conn = mesh.connection
    if conn.execute("SELECT 1 FROM scopes WHERE id = ?", (scope_id,)).fetchone() is None:
        conn.execute(
            "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, 'private', 1)",
            (scope_id, scope_id),
        )
    if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
        conn.execute(
            "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, 'claude-code', 1)",
            (session_id, repo),
        )
    conn.commit()
