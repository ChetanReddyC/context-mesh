"""Tests for SqliteVecBackend bootstrap, pragmas, and vector wiring."""

from __future__ import annotations

import sqlite3
import struct
from typing import TYPE_CHECKING

import pytest

from context_mesh.storage import SqliteVecBackend

if TYPE_CHECKING:
    from pathlib import Path


def test_in_memory_backend_applies_migrations() -> None:
    backend = SqliteVecBackend(":memory:")
    try:
        applied = backend.applied_migrations
        assert len(applied) == 2
        assert [m.version for m in applied] == [1, 2]
        rows = backend.connection.execute(
            "SELECT version, name FROM schema_version ORDER BY version"
        ).fetchall()
        assert rows == [
            (1, "0001_initial_schema"),
            (2, "0002_adapter_sync_state"),
        ]
    finally:
        backend.close()


def test_file_backed_backend_creates_file(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    backend = SqliteVecBackend(db_path)
    backend.close()
    assert db_path.exists()


def test_file_backed_backend_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    first = SqliteVecBackend(db_path)
    first.close()
    second = SqliteVecBackend(db_path)
    try:
        assert second.applied_migrations == []
        count = second.connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        assert count == 2
    finally:
        second.close()


def test_vec_nodes_accepts_384d_vector_and_knn() -> None:
    backend = SqliteVecBackend(":memory:")
    try:
        vector = struct.pack("384f", *([0.1] * 384))
        backend.connection.execute(
            "INSERT INTO vec_nodes (node_id, embedding) VALUES (?, ?)",
            ("node-1", vector),
        )
        rows = backend.connection.execute(
            "SELECT node_id FROM vec_nodes WHERE embedding MATCH ? AND k = 1",
            (vector,),
        ).fetchall()
        assert rows == [("node-1",)]
    finally:
        backend.close()


def test_foreign_keys_enabled() -> None:
    backend = SqliteVecBackend(":memory:")
    try:
        row = backend.connection.execute("PRAGMA foreign_keys").fetchone()
        assert row == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            backend.connection.execute(
                """
                INSERT INTO nodes (
                    id, kind, body, headline, scope_id, source_session_id,
                    source_repo, created_at, updated_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "n-1",
                    "episodic",
                    "body",
                    "headline",
                    "scope-does-not-exist",
                    "session-does-not-exist",
                    "repo-a",
                    1,
                    1,
                    "hash-1",
                ),
            )
    finally:
        backend.close()


def test_wal_mode_set_on_file_backend(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    backend = SqliteVecBackend(db_path)
    try:
        mode = backend.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        backend.close()


def test_wal_mode_not_set_on_memory_backend() -> None:
    backend = SqliteVecBackend(":memory:")
    try:
        mode = backend.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() != "wal"
    finally:
        backend.close()


def test_context_manager_closes_connection() -> None:
    with SqliteVecBackend(":memory:") as backend:
        assert backend.connection.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(sqlite3.ProgrammingError):
        backend.connection.execute("SELECT 1")


def test_applied_migrations_returns_defensive_copy() -> None:
    backend = SqliteVecBackend(":memory:")
    try:
        snapshot = backend.applied_migrations
        snapshot.clear()
        assert len(backend.applied_migrations) == 2
    finally:
        backend.close()
