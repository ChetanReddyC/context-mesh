"""SQL migration runner for the context-mesh storage layer."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import sqlite3

_logger: Final = logging.getLogger(__name__)

_MIGRATION_FILENAME_RE: Final = re.compile(r"^(\d{4})_([a-z][a-z0-9_]*)\.sql$")
_MIGRATIONS_PACKAGE: Final = "context_mesh.storage.migrations"


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    applied_at: int


class MigrationError(Exception):
    """Base class for migration runner failures."""


class MalformedMigrationFilenameError(MigrationError):
    """A file in the migrations package does not match the canonical naming pattern."""


class DuplicateMigrationVersionError(MigrationError):
    """Two or more migration files share the same version number."""


class NonContiguousMigrationsError(MigrationError):
    """Migration versions on disk are not a contiguous 1..N sequence."""


class MigrationDriftError(MigrationError):
    """An applied migration's recorded name no longer matches the file on disk."""


class MissingMigrationError(MigrationError):
    """An applied migration is no longer present on disk."""


@dataclass(frozen=True, slots=True)
class _DiscoveredMigration:
    version: int
    name: str
    filename: str


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at INTEGER NOT NULL
        )
        """
    )


def _discover_migrations() -> list[_DiscoveredMigration]:
    package_root = files(_MIGRATIONS_PACKAGE)
    discovered: list[_DiscoveredMigration] = []
    for entry in package_root.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.endswith(".sql"):
            continue
        match = _MIGRATION_FILENAME_RE.match(entry.name)
        if match is None:
            raise MalformedMigrationFilenameError(
                f"Migration filename does not match required pattern: {entry.name!r}"
            )
        version = int(match.group(1))
        name = entry.name[:-4]
        discovered.append(_DiscoveredMigration(version=version, name=name, filename=entry.name))

    discovered.sort(key=lambda m: m.version)

    seen: set[int] = set()
    for migration in discovered:
        if migration.version in seen:
            raise DuplicateMigrationVersionError(
                f"Duplicate migration version on disk: {migration.version}"
            )
        seen.add(migration.version)

    if discovered:
        expected = list(range(1, len(discovered) + 1))
        actual = [m.version for m in discovered]
        if actual != expected:
            raise NonContiguousMigrationsError(
                f"Migration versions are not contiguous starting at 1: {actual}"
            )

    return discovered


def applied_versions(conn: sqlite3.Connection) -> list[AppliedMigration]:
    _ensure_schema_version_table(conn)
    rows = conn.execute(
        "SELECT version, name, applied_at FROM schema_version ORDER BY version"
    ).fetchall()
    return [AppliedMigration(version=row[0], name=row[1], applied_at=row[2]) for row in rows]


def _check_drift(
    discovered: list[_DiscoveredMigration],
    applied: list[AppliedMigration],
) -> None:
    by_version: dict[int, _DiscoveredMigration] = {m.version: m for m in discovered}
    for record in applied:
        on_disk = by_version.get(record.version)
        if on_disk is None:
            raise MissingMigrationError(
                f"Applied migration {record.version} ({record.name}) not found on disk"
            )
        if on_disk.name != record.name:
            raise MigrationDriftError(
                f"Migration {record.version} drift: applied as {record.name!r}, "
                f"now on disk as {on_disk.name!r}"
            )


def _read_migration_sql(filename: str) -> str:
    return (files(_MIGRATIONS_PACKAGE) / filename).read_text(encoding="utf-8")


def apply_migrations(conn: sqlite3.Connection) -> list[AppliedMigration]:
    discovered = _discover_migrations()
    applied = applied_versions(conn)
    _check_drift(discovered, applied)

    applied_set = {record.version for record in applied}
    pending = [m for m in discovered if m.version not in applied_set]
    if not pending:
        return []

    newly_applied: list[AppliedMigration] = []
    for migration in pending:
        sql = _read_migration_sql(migration.filename)
        conn.execute("BEGIN")
        try:
            conn.executescript(sql)
            timestamp = int(time.time())
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, timestamp),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        _logger.info("applied migration %d (%s)", migration.version, migration.name)
        newly_applied.append(
            AppliedMigration(
                version=migration.version,
                name=migration.name,
                applied_at=timestamp,
            )
        )

    return newly_applied
