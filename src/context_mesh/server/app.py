"""Stdlib-only HTTP server exposing the five `Mesh` agent tools.

Endpoints (all JSON):

    GET  /health                  liveness probe (no auth bypass; auth required)
    POST /search                  Mesh.search
    GET  /node/{id}               Mesh.get + Mesh.get_edges
    POST /node                    Mesh.add
    POST /feedback                Mesh.mark_used
    POST /contradictions          Mesh.find_contradictions

Every request requires `Authorization: Bearer <token>`. The token is read
from (or generated into) `~/.context-mesh/token`. A fresh `Mesh.local`
connection is opened per request so SQLite stays in `check_same_thread=True`
mode without a global lock.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import secrets
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from context_mesh import __version__
from context_mesh._logging import get_logger
from context_mesh.api import Mesh
from context_mesh.api.types import (
    VALID_NODE_KINDS,
    MemoryCluster,
    MemoryEdge,
    MemoryNode,
    NodeKind,
)
from context_mesh.embeddings import DeterministicEmbeddingProvider

if TYPE_CHECKING:
    from context_mesh.embeddings.protocol import EmbeddingProvider

DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 7421
TOKEN_FILE: Final[Path] = Path.home() / ".context-mesh" / "token"

_TOKEN_BYTES: Final[int] = 32
_MAX_REQUEST_BYTES: Final[int] = 1 * 1024 * 1024
_BEARER_PREFIX: Final[str] = "Bearer "
_ACTOR: Final[str] = "server"
_LOGGER_NAME: Final[str] = "context_mesh.server"


def load_or_create_token(path: Path = TOKEN_FILE) -> str:
    """Return the persisted bearer token, generating one on first use.

    The file is created with `0o600` permissions on POSIX. On Windows the
    chmod is a no-op but we still call it for symmetry.
    """
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    path.write_text(token + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return token


def _node_to_dict(node: MemoryNode) -> dict[str, Any]:
    return dataclasses.asdict(node)


def _edge_to_dict(edge: MemoryEdge) -> dict[str, Any]:
    return dataclasses.asdict(edge)


def _cluster_to_dict(cluster: MemoryCluster) -> dict[str, Any]:
    return {
        "cluster_confidence": cluster.cluster_confidence,
        "nodes": [
            {
                "node": _node_to_dict(scored.node),
                "semantic_score": scored.semantic_score,
                "recency_score": scored.recency_score,
                "importance_score": scored.importance_score,
                "usage_score": scored.usage_score,
                "relevance_score": scored.relevance_score,
                "composite_score": scored.composite_score,
            }
            for scored in cluster.nodes
        ],
        "edges": [_edge_to_dict(edge) for edge in cluster.edges],
    }


def _validate_kind(value: Any, *, field: str = "kind") -> NodeKind | None:
    """Coerce a JSON value to a `NodeKind` or `None`. Raises `ValueError`."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if value not in VALID_NODE_KINDS:
        raise ValueError(f"invalid {field} {value!r}; expected one of {sorted(VALID_NODE_KINDS)}")
    return value  # type: ignore[return-value]


class _MeshHandler(BaseHTTPRequestHandler):
    """Dispatches the six endpoints. Spawned per request by ThreadingHTTPServer."""

    server_version = f"context-mesh/{__version__}"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress the default stderr access log; we emit structured logs ourselves."""

    @property
    def _server(self) -> MeshServer:
        return self.server.mesh_server  # type: ignore[attr-defined,no-any-return]

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        started = time.perf_counter()
        status = HTTPStatus.INTERNAL_SERVER_ERROR
        try:
            status = self._route(method)
        except Exception:
            traceback_text = traceback.format_exc()
            self._server.logger.error(
                "request_unhandled_exception",
                method=method,
                path=self.path,
                traceback=traceback_text,
            )
            try:
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal server error"},
                )
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            except Exception:
                pass
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._server.logger.info(
                "request_received",
                method=method,
                path=self.path,
                status=int(status),
                duration_ms=round(duration_ms, 2),
            )

    def _route(self, method: str) -> HTTPStatus:
        path = self.path.split("?", 1)[0]

        if not self._authenticate():
            return self._write_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "missing or invalid bearer token"},
            )

        if path == "/health":
            if method != "GET":
                return self._method_not_allowed(("GET",))
            return self._write_json(
                HTTPStatus.OK,
                {"status": "ok", "version": __version__},
            )

        if path == "/search":
            if method != "POST":
                return self._method_not_allowed(("POST",))
            return self._handle_search()

        if path.startswith("/node/"):
            node_id = path[len("/node/") :]
            if not node_id:
                return self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if method != "GET":
                return self._method_not_allowed(("GET",))
            return self._handle_get_node(node_id)

        if path == "/node":
            if method != "POST":
                return self._method_not_allowed(("POST",))
            return self._handle_add_node()

        if path == "/feedback":
            if method != "POST":
                return self._method_not_allowed(("POST",))
            return self._handle_feedback()

        if path == "/contradictions":
            if method != "POST":
                return self._method_not_allowed(("POST",))
            return self._handle_contradictions()

        return self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _authenticate(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith(_BEARER_PREFIX):
            return False
        candidate = header[len(_BEARER_PREFIX) :].strip()
        if not candidate:
            return False
        return secrets.compare_digest(candidate, self._server.token)

    def _method_not_allowed(self, allowed: tuple[str, ...]) -> HTTPStatus:
        body = json.dumps({"error": "method not allowed"}).encode("utf-8")
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Allow", ", ".join(allowed))
        self.end_headers()
        self.wfile.write(body)
        return HTTPStatus.METHOD_NOT_ALLOWED

    def _read_json_body(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header is not None else 0
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > _MAX_REQUEST_BYTES:
            raise ValueError("request body too large")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> HTTPStatus:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return status

    def _handle_search(self) -> HTTPStatus:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "query must be a non-empty string"},
            )

        try:
            kind = _validate_kind(payload.get("kind"))
        except ValueError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        scope_id = payload.get("scope_id")
        if scope_id is not None and not isinstance(scope_id, str):
            return self._write_json(
                HTTPStatus.BAD_REQUEST, {"error": "scope_id must be a string or null"}
            )

        limit_value = payload.get("limit", 5)
        if not isinstance(limit_value, int) or isinstance(limit_value, bool):
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer"})

        mesh = self._server.open_mesh()
        try:
            cluster = mesh.search(
                query,
                embedder=self._server.embedder,
                limit=limit_value,
                kind=kind,
                scope_id=scope_id,
                actor=_ACTOR,
            )
        except ValueError as exc:
            mesh.close()
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        else:
            mesh.close()
        return self._write_json(HTTPStatus.OK, _cluster_to_dict(cluster))

    def _handle_get_node(self, node_id: str) -> HTTPStatus:
        mesh = self._server.open_mesh()
        try:
            node = mesh.get(node_id)
            if node is None:
                return self._write_json(
                    HTTPStatus.NOT_FOUND, {"error": f"node {node_id!r} not found"}
                )
            out_edges = mesh.get_edges(node_id, direction="out")
            in_edges = mesh.get_edges(node_id, direction="in")
        finally:
            mesh.close()
        return self._write_json(
            HTTPStatus.OK,
            {
                "node": _node_to_dict(node),
                "out_edges": [_edge_to_dict(e) for e in out_edges],
                "in_edges": [_edge_to_dict(e) for e in in_edges],
            },
        )

    def _handle_add_node(self) -> HTTPStatus:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "content must be a non-empty string"},
            )

        try:
            kind = _validate_kind(payload.get("kind"))
        except ValueError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        if kind is None:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": "kind is required"})

        headline_raw = payload.get("headline")
        if headline_raw is not None and not isinstance(headline_raw, str):
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": "headline must be a string"})
        if headline_raw is None or not headline_raw.strip():
            stripped = content.strip()
            first_line = stripped.splitlines()[0] if stripped else ""
            headline = first_line[:120]
        else:
            headline = headline_raw

        scope_id = payload.get("scope_id", "agent")
        source_session_id = payload.get("source_session_id", "agent:adhoc")
        source_repo = payload.get("source_repo", "agent")
        if not isinstance(scope_id, str) or not scope_id.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST, {"error": "scope_id must be a non-empty string"}
            )
        if not isinstance(source_session_id, str) or not source_session_id.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "source_session_id must be a non-empty string"},
            )
        if not isinstance(source_repo, str) or not source_repo.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "source_repo must be a non-empty string"},
            )

        tags_raw = payload.get("tags")
        tags: list[str] | None
        if tags_raw is None:
            tags = None
        elif isinstance(tags_raw, list) and all(isinstance(t, str) for t in tags_raw):
            tags = tags_raw or None
        else:
            return self._write_json(
                HTTPStatus.BAD_REQUEST, {"error": "tags must be a list of strings"}
            )

        mesh = self._server.open_mesh()
        try:
            self._ensure_scope(mesh, scope_id)
            self._ensure_session(mesh, source_session_id, source_repo)
            try:
                node = mesh.make_node(
                    kind=kind,
                    body=content,
                    headline=headline,
                    scope_id=scope_id,
                    source_session_id=source_session_id,
                    source_repo=source_repo,
                    tags=tags,
                )
                node_id = mesh.add(node)
            except ValueError as exc:
                return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            mesh.close()

        return self._write_json(
            HTTPStatus.OK,
            {
                "node_id": node_id,
                "auto_inferred_edges": [],
                "requires_review": True,
            },
        )

    def _handle_feedback(self) -> HTTPStatus:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        node_id = payload.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "node_id must be a non-empty string"},
            )

        helpful = payload.get("helpful")
        if not isinstance(helpful, bool):
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": "helpful must be a boolean"})

        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            return self._write_json(
                HTTPStatus.BAD_REQUEST, {"error": "notes must be a string or null"}
            )

        mesh = self._server.open_mesh()
        try:
            try:
                refreshed = mesh.mark_used(node_id, helpful, notes=notes, actor=_ACTOR)
            except ValueError as exc:
                return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            mesh.close()

        return self._write_json(
            HTTPStatus.OK,
            {
                "node_id": refreshed.id,
                "usage_count": refreshed.usage_count,
                "helpful_count": refreshed.helpful_count,
            },
        )

    def _handle_contradictions(self) -> HTTPStatus:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            return self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "content must be a non-empty string"},
            )

        kind_raw = payload.get("kind", "semantic")
        if not isinstance(kind_raw, str):
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": "kind must be a string"})

        limit_value = payload.get("limit", 5)
        if not isinstance(limit_value, int) or isinstance(limit_value, bool):
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer"})

        scope_id = payload.get("scope_id")
        if scope_id is not None and not isinstance(scope_id, str):
            return self._write_json(
                HTTPStatus.BAD_REQUEST, {"error": "scope_id must be a string or null"}
            )

        mesh = self._server.open_mesh()
        try:
            try:
                results = mesh.find_contradictions(
                    content,
                    embedder=self._server.embedder,
                    kind=kind_raw,  # type: ignore[arg-type]
                    limit=limit_value,
                    scope_id=scope_id,
                    actor=_ACTOR,
                )
            except ValueError as exc:
                return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            mesh.close()

        return self._write_json(
            HTTPStatus.OK,
            {"results": [_node_to_dict(n) for n in results]},
        )

    @staticmethod
    def _ensure_scope(mesh: Mesh, scope_id: str) -> None:
        conn = mesh.connection
        row = conn.execute("SELECT 1 FROM scopes WHERE id = ?", (scope_id,)).fetchone()
        if row is not None:
            return
        conn.execute(
            "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, 'private', ?)",
            (scope_id, scope_id, int(time.time())),
        )
        conn.commit()

    @staticmethod
    def _ensure_session(mesh: Mesh, session_id: str, repo: str) -> None:
        conn = mesh.connection
        row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is not None:
            return
        conn.execute(
            "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, 'server', ?)",
            (session_id, repo, int(time.time())),
        )
        conn.commit()


class _ThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with daemon worker threads and a back-reference to MeshServer."""

    daemon_threads = True
    allow_reuse_address = True
    mesh_server: MeshServer


def make_app(server: MeshServer) -> _ThreadingHTTPServer:
    """Build a `ThreadingHTTPServer` bound to the given `MeshServer`."""
    httpd = _ThreadingHTTPServer((server.host, server.port), _MeshHandler)
    httpd.mesh_server = server
    return httpd


class MeshServer:
    """Stdlib HTTP server fronting a `Mesh` store.

    Each request opens a fresh `Mesh.local` connection so SQLite stays in
    `check_same_thread=True` mode without a global mutation lock.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        token: str | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._db_path: Path = db_path
        self._host: str = host
        self._port: int = port
        self._token: str = token if token is not None else load_or_create_token()
        self._embedder: EmbeddingProvider = (
            embedder if embedder is not None else DeterministicEmbeddingProvider()
        )
        self._httpd: _ThreadingHTTPServer | None = None
        self._stopped = threading.Event()
        self._logger = get_logger(_LOGGER_NAME)

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def embedder(self) -> EmbeddingProvider:
        return self._embedder

    @property
    def logger(self) -> Any:
        return self._logger

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def open_mesh(self) -> Mesh:
        return Mesh.local(self._db_path)

    def start(self) -> None:
        """Block and serve until `stop()` is called."""
        httpd = make_app(self)
        self._httpd = httpd
        # If the user passed port 0, capture the OS-assigned port.
        bound_host, bound_port = httpd.server_address[:2]
        self._host = str(bound_host)
        self._port = int(bound_port)
        self._stopped.clear()
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
            self._httpd = None
            self._stopped.set()

    def start_in_thread(self) -> threading.Thread:
        """Convenience: start the server on a daemon background thread.

        Used by the test suite. The returned thread is started and running
        before this method returns; `self.url` is valid as soon as it returns.
        """
        httpd = make_app(self)
        self._httpd = httpd
        bound_host, bound_port = httpd.server_address[:2]
        self._host = str(bound_host)
        self._port = int(bound_port)
        self._stopped.clear()

        def _run() -> None:
            try:
                httpd.serve_forever()
            finally:
                httpd.server_close()
                self._stopped.set()

        thread = threading.Thread(target=_run, name="context-mesh-server", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Signal the server loop to exit. Idempotent."""
        httpd = self._httpd
        if httpd is None:
            return
        httpd.shutdown()
        self._httpd = None


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "TOKEN_FILE",
    "MeshServer",
    "load_or_create_token",
    "make_app",
]
