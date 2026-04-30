"""Shared pytest fixtures for the context-mesh test suite."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
import sqlite_vec

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture()
def in_memory_db() -> Iterator[sqlite3.Connection]:
    """Yield an in-memory SQLite connection with sqlite-vec loaded."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
    finally:
        conn.close()
