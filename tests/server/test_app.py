"""Tests for the stdlib HTTP server (`context_mesh.server.app`)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from context_mesh import __version__
from context_mesh.api import Mesh

if TYPE_CHECKING:
    from .conftest import ServerCtx


def test_health_returns_ok(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.get("/health", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == __version__


def test_health_requires_auth(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.get("/health")
    assert response.status_code == 401
    assert response.json() == {"error": "missing or invalid bearer token"}


def test_search_rejects_missing_token(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.post("/search", json={"query": "x"})
    assert response.status_code == 401


def test_search_rejects_wrong_token(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.post(
        "/search",
        json={"query": "x"},
        headers={"Authorization": "Bearer not-the-real-token"},
    )
    assert response.status_code == 401


def test_node_get_rejects_missing_token(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.get("/node/anything")
    assert response.status_code == 401


def test_node_post_rejects_missing_token(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.post("/node", json={"content": "x", "kind": "semantic"})
    assert response.status_code == 401


def test_feedback_rejects_missing_token(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.post("/feedback", json={"node_id": "x", "helpful": True})
    assert response.status_code == 401


def test_contradictions_rejects_missing_token(server_ctx: ServerCtx) -> None:
    response = server_ctx.client.post("/contradictions", json={"content": "x"})
    assert response.status_code == 401


def test_search_returns_cluster(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.post(
        "/search",
        headers=auth_headers,
        json={"query": "auth requires JWT verification on every request", "limit": 3},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "cluster_confidence" in payload
    assert "nodes" in payload
    assert "edges" in payload
    assert isinstance(payload["nodes"], list)
    assert payload["nodes"], "expected at least one match"
    headlines = [scored["node"]["headline"] for scored in payload["nodes"]]
    assert "JWT auth requirement" in headlines


def test_search_with_kind_filter(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.post(
        "/search",
        headers=auth_headers,
        json={
            "query": "auth requires JWT verification on every request",
            "kind": "semantic",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    for scored in payload["nodes"]:
        assert scored["node"]["kind"] == "semantic"


def test_search_malformed_json_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/search",
        headers={**auth_headers, "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_search_missing_query_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post("/search", headers=auth_headers, json={})
    assert response.status_code == 400


def test_search_invalid_kind_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/search",
        headers=auth_headers,
        json={"query": "x", "kind": "bogus"},
    )
    assert response.status_code == 400


def test_node_get_returns_node_and_edges(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    mesh = Mesh.local(server_ctx.db_path)
    try:
        nodes = mesh.list_nodes(kind="semantic")
        node_id = nodes[0].id
    finally:
        mesh.close()

    response = server_ctx.client.get(f"/node/{node_id}", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["node"]["id"] == node_id
    assert isinstance(payload["out_edges"], list)
    assert isinstance(payload["in_edges"], list)
    assert payload["out_edges"], "seeded semantic node should have an out edge"


def test_node_get_unknown_returns_404(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.get("/node/does-not-exist", headers=auth_headers)
    assert response.status_code == 404
    assert "error" in response.json()


def test_node_post_creates_node(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.post(
        "/node",
        headers=auth_headers,
        json={
            "content": "first line is the headline\nrest is body",
            "kind": "semantic",
            "scope_id": "test",
            "source_session_id": "s1",
            "source_repo": "repo-a",
            "tags": ["created-via-http"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "node_id" in payload
    assert payload["auto_inferred_edges"] == []
    assert payload["requires_review"] is True

    mesh = Mesh.local(server_ctx.db_path)
    try:
        node = mesh.get(payload["node_id"])
    finally:
        mesh.close()
    assert node is not None
    assert node.kind == "semantic"
    assert node.tags == ["created-via-http"]
    assert node.headline == "first line is the headline"


def test_node_post_missing_kind_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/node",
        headers=auth_headers,
        json={"content": "body without kind"},
    )
    assert response.status_code == 400


def test_node_post_missing_content_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/node",
        headers=auth_headers,
        json={"kind": "semantic"},
    )
    assert response.status_code == 400


def test_feedback_increments_counts(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    mesh = Mesh.local(server_ctx.db_path)
    try:
        nodes = mesh.list_nodes(kind="semantic")
        node_id = nodes[0].id
        before = nodes[0]
    finally:
        mesh.close()

    response = server_ctx.client.post(
        "/feedback",
        headers=auth_headers,
        json={"node_id": node_id, "helpful": True, "notes": "very useful"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["node_id"] == node_id
    assert payload["usage_count"] == before.usage_count + 1
    assert payload["helpful_count"] == before.helpful_count + 1


def test_feedback_unknown_node_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/feedback",
        headers=auth_headers,
        json={"node_id": "ghost-id", "helpful": False},
    )
    assert response.status_code == 400
    assert "not found" in response.json()["error"]


def test_contradictions_returns_results(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/contradictions",
        headers=auth_headers,
        json={
            "content": "auth requires JWT verification on every request",
            "limit": 3,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "results" in payload
    assert isinstance(payload["results"], list)
    assert payload["results"]
    assert payload["results"][0]["headline"] == "JWT auth requirement"


def test_contradictions_empty_content_returns_400(
    server_ctx: ServerCtx, auth_headers: dict[str, str]
) -> None:
    response = server_ctx.client.post(
        "/contradictions",
        headers=auth_headers,
        json={"content": ""},
    )
    assert response.status_code == 400


def test_unknown_path_returns_404(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.get("/", headers=auth_headers)
    assert response.status_code == 404


def test_search_via_get_returns_405(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.get("/search", headers=auth_headers)
    assert response.status_code == 405
    assert "Allow" in response.headers
    assert "POST" in response.headers["Allow"]


def test_health_via_post_returns_405(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.post("/health", headers=auth_headers, json={})
    assert response.status_code == 405


def test_search_response_is_valid_json(server_ctx: ServerCtx, auth_headers: dict[str, str]) -> None:
    response = server_ctx.client.post(
        "/search",
        headers=auth_headers,
        json={"query": "JWT"},
    )
    # Will raise if not valid JSON.
    json.loads(response.text)
