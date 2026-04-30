"""Public surface of the storage layer."""

from context_mesh.storage.backend import SqliteVecBackend
from context_mesh.storage.migrations import (
    AppliedMigration,
    DuplicateMigrationVersionError,
    MalformedMigrationFilenameError,
    MigrationDriftError,
    MigrationError,
    MissingMigrationError,
    NonContiguousMigrationsError,
    applied_versions,
    apply_migrations,
)

__all__ = [
    "AppliedMigration",
    "DuplicateMigrationVersionError",
    "MalformedMigrationFilenameError",
    "MigrationDriftError",
    "MigrationError",
    "MissingMigrationError",
    "NonContiguousMigrationsError",
    "SqliteVecBackend",
    "applied_versions",
    "apply_migrations",
]
