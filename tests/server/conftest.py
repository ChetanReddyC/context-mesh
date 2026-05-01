"""Shared fixtures for HTTP server tests."""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, NamedTuple

import httpx
import pytest

from context_mesh.api import Mesh
from context_mesh.embeddings import DeterministicEmbeddingProvider
from context_mesh.server import MeshServer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class ServerCtx(NamedTuple):
    client: httpx.Client
    base_url: str
    token: str
    db_path: Path


def _seed_scope_session(mesh: Mesh, scope_id: str, session_id: str, repo: str) -> None:
    conn = mesh.connection
    if conn.execute("SELECT 1 FROM scopes WHERE id = ?", (scope_id,)).fetchone() is None:
        conn.execute(
            "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, 'private', ?)",
            (scope_id, scope_id, int(time.time())),
        )
    if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
        conn.execute(
            "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, 'server', ?)",
            (session_id, repo, int(time.time())),
        )
    conn.commit()


def _seed(db_path: Path) -> None:
    mesh = Mesh.local(db_path)
    try:
        _seed_scope_session(mesh, "test", "s1", "repo-a")
        _seed_scope_session(mesh, "other", "s2", "repo-b")
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
    finally:
        mesh.close()


@pytest.fixture()
def server_ctx(tmp_path: Path) -> Iterator[ServerCtx]:
    """Start a `MeshServer` against a freshly seeded tmp DB on an OS-assigned port."""
    db_path = tmp_path / "memory.db"
    _seed(db_path)
    token = secrets.token_urlsafe(16)
    server = MeshServer(db_path=db_path, host="127.0.0.1", port=0, token=token)
    server.start_in_thread()
    base_url = server.url
    client = httpx.Client(base_url=base_url, timeout=10.0)
    try:
        yield ServerCtx(client=client, base_url=base_url, token=token, db_path=db_path)
    finally:
        client.close()
        server.stop()


@pytest.fixture()
def auth_headers(server_ctx: ServerCtx) -> dict[str, str]:
    return {"Authorization": f"Bearer {server_ctx.token}"}
