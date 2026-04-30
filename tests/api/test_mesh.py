"""Tests for `context_mesh.api.mesh.Mesh` — lifecycle and schema bring-up."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from context_mesh import MemoryEdge as TopLevelEdge
from context_mesh import MemoryNode as TopLevelNode
from context_mesh import Mesh as TopLevelMesh
from context_mesh.api import Mesh
from context_mesh.storage import SqliteVecBackend

if TYPE_CHECKING:
    from pathlib import Path


_EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "scopes",
        "sessions",
        "nodes",
        "edges",
        "vec_nodes",
        "vector_meta",
        "audit",
        "schema_version",
    }
)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table','virtual') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def test_mesh_local_in_memory_returns_mesh_instance() -> None:
    mesh = Mesh.local(":memory:")
    try:
        assert isinstance(mesh, Mesh)
    finally:
        mesh.close()


def test_mesh_local_file_creates_db(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    assert not db_path.exists()

    mesh = Mesh.local(db_path)
    try:
        assert db_path.exists()
    finally:
        mesh.close()


def test_mesh_local_applies_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    mesh = Mesh.local(db_path)
    try:
        tables = _table_names(mesh.connection)
        missing = _EXPECTED_TABLES - tables
        assert not missing, f"expected tables missing from schema: {sorted(missing)}"
    finally:
        mesh.close()


def test_mesh_close_closes_underlying_connection() -> None:
    mesh = Mesh.local(":memory:")
    mesh.close()

    with pytest.raises(sqlite3.ProgrammingError):
        mesh.connection.execute("SELECT 1")


def test_mesh_context_manager_closes_on_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    with Mesh.local(db_path) as mesh:
        conn = mesh.connection
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_mesh_context_manager_closes_on_exception(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    captured: dict[str, sqlite3.Connection] = {}

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), Mesh.local(db_path) as mesh:
        captured["conn"] = mesh.connection
        raise _Boom

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


def test_mesh_connection_property_returns_live_connection() -> None:
    mesh = Mesh.local(":memory:")
    try:
        result = mesh.connection.execute("SELECT 1").fetchone()
        assert result == (1,)
    finally:
        mesh.close()


def test_mesh_backend_property_returns_backend() -> None:
    backend = SqliteVecBackend(":memory:")
    mesh = Mesh(backend)
    try:
        assert mesh.backend is backend
    finally:
        mesh.close()


def test_mesh_constructor_accepts_injected_backend() -> None:
    backend = SqliteVecBackend(":memory:")
    mesh = Mesh(backend)
    try:
        assert mesh.connection is backend.connection
    finally:
        mesh.close()


def test_top_level_imports_resolve() -> None:
    assert TopLevelMesh is Mesh
    assert TopLevelNode.__name__ == "MemoryNode"
    assert TopLevelEdge.__name__ == "MemoryEdge"
