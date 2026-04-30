"""Append-only audit log for every memory-touching operation.

Every retrieval, mutation, and sync event lands in the `audit` table per
docs/SCHEMA.md section 6. This module exposes a single function, `log`, that
storage / CLI / retrieval layers call directly. It commits on success: audit
events are fire-and-forget by design; the caller never owns the commit.

In addition to the row insert, every event emits a structured stdlib log
record on the `context_mesh.audit` logger so operators can tail the live
stream without opening the database.
"""

from __future__ import annotations

import json
import logging
import sqlite3  # noqa: TC003
import time
import uuid
from typing import Any, Final, Literal, get_args

AuditEventType = Literal[
    "retrieve",
    "inject",
    "add",
    "edit",
    "delete",
    "sync_push",
    "sync_pull",
    "mark_helpful",
    "mark_unhelpful",
    "init",
]

_VALID_EVENT_TYPES: Final[frozenset[str]] = frozenset(get_args(AuditEventType))

_INSERT_SQL: Final[str] = (
    "INSERT INTO audit "
    "(id, event_type, actor, node_ids, query, result_count, metadata, timestamp) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_logger: Final = logging.getLogger("context_mesh.audit")


def log(
    conn: sqlite3.Connection,
    event_type: AuditEventType,
    actor: str,
    *,
    node_ids: list[str] | None = None,
    query: str | None = None,
    result_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Append one row to the `audit` table and emit a structured log record.

    Returns the new row's id.
    """
    if event_type not in _VALID_EVENT_TYPES:
        raise ValueError(
            f"invalid audit event_type {event_type!r}; expected one of {sorted(_VALID_EVENT_TYPES)}"
        )
    if not actor:
        raise ValueError("audit actor must be a non-empty string")

    audit_id = str(uuid.uuid4())
    timestamp = int(time.time())
    node_ids_json = json.dumps(node_ids) if node_ids is not None else None
    metadata_json = json.dumps(metadata) if metadata is not None else None

    conn.execute(
        _INSERT_SQL,
        (
            audit_id,
            event_type,
            actor,
            node_ids_json,
            query,
            result_count,
            metadata_json,
            timestamp,
        ),
    )
    conn.commit()

    _logger.info(
        "audit_event",
        extra={
            "audit_id": audit_id,
            "event_type": event_type,
            "actor": actor,
            "node_ids": node_ids,
            "query": query,
            "result_count": result_count,
            "metadata": metadata,
            "timestamp": timestamp,
        },
    )

    return audit_id


__all__ = ["AuditEventType", "log"]
