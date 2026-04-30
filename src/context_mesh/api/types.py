"""Public dataclasses mirroring the v1 `nodes` and `edges` tables.

Field order in `to_row()` is locked to the column order in
`0001_initial_schema.sql`. JSON-typed columns are serialized at this boundary
so the rest of the codebase only ever sees Python lists and dicts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3  # noqa: TC003
from dataclasses import dataclass, field
from typing import Any, Final, Literal, get_args

NodeKind = Literal["episodic", "semantic", "procedural"]
EdgeRelation = Literal[
    "caused_by",
    "applies_to",
    "contradicts",
    "generalizes",
    "supersedes",
    "co_occurs_with",
]
EdgeCreatedBy = Literal["auto", "manual", "agent"]

VALID_NODE_KINDS: Final[frozenset[str]] = frozenset(get_args(NodeKind))
VALID_EDGE_RELATIONS: Final[frozenset[str]] = frozenset(get_args(EdgeRelation))
VALID_EDGE_CREATED_BY: Final[frozenset[str]] = frozenset(get_args(EdgeCreatedBy))

NODE_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "kind",
    "body",
    "headline",
    "summary",
    "decisions",
    "failed_approaches",
    "warnings",
    "error_signatures",
    "cause_chain",
    "file_dependencies",
    "key_insight",
    "tags",
    "scope_id",
    "source_session_id",
    "source_repo",
    "source_branch",
    "source_commit",
    "created_at",
    "updated_at",
    "decayed_at",
    "importance",
    "usage_count",
    "helpful_count",
    "superseded_by",
    "content_hash",
)

EDGE_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "from_node_id",
    "to_node_id",
    "relation",
    "confidence",
    "created_at",
    "created_by",
    "metadata",
)


def _dump_list(value: list[str] | None) -> str | None:
    return None if value is None else json.dumps(value)


def _load_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON list, got {type(parsed).__name__}")
    return [str(item) for item in parsed]


def _dump_dict(value: dict[str, Any] | None) -> str | None:
    return None if value is None else json.dumps(value)


def _load_dict(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed


@dataclass(slots=True)
class MemoryNode:
    """In-memory representation of one row of the `nodes` table."""

    id: str
    kind: NodeKind
    body: str
    headline: str
    scope_id: str
    source_session_id: str
    source_repo: str
    created_at: int
    updated_at: int
    content_hash: str
    summary: str | None = None
    decisions: list[str] | None = None
    failed_approaches: list[str] | None = None
    warnings: list[str] | None = None
    error_signatures: list[str] | None = None
    cause_chain: list[str] | None = None
    file_dependencies: list[str] | None = None
    key_insight: str | None = None
    tags: list[str] | None = None
    source_branch: str | None = None
    source_commit: str | None = None
    decayed_at: int | None = None
    importance: float = 0.0
    usage_count: int = 0
    helpful_count: int = 0
    superseded_by: str | None = None

    def to_row(self) -> tuple[Any, ...]:
        """Return a tuple in `NODE_COLUMNS` order, ready for `INSERT INTO nodes`."""
        return (
            self.id,
            self.kind,
            self.body,
            self.headline,
            self.summary,
            _dump_list(self.decisions),
            _dump_list(self.failed_approaches),
            _dump_list(self.warnings),
            _dump_list(self.error_signatures),
            _dump_list(self.cause_chain),
            _dump_list(self.file_dependencies),
            self.key_insight,
            _dump_list(self.tags),
            self.scope_id,
            self.source_session_id,
            self.source_repo,
            self.source_branch,
            self.source_commit,
            self.created_at,
            self.updated_at,
            self.decayed_at,
            self.importance,
            self.usage_count,
            self.helpful_count,
            self.superseded_by,
            self.content_hash,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row | tuple[Any, ...]) -> MemoryNode:
        """Reconstruct a `MemoryNode` from a row in `NODE_COLUMNS` order."""
        values = tuple(row)
        if len(values) != len(NODE_COLUMNS):
            raise ValueError(f"expected {len(NODE_COLUMNS)} columns, got {len(values)}")
        (
            id_,
            kind,
            body,
            headline,
            summary,
            decisions,
            failed_approaches,
            warnings,
            error_signatures,
            cause_chain,
            file_dependencies,
            key_insight,
            tags,
            scope_id,
            source_session_id,
            source_repo,
            source_branch,
            source_commit,
            created_at,
            updated_at,
            decayed_at,
            importance,
            usage_count,
            helpful_count,
            superseded_by,
            content_hash,
        ) = values
        return cls(
            id=id_,
            kind=kind,
            body=body,
            headline=headline,
            summary=summary,
            decisions=_load_list(decisions),
            failed_approaches=_load_list(failed_approaches),
            warnings=_load_list(warnings),
            error_signatures=_load_list(error_signatures),
            cause_chain=_load_list(cause_chain),
            file_dependencies=_load_list(file_dependencies),
            key_insight=key_insight,
            tags=_load_list(tags),
            scope_id=scope_id,
            source_session_id=source_session_id,
            source_repo=source_repo,
            source_branch=source_branch,
            source_commit=source_commit,
            created_at=int(created_at),
            updated_at=int(updated_at),
            decayed_at=None if decayed_at is None else int(decayed_at),
            importance=float(importance),
            usage_count=int(usage_count),
            helpful_count=int(helpful_count),
            superseded_by=superseded_by,
            content_hash=content_hash,
        )

    @staticmethod
    def compute_content_hash(
        body: str,
        key_insight: str | None,
        decisions: list[str] | None,
    ) -> str:
        """Deterministic SHA-256 over normalized identity fields.

        Locked algorithm; future migrations replicating it for dedup must use
        exactly this normalization.
        """
        payload = (
            (body or "")
            + "\n"
            + (key_insight or "")
            + "\n"
            + json.dumps(decisions or [], sort_keys=True, ensure_ascii=False)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class MemoryEdge:
    """In-memory representation of one row of the `edges` table."""

    id: str
    from_node_id: str
    to_node_id: str
    relation: EdgeRelation
    created_at: int
    created_by: EdgeCreatedBy
    confidence: float = 1.0
    metadata: dict[str, Any] | None = field(default=None)

    def to_row(self) -> tuple[Any, ...]:
        """Return a tuple in `EDGE_COLUMNS` order, ready for `INSERT INTO edges`."""
        return (
            self.id,
            self.from_node_id,
            self.to_node_id,
            self.relation,
            self.confidence,
            self.created_at,
            self.created_by,
            _dump_dict(self.metadata),
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row | tuple[Any, ...]) -> MemoryEdge:
        """Reconstruct a `MemoryEdge` from a row in `EDGE_COLUMNS` order."""
        values = tuple(row)
        if len(values) != len(EDGE_COLUMNS):
            raise ValueError(f"expected {len(EDGE_COLUMNS)} columns, got {len(values)}")
        (
            id_,
            from_node_id,
            to_node_id,
            relation,
            confidence,
            created_at,
            created_by,
            metadata,
        ) = values
        return cls(
            id=id_,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            relation=relation,
            confidence=float(confidence),
            created_at=int(created_at),
            created_by=created_by,
            metadata=_load_dict(metadata),
        )


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    """One hit from `Mesh.search_by_vector`.

    `distance` uses the metric of the underlying vec0 index (L2 by default in
    sqlite-vec 0.1.9). Smaller distance = more similar. For unit-normalized
    embeddings, L2 ordering is monotonic with cosine similarity.
    """

    node_id: str
    distance: float
    node: MemoryNode


@dataclass(frozen=True, slots=True)
class ScoredNode:
    """One node in a `MemoryCluster`, with composite ranking signals.

    All scores are in the [0, 100] range; `composite_score` is the
    weighted average of the five signals and drives final ordering.
    `relevance_score` is edge-derived; it is 0 for direct vector hits
    that never reached the graph-expansion stage.
    """

    node: MemoryNode
    semantic_score: float
    recency_score: float
    importance_score: float
    usage_score: float
    relevance_score: float
    composite_score: float


@dataclass(frozen=True, slots=True)
class MemoryCluster:
    """Result of hybrid retrieval. The unit returned to agents.

    `nodes` is sorted by descending `composite_score`. `edges` is the
    subset of edges whose endpoints are both within `nodes`.
    `cluster_confidence` is the mean composite score divided by 100,
    clamped to [0, 1]; it is `None` when the cluster is empty.
    """

    nodes: list[ScoredNode]
    edges: list[MemoryEdge]
    cluster_confidence: float | None


__all__ = [
    "EDGE_COLUMNS",
    "NODE_COLUMNS",
    "VALID_EDGE_CREATED_BY",
    "VALID_EDGE_RELATIONS",
    "VALID_NODE_KINDS",
    "EdgeCreatedBy",
    "EdgeRelation",
    "MemoryCluster",
    "MemoryEdge",
    "MemoryNode",
    "NodeKind",
    "ScoredNode",
    "VectorSearchResult",
]
