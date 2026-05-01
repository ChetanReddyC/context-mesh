"""High-level memory mesh API. Thin facade over `SqliteVecBackend`."""

from __future__ import annotations

import math
import struct
import time
import uuid
from typing import TYPE_CHECKING, Any, Final, Literal

from context_mesh import _audit
from context_mesh.api.types import (
    EDGE_COLUMNS,
    NODE_COLUMNS,
    VALID_EDGE_CREATED_BY,
    VALID_EDGE_RELATIONS,
    VALID_NODE_KINDS,
    EdgeCreatedBy,
    EdgeRelation,
    MemoryCluster,
    MemoryEdge,
    MemoryNode,
    NodeKind,
    ScoredNode,
    VectorSearchResult,
    _dump_list,
)
from context_mesh.storage import SqliteVecBackend

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path
    from types import TracebackType

    from context_mesh.config import Config
    from context_mesh.distillation.protocol import Distiller, DistillerCandidate
    from context_mesh.embeddings.protocol import EmbeddingProvider


_NODE_COL_LIST: Final[str] = ", ".join(NODE_COLUMNS)
_NODE_PLACEHOLDERS: Final[str] = ", ".join(["?"] * len(NODE_COLUMNS))

_INSERT_NODE_SQL: Final[str] = f"INSERT INTO nodes ({_NODE_COL_LIST}) VALUES ({_NODE_PLACEHOLDERS})"
_SELECT_NODE_BY_ID_SQL: Final[str] = f"SELECT {_NODE_COL_LIST} FROM nodes WHERE id = ?"
_DELETE_NODE_SQL: Final[str] = "DELETE FROM nodes WHERE id = ?"
_MARK_USED_SQL: Final[str] = (
    "UPDATE nodes "
    "SET usage_count = usage_count + 1, "
    "helpful_count = helpful_count + ?, "
    "updated_at = ? "
    "WHERE id = ?"
)

_EDGE_COL_LIST: Final[str] = ", ".join(EDGE_COLUMNS)
_EDGE_PLACEHOLDERS: Final[str] = ", ".join(["?"] * len(EDGE_COLUMNS))

_INSERT_EDGE_SQL: Final[str] = f"INSERT INTO edges ({_EDGE_COL_LIST}) VALUES ({_EDGE_PLACEHOLDERS})"
_SELECT_EDGE_BY_ID_SQL: Final[str] = f"SELECT {_EDGE_COL_LIST} FROM edges WHERE id = ?"
_DELETE_EDGE_SQL: Final[str] = "DELETE FROM edges WHERE id = ?"
_SELECT_EDGE_ENDPOINTS_SQL: Final[str] = "SELECT from_node_id, to_node_id FROM edges WHERE id = ?"

_IMMUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "created_at",
        "content_hash",
        "source_session_id",
        "source_repo",
        "scope_id",
    }
)
_AUTO_STAMPED_FIELDS: Final[frozenset[str]] = frozenset({"updated_at"})
_UPDATABLE_FIELDS: Final[frozenset[str]] = (
    frozenset(NODE_COLUMNS) - _IMMUTABLE_FIELDS - _AUTO_STAMPED_FIELDS
)
_JSON_LIST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "decisions",
        "failed_approaches",
        "warnings",
        "error_signatures",
        "cause_chain",
        "file_dependencies",
        "tags",
    }
)

_ACTOR: Final[str] = "mesh"

_EXPECTED_VECTOR_DIM: Final[int] = 384
_BYTES_PER_FLOAT: Final[int] = 4
_EXPECTED_VECTOR_BYTES: Final[int] = _EXPECTED_VECTOR_DIM * _BYTES_PER_FLOAT
_VECTOR_PACK_FORMAT: Final[str] = f"<{_EXPECTED_VECTOR_DIM}f"

_INSERT_VEC_NODE_SQL: Final[str] = "INSERT INTO vec_nodes(node_id, embedding) VALUES (?, ?)"
_INSERT_VECTOR_META_SQL: Final[str] = (
    "INSERT INTO vector_meta(node_id, model, dimensions, embedded_at) VALUES (?, ?, ?, ?)"
)
_DELETE_VEC_NODE_SQL: Final[str] = "DELETE FROM vec_nodes WHERE node_id = ?"
_DELETE_VECTOR_META_SQL: Final[str] = "DELETE FROM vector_meta WHERE node_id = ?"
_SELECT_VECTOR_SQL: Final[str] = (
    "SELECT vn.embedding, vm.model, vm.dimensions "
    "FROM vec_nodes vn JOIN vector_meta vm ON vn.node_id = vm.node_id "
    "WHERE vn.node_id = ?"
)
_SELECT_VECTOR_META_EXISTS_SQL: Final[str] = "SELECT 1 FROM vector_meta WHERE node_id = ?"

_SEARCH_OVER_FETCH_FACTOR: Final[int] = 4

_SEARCH_BY_VECTOR_INNER_SQL: Final[str] = (
    "WITH knn AS ("
    " SELECT node_id, distance FROM vec_nodes "
    "WHERE embedding MATCH ? ORDER BY distance LIMIT ?"
    ")"
)
_SEARCH_BY_VECTOR_OUTER_SELECT: Final[str] = (
    f"SELECT knn.distance, {', '.join('n.' + c for c in NODE_COLUMNS)} "
    "FROM knn JOIN nodes n ON knn.node_id = n.id"
)
_SEARCH_BY_VECTOR_ORDER_TAIL: Final[str] = " ORDER BY knn.distance LIMIT ?"

_CONTRADICTION_KINDS: Final[frozenset[str]] = frozenset({"semantic", "procedural"})

_DEFAULT_RETRIEVE_LIMIT: Final[int] = 5
_RETRIEVE_OVERFETCH_FACTOR: Final[int] = 4
_RETRIEVE_MIN_OVERFETCH: Final[int] = 20
_GRAPH_EXPAND_FROM_TOP_N: Final[int] = 5

_DEFAULT_MIN_CONTENT_SCORE: Final[float] = 30.0
_DEFAULT_QUALITY_THRESHOLD: Final[float] = 40.0

_RECENCY_HALF_LIFE_DAYS: Final[int] = 30
_SECONDS_PER_DAY: Final[int] = 86400

_W_SEMANTIC: Final[float] = 0.50
_W_RELEVANCE: Final[float] = 0.20
_W_RECENCY: Final[float] = 0.10
_W_IMPORTANCE: Final[float] = 0.10
_W_USAGE: Final[float] = 0.10

_GRAPH_RELATIONS: Final[frozenset[str]] = frozenset(
    {"applies_to", "generalizes", "supersedes", "contradicts"}
)

_DISTILL_INFER_EDGE_CAP: Final[int] = 16
_DISTILL_INFERRED_EDGE_CONFIDENCE: Final[float] = 0.5


def _distill_tuple_to_list(t: tuple[str, ...]) -> list[str] | None:
    return list(t) if t else None


def _compute_recency_score(
    created_at: int, now: int, half_life_days: int = _RECENCY_HALF_LIFE_DAYS
) -> float:
    """Exponential decay: score=100 at age 0, halves every `half_life_days`."""
    age_days = max(0.0, (now - created_at) / _SECONDS_PER_DAY)
    decay = math.exp(-math.log(2) * age_days / half_life_days)
    return decay * 100.0


def _compute_usage_score(usage_count: int, helpful_count: int) -> float:
    """Combine retrieval count + helpful feedback into [0, 100]."""
    base = math.log1p(max(0, usage_count)) * 10.0
    helpful_factor = 1.0 / (1.0 + math.exp(-max(0, helpful_count) / 5.0))
    return min(100.0, base * helpful_factor * 2.0)


def _semantic_score_from_distance(distance: float) -> float:
    """Map L2 distance (unit-normalized vectors → [0, 2]) to similarity [0, 100]."""
    return max(0.0, min(100.0, 100.0 * (1.0 - distance / 2.0)))


def _compute_edge_relevance(edge: MemoryEdge, hop: int = 1) -> float:
    """Edge-derived relevance score in [0, 100], damped by hop distance."""
    return float(edge.confidence) * 100.0 / (1.0 + (hop - 1))


class Mesh:
    """High-level memory mesh API. Wraps a `SqliteVecBackend`."""

    def __init__(self, backend: SqliteVecBackend) -> None:
        self._backend: SqliteVecBackend = backend

    @classmethod
    def local(cls, path: str | Path | Literal[":memory:"]) -> Mesh:
        """Open a Mesh backed by a local SQLite file (or `":memory:"`)."""
        return cls(SqliteVecBackend(path))

    @classmethod
    def from_config(cls, config: Config) -> Mesh:
        """Build a `Mesh` from a resolved `Config`.

        The database path comes from `config.storage.path`. Federation,
        embeddings, and other config fields are *not* applied here — this
        constructor is intentionally narrow. Callers that need them should
        pass the relevant config sections to the modules that consume them.
        """
        from pathlib import Path as _Path

        return cls.local(_Path(config.storage.path))

    def close(self) -> None:
        """Close the underlying database connection."""
        self._backend.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Underlying `sqlite3.Connection`. Prefer the high-level API."""
        return self._backend.connection

    @property
    def backend(self) -> SqliteVecBackend:
        """Underlying backend. Exposed for tests and adapters."""
        return self._backend

    def __enter__(self) -> Mesh:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def make_node(
        *,
        kind: NodeKind,
        body: str,
        headline: str,
        scope_id: str,
        source_session_id: str,
        source_repo: str,
        summary: str | None = None,
        decisions: list[str] | None = None,
        failed_approaches: list[str] | None = None,
        warnings: list[str] | None = None,
        error_signatures: list[str] | None = None,
        cause_chain: list[str] | None = None,
        file_dependencies: list[str] | None = None,
        key_insight: str | None = None,
        tags: list[str] | None = None,
        source_branch: str | None = None,
        source_commit: str | None = None,
        decayed_at: int | None = None,
        importance: float = 0.0,
        usage_count: int = 0,
        helpful_count: int = 0,
        superseded_by: str | None = None,
    ) -> MemoryNode:
        """Construct a `MemoryNode` with auto id, timestamps, and content hash."""
        if kind not in VALID_NODE_KINDS:
            raise ValueError(
                f"invalid node kind {kind!r}; expected one of {sorted(VALID_NODE_KINDS)}"
            )
        now = int(time.time())
        return MemoryNode(
            id=str(uuid.uuid4()),
            kind=kind,
            body=body,
            headline=headline,
            scope_id=scope_id,
            source_session_id=source_session_id,
            source_repo=source_repo,
            created_at=now,
            updated_at=now,
            content_hash=MemoryNode.compute_content_hash(body, key_insight, decisions),
            summary=summary,
            decisions=decisions,
            failed_approaches=failed_approaches,
            warnings=warnings,
            error_signatures=error_signatures,
            cause_chain=cause_chain,
            file_dependencies=file_dependencies,
            key_insight=key_insight,
            tags=tags,
            source_branch=source_branch,
            source_commit=source_commit,
            decayed_at=decayed_at,
            importance=importance,
            usage_count=usage_count,
            helpful_count=helpful_count,
            superseded_by=superseded_by,
        )

    @staticmethod
    def make_edge(
        *,
        from_node_id: str,
        to_node_id: str,
        relation: EdgeRelation,
        created_by: EdgeCreatedBy,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEdge:
        """Construct a `MemoryEdge` with auto id and current timestamp."""
        if relation not in VALID_EDGE_RELATIONS:
            raise ValueError(
                f"invalid edge relation {relation!r}; "
                f"expected one of {sorted(VALID_EDGE_RELATIONS)}"
            )
        if created_by not in VALID_EDGE_CREATED_BY:
            raise ValueError(
                f"invalid edge created_by {created_by!r}; "
                f"expected one of {sorted(VALID_EDGE_CREATED_BY)}"
            )
        return MemoryEdge(
            id=str(uuid.uuid4()),
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            relation=relation,
            created_at=int(time.time()),
            created_by=created_by,
            confidence=confidence,
            metadata=metadata,
        )

    def add(self, node: MemoryNode) -> str:
        """Insert a memory node. Returns the node id. Audits the operation."""
        if node.kind not in VALID_NODE_KINDS:
            raise ValueError(
                f"invalid node kind {node.kind!r}; expected one of {sorted(VALID_NODE_KINDS)}"
            )
        conn = self._backend.connection
        conn.execute(_INSERT_NODE_SQL, node.to_row())
        conn.commit()
        _audit.log(
            conn,
            "add",
            _ACTOR,
            node_ids=[node.id],
            metadata={"kind": node.kind, "scope_id": node.scope_id},
        )
        return node.id

    def get(self, node_id: str) -> MemoryNode | None:
        """Fetch a node by id. Returns None if not found."""
        row = self._backend.connection.execute(_SELECT_NODE_BY_ID_SQL, (node_id,)).fetchone()
        if row is None:
            return None
        return MemoryNode.from_row(row)

    def list_nodes(
        self,
        *,
        kind: NodeKind | None = None,
        scope_id: str | None = None,
        source_repo: str | None = None,
        decayed: bool | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MemoryNode]:
        """List nodes with optional filters.

        `decayed=False` excludes decayed; `decayed=True` returns only decayed.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if kind is not None:
            if kind not in VALID_NODE_KINDS:
                raise ValueError(
                    f"invalid node kind {kind!r}; expected one of {sorted(VALID_NODE_KINDS)}"
                )
            clauses.append("kind = ?")
            params.append(kind)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if source_repo is not None:
            clauses.append("source_repo = ?")
            params.append(source_repo)
        if decayed is True:
            clauses.append("decayed_at IS NOT NULL")
        elif decayed is False:
            clauses.append("decayed_at IS NULL")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_NODE_COL_LIST} FROM nodes{where} ORDER BY created_at, id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.append(limit)
            params.append(offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)

        rows = self._backend.connection.execute(sql, params).fetchall()
        return [MemoryNode.from_row(row) for row in rows]

    def update(self, node_id: str, fields: dict[str, Any]) -> MemoryNode:
        """Update specific fields. Returns refreshed node. Audits."""
        if not fields:
            raise ValueError("no fields to update")

        invalid = set(fields) - _UPDATABLE_FIELDS
        if invalid:
            raise ValueError(
                f"unknown or immutable update field(s): {sorted(invalid)}; "
                f"allowed: {sorted(_UPDATABLE_FIELDS)}"
            )
        if "kind" in fields and fields["kind"] not in VALID_NODE_KINDS:
            raise ValueError(
                f"invalid node kind {fields['kind']!r}; expected one of {sorted(VALID_NODE_KINDS)}"
            )

        cols = list(fields.keys())
        values: list[Any] = []
        for col in cols:
            raw = fields[col]
            if col in _JSON_LIST_FIELDS:
                values.append(_dump_list(raw))
            else:
                values.append(raw)

        cols.append("updated_at")
        values.append(int(time.time()))

        set_clause = ", ".join(f"{col} = ?" for col in cols)
        sql = f"UPDATE nodes SET {set_clause} WHERE id = ?"
        values.append(node_id)

        conn = self._backend.connection
        cursor = conn.execute(sql, values)
        if cursor.rowcount == 0:
            conn.rollback()
            raise ValueError(f"node {node_id!r} not found")
        conn.commit()

        _audit.log(
            conn,
            "edit",
            _ACTOR,
            node_ids=[node_id],
            metadata={"fields": sorted(fields.keys())},
        )

        refreshed = self.get(node_id)
        if refreshed is None:
            raise RuntimeError(f"node {node_id!r} disappeared after update")
        return refreshed

    def delete(self, node_id: str) -> bool:
        """Hard-delete a node. Returns True if deleted, False if missing. Audits."""
        conn = self._backend.connection
        conn.execute(_DELETE_VEC_NODE_SQL, (node_id,))
        cursor = conn.execute(_DELETE_NODE_SQL, (node_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        if deleted:
            _audit.log(conn, "delete", _ACTOR, node_ids=[node_id])
        return deleted

    def add_edge(self, edge: MemoryEdge) -> str:
        """Insert an edge between two existing nodes. Returns edge id. Audits."""
        if edge.relation not in VALID_EDGE_RELATIONS:
            raise ValueError(
                f"invalid edge relation {edge.relation!r}; "
                f"expected one of {sorted(VALID_EDGE_RELATIONS)}"
            )
        if edge.created_by not in VALID_EDGE_CREATED_BY:
            raise ValueError(
                f"invalid edge created_by {edge.created_by!r}; "
                f"expected one of {sorted(VALID_EDGE_CREATED_BY)}"
            )
        conn = self._backend.connection
        conn.execute(_INSERT_EDGE_SQL, edge.to_row())
        conn.commit()
        _audit.log(
            conn,
            "add",
            _ACTOR,
            node_ids=[edge.from_node_id, edge.to_node_id],
            metadata={
                "edge_id": edge.id,
                "relation": edge.relation,
                "confidence": edge.confidence,
            },
        )
        return edge.id

    def get_edge(self, edge_id: str) -> MemoryEdge | None:
        """Fetch an edge by id. Returns None if not found."""
        row = self._backend.connection.execute(_SELECT_EDGE_BY_ID_SQL, (edge_id,)).fetchone()
        if row is None:
            return None
        return MemoryEdge.from_row(row)

    def get_edges(
        self,
        node_id: str,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relation: EdgeRelation | None = None,
    ) -> list[MemoryEdge]:
        """Edges connected to `node_id`."""
        if direction == "out":
            clause = "from_node_id = ?"
            params: list[Any] = [node_id]
        elif direction == "in":
            clause = "to_node_id = ?"
            params = [node_id]
        elif direction == "both":
            clause = "(from_node_id = ? OR to_node_id = ?)"
            params = [node_id, node_id]
        else:
            raise ValueError(f"invalid direction {direction!r}; expected 'out', 'in', or 'both'")

        if relation is not None:
            if relation not in VALID_EDGE_RELATIONS:
                raise ValueError(
                    f"invalid edge relation {relation!r}; "
                    f"expected one of {sorted(VALID_EDGE_RELATIONS)}"
                )
            clause += " AND relation = ?"
            params.append(relation)

        sql = f"SELECT {_EDGE_COL_LIST} FROM edges WHERE {clause} ORDER BY created_at, id"
        rows = self._backend.connection.execute(sql, params).fetchall()
        return [MemoryEdge.from_row(row) for row in rows]

    def list_edges(
        self,
        *,
        relation: EdgeRelation | None = None,
        created_by: EdgeCreatedBy | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MemoryEdge]:
        """List edges with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if relation is not None:
            if relation not in VALID_EDGE_RELATIONS:
                raise ValueError(
                    f"invalid edge relation {relation!r}; "
                    f"expected one of {sorted(VALID_EDGE_RELATIONS)}"
                )
            clauses.append("relation = ?")
            params.append(relation)
        if created_by is not None:
            if created_by not in VALID_EDGE_CREATED_BY:
                raise ValueError(
                    f"invalid edge created_by {created_by!r}; "
                    f"expected one of {sorted(VALID_EDGE_CREATED_BY)}"
                )
            clauses.append("created_by = ?")
            params.append(created_by)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_EDGE_COL_LIST} FROM edges{where} ORDER BY created_at, id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.append(limit)
            params.append(offset)
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)

        rows = self._backend.connection.execute(sql, params).fetchall()
        return [MemoryEdge.from_row(row) for row in rows]

    def delete_edge(self, edge_id: str) -> bool:
        """Hard-delete an edge by id. Pre-checks existence; audits only when deleted."""
        conn = self._backend.connection
        row = conn.execute(_SELECT_EDGE_ENDPOINTS_SQL, (edge_id,)).fetchone()
        if row is None:
            return False
        from_id, to_id = row
        conn.execute(_DELETE_EDGE_SQL, (edge_id,))
        conn.commit()
        _audit.log(
            conn,
            "delete",
            _ACTOR,
            node_ids=[from_id, to_id],
            metadata={"edge_id": edge_id},
        )
        return True

    def set_vector(
        self,
        node_id: str,
        embedding: list[float] | bytes,
        model: str,
        *,
        replace: bool = False,
    ) -> None:
        """Insert (or replace if replace=True) a vector embedding for an existing node."""
        if not model:
            raise ValueError("model must be a non-empty string")
        embedding_bytes = self._coerce_embedding_bytes(embedding)

        conn = self._backend.connection
        existed = conn.execute(_SELECT_VECTOR_META_EXISTS_SQL, (node_id,)).fetchone() is not None
        if existed and not replace:
            raise ValueError(
                f"vector already exists for node {node_id!r}; pass replace=True to overwrite"
            )

        embedded_at = int(time.time())
        conn.execute("BEGIN")
        try:
            if existed:
                conn.execute(_DELETE_VEC_NODE_SQL, (node_id,))
                conn.execute(_DELETE_VECTOR_META_SQL, (node_id,))
            conn.execute(_INSERT_VEC_NODE_SQL, (node_id, embedding_bytes))
            conn.execute(
                _INSERT_VECTOR_META_SQL,
                (node_id, model, _EXPECTED_VECTOR_DIM, embedded_at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        _audit.log(
            conn,
            "edit" if existed else "add",
            _ACTOR,
            node_ids=[node_id],
            metadata={
                "action": "set_vector",
                "model": model,
                "dimensions": _EXPECTED_VECTOR_DIM,
                "replace": existed,
            },
        )

    def get_vector(self, node_id: str) -> tuple[bytes, str, int] | None:
        """Fetch (embedding_bytes, model, dimensions) or None if no vector stored."""
        row = self._backend.connection.execute(_SELECT_VECTOR_SQL, (node_id,)).fetchone()
        if row is None:
            return None
        embedding, model, dimensions = row
        return bytes(embedding), str(model), int(dimensions)

    def has_vector(self, node_id: str) -> bool:
        """Return True if a vector exists for the node."""
        row = self._backend.connection.execute(
            _SELECT_VECTOR_META_EXISTS_SQL, (node_id,)
        ).fetchone()
        return row is not None

    def delete_vector(self, node_id: str) -> bool:
        """Hard-delete the vector for a node from BOTH tables. Returns True if deleted."""
        conn = self._backend.connection
        existed = conn.execute(_SELECT_VECTOR_META_EXISTS_SQL, (node_id,)).fetchone() is not None
        if not existed:
            return False
        conn.execute("BEGIN")
        try:
            conn.execute(_DELETE_VEC_NODE_SQL, (node_id,))
            conn.execute(_DELETE_VECTOR_META_SQL, (node_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        _audit.log(
            conn,
            "delete",
            _ACTOR,
            node_ids=[node_id],
            metadata={"action": "delete_vector"},
        )
        return True

    def search_by_vector(
        self,
        query_embedding: list[float] | bytes,
        *,
        k: int = 10,
        kind: NodeKind | None = None,
        scope_id: str | None = None,
    ) -> list[VectorSearchResult]:
        """Top-k nearest nodes by vec_nodes kNN, ascending distance (L2)."""
        if k < 0:
            raise ValueError(f"k must be non-negative; got {k}")
        if k == 0:
            return []
        if kind is not None and kind not in VALID_NODE_KINDS:
            raise ValueError(
                f"invalid node kind {kind!r}; expected one of {sorted(VALID_NODE_KINDS)}"
            )

        embedding_bytes = self._coerce_embedding_bytes(query_embedding)

        has_filter = kind is not None or scope_id is not None
        inner_limit = k * _SEARCH_OVER_FETCH_FACTOR if has_filter else k

        where_parts: list[str] = []
        params: list[Any] = [embedding_bytes, inner_limit]
        if kind is not None:
            where_parts.append("n.kind = ?")
            params.append(kind)
        if scope_id is not None:
            where_parts.append("n.scope_id = ?")
            params.append(scope_id)

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = (
            _SEARCH_BY_VECTOR_INNER_SQL
            + " "
            + _SEARCH_BY_VECTOR_OUTER_SELECT
            + where_clause
            + _SEARCH_BY_VECTOR_ORDER_TAIL
        )
        params.append(k)

        rows = self._backend.connection.execute(sql, params).fetchall()
        results: list[VectorSearchResult] = []
        for row in rows:
            distance = float(row[0])
            node = MemoryNode.from_row(row[1:])
            results.append(VectorSearchResult(node_id=node.id, distance=distance, node=node))
        return results

    def _hybrid_retrieve(
        self,
        query_embedding: list[float] | bytes,
        *,
        limit: int = _DEFAULT_RETRIEVE_LIMIT,
        kind: NodeKind | None = None,
        scope_id: str | None = None,
        min_content_score: float = _DEFAULT_MIN_CONTENT_SCORE,
        quality_threshold: float = _DEFAULT_QUALITY_THRESHOLD,
        now: int | None = None,
    ) -> MemoryCluster:
        """Run the hybrid retrieval algorithm. Internal; public surface is `search()`."""
        if limit < 0:
            raise ValueError(f"limit must be non-negative; got {limit}")
        if limit == 0:
            return MemoryCluster(nodes=[], edges=[], cluster_confidence=None)

        effective_now = int(time.time()) if now is None else now

        k = max(limit * _RETRIEVE_OVERFETCH_FACTOR, _RETRIEVE_MIN_OVERFETCH)
        candidates = self.search_by_vector(query_embedding, k=k, kind=kind, scope_id=scope_id)

        scored: dict[str, ScoredNode] = {}
        primary_ids: list[str] = []
        for hit in candidates:
            sem = _semantic_score_from_distance(hit.distance)
            if sem < min_content_score:
                continue
            rec = _compute_recency_score(hit.node.created_at, effective_now)
            imp = float(hit.node.importance) * 100.0
            usg = _compute_usage_score(hit.node.usage_count, hit.node.helpful_count)
            composite = (
                sem * _W_SEMANTIC
                + 0.0 * _W_RELEVANCE
                + rec * _W_RECENCY
                + imp * _W_IMPORTANCE
                + usg * _W_USAGE
            )
            scored[hit.node.id] = ScoredNode(
                node=hit.node,
                semantic_score=sem,
                recency_score=rec,
                importance_score=imp,
                usage_score=usg,
                relevance_score=0.0,
                composite_score=composite,
            )
            primary_ids.append(hit.node.id)

        for src_id in primary_ids[:_GRAPH_EXPAND_FROM_TOP_N]:
            for edge in self.get_edges(src_id, direction="both"):
                if edge.relation not in _GRAPH_RELATIONS:
                    continue
                other_id = edge.to_node_id if edge.from_node_id == src_id else edge.from_node_id
                if other_id == src_id:
                    continue
                other_node = self.get(other_id)
                if other_node is None:
                    continue
                if kind is not None and other_node.kind != kind:
                    continue
                if scope_id is not None and other_node.scope_id != scope_id:
                    continue
                new_relevance = _compute_edge_relevance(edge, hop=1)
                existing = scored.get(other_id)
                if existing is not None:
                    if new_relevance <= existing.relevance_score:
                        continue
                    sem = existing.semantic_score
                    rec = existing.recency_score
                    imp = existing.importance_score
                    usg = existing.usage_score
                    node_obj = existing.node
                else:
                    sem = 0.0
                    rec = _compute_recency_score(other_node.created_at, effective_now)
                    imp = float(other_node.importance) * 100.0
                    usg = _compute_usage_score(other_node.usage_count, other_node.helpful_count)
                    node_obj = other_node
                composite = (
                    sem * _W_SEMANTIC
                    + new_relevance * _W_RELEVANCE
                    + rec * _W_RECENCY
                    + imp * _W_IMPORTANCE
                    + usg * _W_USAGE
                )
                scored[other_id] = ScoredNode(
                    node=node_obj,
                    semantic_score=sem,
                    recency_score=rec,
                    importance_score=imp,
                    usage_score=usg,
                    relevance_score=new_relevance,
                    composite_score=composite,
                )

        ranked = sorted(scored.values(), key=lambda s: s.composite_score, reverse=True)
        selected = ranked[:limit]
        selected = [s for s in selected if s.composite_score >= quality_threshold]

        selected_ids = {s.node.id for s in selected}
        edges_among: list[MemoryEdge] = []
        if selected_ids:
            seen_edge_ids: set[str] = set()
            for s in selected:
                for edge in self.get_edges(s.node.id, direction="both"):
                    if edge.id in seen_edge_ids:
                        continue
                    if edge.from_node_id in selected_ids and edge.to_node_id in selected_ids:
                        edges_among.append(edge)
                        seen_edge_ids.add(edge.id)

        if not selected:
            return MemoryCluster(nodes=[], edges=[], cluster_confidence=None)

        confidence_raw = sum(s.composite_score for s in selected) / (len(selected) * 100.0)
        confidence = max(0.0, min(1.0, confidence_raw))
        return MemoryCluster(nodes=selected, edges=edges_among, cluster_confidence=confidence)

    def search(
        self,
        query: str,
        *,
        embedder: EmbeddingProvider,
        limit: int = _DEFAULT_RETRIEVE_LIMIT,
        kind: NodeKind | None = None,
        scope_id: str | None = None,
        actor: str = _ACTOR,
        min_content_score: float | None = None,
        quality_threshold: float | None = None,
    ) -> MemoryCluster:
        """Hybrid memory search.

        Embeds `query`, runs vector kNN + graph expansion + composite ranking,
        and returns a `MemoryCluster`. Audits each retrieval as
        `event_type='retrieve'` with the query, result count, embedder name,
        and filter parameters.
        """
        if not isinstance(query, str):
            raise TypeError(f"query must be a string; got {type(query).__name__}")
        if limit < 0:
            raise ValueError(f"limit must be non-negative; got {limit}")
        if kind is not None and kind not in VALID_NODE_KINDS:
            raise ValueError(
                f"invalid node kind {kind!r}; expected one of {sorted(VALID_NODE_KINDS)}"
            )
        if not actor:
            raise ValueError("actor must be a non-empty string")

        query_embedding = embedder.embed_text(query)

        retrieve_kwargs: dict[str, Any] = {
            "limit": limit,
            "kind": kind,
            "scope_id": scope_id,
        }
        if min_content_score is not None:
            retrieve_kwargs["min_content_score"] = min_content_score
        if quality_threshold is not None:
            retrieve_kwargs["quality_threshold"] = quality_threshold

        cluster = self._hybrid_retrieve(query_embedding, **retrieve_kwargs)

        _audit.log(
            self._backend.connection,
            "retrieve",
            actor,
            query=query,
            result_count=len(cluster.nodes),
            metadata={
                "limit": limit,
                "kind": kind,
                "scope_id": scope_id,
                "embedder": embedder.name,
            },
        )

        return cluster

    def find_contradictions(
        self,
        content: str,
        *,
        embedder: EmbeddingProvider,
        kind: NodeKind = "semantic",
        limit: int = 5,
        scope_id: str | None = None,
        actor: str = _ACTOR,
    ) -> list[MemoryNode]:
        """Return memories most semantically similar to `content` within
        contradiction-prone kinds (`semantic` or `procedural`).

        Embeds `content`, runs a flat vector kNN over `vec_nodes` joined to
        `nodes`, filtered to `kind` (default `"semantic"`) and optionally
        `scope_id`. Returns up to `limit` `MemoryNode`s sorted by ascending
        distance (closest first). Audits each call as
        `event_type='retrieve'` with `metadata.action='find_contradictions'`.

        This is the v1 implementation: it surfaces *similarity*, not logical
        contradiction. The returned nodes are the most likely places where
        a contradiction would occur, ranked by semantic proximity. A future
        LLM-pass over these results is what would turn similarity into a
        contradiction verdict.
        """
        if not content or not content.strip():
            raise ValueError("content must not be empty")
        if kind not in _CONTRADICTION_KINDS:
            raise ValueError("kind must be 'semantic' or 'procedural'")
        if limit < 1:
            raise ValueError(f"limit must be >= 1; got {limit}")
        if not actor or not actor.strip():
            raise ValueError("actor must not be empty")

        query_embedding = embedder.embed_text(content)
        hits = self.search_by_vector(
            query_embedding,
            k=limit,
            kind=kind,
            scope_id=scope_id,
        )
        results = [hit.node for hit in hits]

        _audit.log(
            self._backend.connection,
            "retrieve",
            actor,
            query=content,
            result_count=len(results),
            metadata={
                "action": "find_contradictions",
                "kind": kind,
                "scope_id": scope_id,
                "limit": limit,
                "embedder": embedder.name,
            },
        )

        return results

    def mark_used(
        self,
        node_id: str,
        helpful: bool,
        *,
        notes: str | None = None,
        actor: str = _ACTOR,
    ) -> MemoryNode:
        """Record a usage signal on a node and return the refreshed node.

        Always increments `usage_count` by 1. Increments `helpful_count`
        by 1 only when `helpful=True`. Stamps `updated_at`. Audits as
        `event_type='mark_helpful'` if `helpful` else `'mark_unhelpful'`.
        `notes`, when supplied, lands in the audit row's metadata.
        """
        if not node_id or not node_id.strip():
            raise ValueError("node_id must not be empty")
        if not actor or not actor.strip():
            raise ValueError("actor must not be empty")
        if notes is not None and not isinstance(notes, str):
            raise TypeError(f"notes must be a string or None; got {type(notes).__name__}")

        helpful_increment = 1 if helpful else 0
        now = int(time.time())

        conn = self._backend.connection
        cursor = conn.execute(_MARK_USED_SQL, (helpful_increment, now, node_id))
        if cursor.rowcount == 0:
            conn.rollback()
            raise ValueError(f"node {node_id!r} not found")
        conn.commit()

        event_type: Literal["mark_helpful", "mark_unhelpful"] = (
            "mark_helpful" if helpful else "mark_unhelpful"
        )
        _audit.log(
            conn,
            event_type,
            actor,
            node_ids=[node_id],
            metadata={"notes": notes} if notes else None,
        )

        refreshed = self.get(node_id)
        if refreshed is None:
            raise RuntimeError(f"node {node_id!r} disappeared after mark_used")
        return refreshed

    def distill(
        self,
        session_text: str,
        *,
        distiller: Distiller,
        scope_id: str,
        source_session_id: str,
        source_repo: str,
        actor: str = "agent:distiller",
    ) -> list[MemoryNode]:
        """Distill a session transcript into persisted memory nodes.

        Runs the supplied `distiller` over `session_text`, persists each
        resulting candidate as a `MemoryNode` (one commit per node), and then
        materializes intra-session inferred edges (`semantic` -> `episodic` as
        `generalizes`, `procedural` -> `episodic` as `applies_to`, both with
        `created_by="auto"`). Each node insert and each inferred edge insert
        emits its own `add` audit row tagged with the distiller name and
        candidate provenance. Returns the persisted nodes in distillation
        order. If the input is empty or the distiller yields no candidates,
        returns `[]`.
        """
        if not scope_id or not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        if not source_session_id or not source_session_id.strip():
            raise ValueError("source_session_id must not be empty")
        if not source_repo or not source_repo.strip():
            raise ValueError("source_repo must not be empty")
        if not actor or not actor.strip():
            raise ValueError("actor must not be empty")

        if not session_text or not session_text.strip():
            return []

        candidates = distiller.distill(session_text)
        if not candidates:
            return []

        conn = self._backend.connection
        persisted: list[MemoryNode] = []
        for candidate in candidates:
            node = self.make_node(
                kind=candidate.kind,
                body=candidate.body,
                headline=candidate.headline,
                scope_id=scope_id,
                source_session_id=source_session_id,
                source_repo=source_repo,
                tags=_distill_tuple_to_list(candidate.tags),
                decisions=_distill_tuple_to_list(candidate.decisions),
                failed_approaches=_distill_tuple_to_list(candidate.failed_approaches),
                error_signatures=_distill_tuple_to_list(candidate.error_signatures),
                file_dependencies=_distill_tuple_to_list(candidate.file_paths),
                importance=candidate.importance,
            )
            conn.execute(_INSERT_NODE_SQL, node.to_row())
            conn.commit()

            node_metadata: dict[str, Any] = {
                "kind": node.kind,
                "scope_id": node.scope_id,
                "distiller": distiller.name,
                "headline": node.headline[:60],
                "candidate_confidence": candidate.confidence,
                "redaction_finding_count": len(candidate.redaction_findings),
                "redaction_kinds": sorted({f.kind for f in candidate.redaction_findings}),
                "source_session_id": node.source_session_id,
            }
            _audit.log(
                conn,
                "add",
                actor,
                node_ids=[node.id],
                metadata=node_metadata,
            )
            persisted.append(node)

        inferred_edges = self._infer_intra_session_edges(
            persisted,
            candidates,
            distiller_name=distiller.name,
            source_session_id=source_session_id,
        )

        for edge in inferred_edges:
            conn.execute(_INSERT_EDGE_SQL, edge.to_row())
            conn.commit()
            edge_metadata: dict[str, Any] = {
                "edge_id": edge.id,
                "relation": edge.relation,
                "confidence": edge.confidence,
                "action": "infer_edge",
                "inferred_by": "distill",
                "distiller": distiller.name,
                "from_node_id": edge.from_node_id,
                "to_node_id": edge.to_node_id,
                "session_id": source_session_id,
            }
            _audit.log(
                conn,
                "add",
                actor,
                node_ids=[edge.from_node_id, edge.to_node_id],
                metadata=edge_metadata,
            )

        return persisted

    def _infer_intra_session_edges(
        self,
        persisted: list[MemoryNode],
        candidates: list[DistillerCandidate],
        *,
        distiller_name: str,
        source_session_id: str,
    ) -> list[MemoryEdge]:
        semantic_idx = [i for i, c in enumerate(candidates) if c.kind == "semantic"]
        episodic_idx = [i for i, c in enumerate(candidates) if c.kind == "episodic"]
        procedural_idx = [i for i, c in enumerate(candidates) if c.kind == "procedural"]

        edge_metadata: dict[str, Any] = {
            "inferred_by": "distill",
            "distiller": distiller_name,
            "session_id": source_session_id,
        }

        pairs: list[MemoryEdge] = []
        for s in semantic_idx:
            for e in episodic_idx:
                pairs.append(
                    self.make_edge(
                        from_node_id=persisted[s].id,
                        to_node_id=persisted[e].id,
                        relation="generalizes",
                        created_by="auto",
                        confidence=_DISTILL_INFERRED_EDGE_CONFIDENCE,
                        metadata=dict(edge_metadata),
                    )
                )
                if len(pairs) >= _DISTILL_INFER_EDGE_CAP:
                    return pairs
        for p in procedural_idx:
            for e in episodic_idx:
                pairs.append(
                    self.make_edge(
                        from_node_id=persisted[p].id,
                        to_node_id=persisted[e].id,
                        relation="applies_to",
                        created_by="auto",
                        confidence=_DISTILL_INFERRED_EDGE_CONFIDENCE,
                        metadata=dict(edge_metadata),
                    )
                )
                if len(pairs) >= _DISTILL_INFER_EDGE_CAP:
                    return pairs
        return pairs

    @staticmethod
    def _coerce_embedding_bytes(embedding: list[float] | bytes) -> bytes:
        """Validate and convert embedding to packed little-endian float32 bytes."""
        if isinstance(embedding, bytes):
            if len(embedding) != _EXPECTED_VECTOR_BYTES:
                raise ValueError(
                    f"embedding bytes length {len(embedding)} does not match "
                    f"expected {_EXPECTED_VECTOR_BYTES} "
                    f"({_EXPECTED_VECTOR_DIM} floats x {_BYTES_PER_FLOAT} bytes)"
                )
            return embedding
        if not isinstance(embedding, list):
            raise TypeError(
                f"embedding must be list[float] or bytes; got {type(embedding).__name__}"
            )
        if len(embedding) != _EXPECTED_VECTOR_DIM:
            raise ValueError(
                f"embedding dimension {len(embedding)} does not match "
                f"expected {_EXPECTED_VECTOR_DIM}"
            )
        return struct.pack(_VECTOR_PACK_FORMAT, *embedding)


__all__ = ["Mesh"]
