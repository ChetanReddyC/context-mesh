"""SQLite + sqlite-vec storage backend."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import sqlite_vec

from context_mesh.storage.migrations import AppliedMigration, apply_migrations

if TYPE_CHECKING:
    from types import TracebackType

_logger: Final = logging.getLogger(__name__)
_MEMORY: Final[Literal[":memory:"]] = ":memory:"


class SqliteVecBackend:
    """SQLite-backed storage with sqlite-vec loaded and v1 schema applied."""

    def __init__(self, db_path: str | Path | Literal[":memory:"]) -> None:
        self._db_path: str | Path = db_path
        self._is_memory: bool = db_path == _MEMORY
        self._conn: sqlite3.Connection = self._open()
        self._configure_pragmas()
        self._applied: list[AppliedMigration] = apply_migrations(self._conn)

    def _open(self) -> sqlite3.Connection:
        target = _MEMORY if self._is_memory else str(Path(self._db_path))
        conn = sqlite3.connect(target)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _configure_pragmas(self) -> None:
        self._conn.execute("PRAGMA foreign_keys = ON")
        if not self._is_memory:
            self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def applied_migrations(self) -> list[AppliedMigration]:
        return list(self._applied)

    def __enter__(self) -> SqliteVecBackend:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
