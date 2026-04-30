"""Tests for the migration runner and the v1 initial schema."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from context_mesh.storage import apply_migrations
from context_mesh.storage.migrations import _MIGRATION_FILENAME_RE

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture()
def migrated_db(in_memory_db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    apply_migrations(in_memory_db)
    in_memory_db.execute("PRAGMA foreign_keys = ON")
    yield in_memory_db


def _insert_minimal_scope_and_session(conn: sqlite3.Connection) -> tuple[str, str]:
    scope_id = "scope-1"
    session_id = "session-1"
    conn.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        (scope_id, "private", "private", 1),
    )
    conn.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        (session_id, "repo-a", "claude-code", 1),
    )
    return scope_id, session_id


def _insert_minimal_node(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    scope_id: str,
    session_id: str,
    content_hash: str,
    kind: str = "episodic",
) -> None:
    conn.execute(
        """
        INSERT INTO nodes (
            id, kind, body, headline, scope_id, source_session_id,
            source_repo, created_at, updated_at, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (node_id, kind, "body", "headline", scope_id, session_id, "repo-a", 1, 1, content_hash),
    )


def test_apply_migrations_creates_schema_version_table(
    in_memory_db: sqlite3.Connection,
) -> None:
    apply_migrations(in_memory_db)
    row = in_memory_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    assert row == ("schema_version",)
    rows = in_memory_db.execute(
        "SELECT version, name FROM schema_version ORDER BY version"
    ).fetchall()
    assert rows == [(1, "0001_initial_schema")]


def test_apply_migrations_records_initial_migration(
    in_memory_db: sqlite3.Connection,
) -> None:
    applied = apply_migrations(in_memory_db)
    assert len(applied) == 1
    record = applied[0]
    assert record.version == 1
    assert record.name == "0001_initial_schema"
    assert isinstance(record.applied_at, int)
    assert record.applied_at > 0


def test_apply_migrations_is_idempotent(in_memory_db: sqlite3.Connection) -> None:
    first = apply_migrations(in_memory_db)
    second = apply_migrations(in_memory_db)
    assert len(first) == 1
    assert second == []
    count = in_memory_db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 1


@pytest.mark.parametrize(
    "table_name",
    [
        "scopes",
        "sessions",
        "nodes",
        "edges",
        "vec_nodes",
        "vector_meta",
        "audit",
        "schema_version",
    ],
)
def test_all_expected_tables_exist(in_memory_db: sqlite3.Connection, table_name: str) -> None:
    apply_migrations(in_memory_db)
    row = in_memory_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    assert row is not None, f"missing table: {table_name}"
    assert row[0] == table_name


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_sessions_repo",
        "idx_sessions_agent",
        "idx_nodes_kind",
        "idx_nodes_scope",
        "idx_nodes_repo",
        "idx_nodes_decayed",
        "idx_edges_from",
        "idx_edges_to",
        "idx_edges_relation",
        "idx_audit_timestamp",
        "idx_audit_actor",
        "idx_audit_event",
    ],
)
def test_all_expected_indexes_exist(in_memory_db: sqlite3.Connection, index_name: str) -> None:
    apply_migrations(in_memory_db)
    row = in_memory_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    assert row is not None, f"missing index: {index_name}"
    assert row[0] == index_name


def test_kind_check_constraint_rejects_invalid_value(
    migrated_db: sqlite3.Connection,
) -> None:
    scope_id, session_id = _insert_minimal_scope_and_session(migrated_db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_minimal_node(
            migrated_db,
            node_id="node-bad",
            scope_id=scope_id,
            session_id=session_id,
            content_hash="hash-bad",
            kind="invalid",
        )


def test_relation_check_constraint_rejects_invalid_value(
    migrated_db: sqlite3.Connection,
) -> None:
    scope_id, session_id = _insert_minimal_scope_and_session(migrated_db)
    _insert_minimal_node(
        migrated_db,
        node_id="node-a",
        scope_id=scope_id,
        session_id=session_id,
        content_hash="hash-a",
    )
    _insert_minimal_node(
        migrated_db,
        node_id="node-b",
        scope_id=scope_id,
        session_id=session_id,
        content_hash="hash-b",
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            """
            INSERT INTO edges (id, from_node_id, to_node_id, relation, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("edge-1", "node-a", "node-b", "definitely_not_a_relation", 1, "auto"),
        )


def test_unique_content_hash(migrated_db: sqlite3.Connection) -> None:
    scope_id, session_id = _insert_minimal_scope_and_session(migrated_db)
    _insert_minimal_node(
        migrated_db,
        node_id="node-a",
        scope_id=scope_id,
        session_id=session_id,
        content_hash="hash-shared",
    )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_minimal_node(
            migrated_db,
            node_id="node-b",
            scope_id=scope_id,
            session_id=session_id,
            content_hash="hash-shared",
        )


def test_unique_edge_triple(migrated_db: sqlite3.Connection) -> None:
    scope_id, session_id = _insert_minimal_scope_and_session(migrated_db)
    _insert_minimal_node(
        migrated_db,
        node_id="node-a",
        scope_id=scope_id,
        session_id=session_id,
        content_hash="hash-a",
    )
    _insert_minimal_node(
        migrated_db,
        node_id="node-b",
        scope_id=scope_id,
        session_id=session_id,
        content_hash="hash-b",
    )
    migrated_db.execute(
        """
        INSERT INTO edges (id, from_node_id, to_node_id, relation, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("edge-1", "node-a", "node-b", "applies_to", 1, "auto"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            """
            INSERT INTO edges (id, from_node_id, to_node_id, relation, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("edge-2", "node-a", "node-b", "applies_to", 2, "manual"),
        )


@pytest.mark.parametrize(
    ("filename", "should_match"),
    [
        ("0001_initial_schema.sql", True),
        ("0042_add_decay_column.sql", True),
        ("0007_a.sql", True),
        ("foo.sql", False),
        ("0001_X.sql", False),
        ("1_x.sql", False),
        ("0001_initial_schema.txt", False),
        ("0001_Initial.sql", False),
        ("0001_.sql", False),
        ("0001_2bad.sql", False),
    ],
)
def test_migration_filename_regex(filename: str, should_match: bool) -> None:
    match = _MIGRATION_FILENAME_RE.match(filename)
    assert (match is not None) is should_match
