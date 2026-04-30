"""Tests for the `context-mesh init` CLI command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh import __version__
from context_mesh.cli import app
from context_mesh.storage import SqliteVecBackend

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_init_creates_directory_and_db(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    db_path = tmp_path / ".context-mesh" / "memory.db"
    assert db_path.exists()

    backend = SqliteVecBackend(db_path)
    try:
        rows = backend.connection.execute("SELECT version FROM schema_version").fetchall()
    finally:
        backend.close()
    assert len(rows) >= 1


def test_init_writes_config_toml(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    config_path = tmp_path / ".context-mesh" / "config.toml"
    assert config_path.exists()

    content = config_path.read_text(encoding="utf-8")
    for section in ("[storage]", "[embeddings]", "[retrieval]", "[adapters]"):
        assert section in content


def test_init_creates_gitignore_if_missing(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert ".context-mesh/" in content
    assert content.endswith("\n")


def test_init_appends_to_existing_gitignore(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    content = gitignore.read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".context-mesh/" in content
    assert content.count(".context-mesh/") == 1


def test_init_gitignore_idempotent(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".context-mesh/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    content = gitignore.read_text(encoding="utf-8")
    assert content.count(".context-mesh/") == 1


def test_init_refuses_existing_db_without_force(tmp_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(app, ["init", str(tmp_path)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["init", str(tmp_path)])
    assert second.exit_code == 1
    assert "already exists" in second.stderr


def test_init_overwrites_existing_db_with_force(tmp_path: Path) -> None:
    mesh_dir = tmp_path / ".context-mesh"
    mesh_dir.mkdir()
    db_path = mesh_dir / "memory.db"
    db_path.write_bytes(b"not a sqlite file at all")

    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])

    assert result.exit_code == 0, result.output
    backend = SqliteVecBackend(db_path)
    try:
        rows = backend.connection.execute("SELECT version FROM schema_version").fetchall()
    finally:
        backend.close()
    assert len(rows) >= 1


def test_init_force_cleans_wal_shm_siblings(tmp_path: Path) -> None:
    mesh_dir = tmp_path / ".context-mesh"
    mesh_dir.mkdir()
    db_path = mesh_dir / "memory.db"
    wal_path = mesh_dir / "memory.db-wal"
    shm_path = mesh_dir / "memory.db-shm"

    junk_db = b"junk-db-bytes"
    junk_wal = b"junk-wal-bytes-longer"
    junk_shm = b"junk-shm"
    db_path.write_bytes(junk_db)
    wal_path.write_bytes(junk_wal)
    shm_path.write_bytes(junk_shm)

    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])

    assert result.exit_code == 0, result.output
    if wal_path.exists():
        assert wal_path.read_bytes() != junk_wal
    if shm_path.exists():
        assert shm_path.read_bytes() != junk_shm
    assert db_path.exists()
    assert db_path.read_bytes() != junk_db


def test_init_does_not_overwrite_existing_config_toml(tmp_path: Path) -> None:
    mesh_dir = tmp_path / ".context-mesh"
    mesh_dir.mkdir()
    config_path = mesh_dir / "config.toml"
    config_path.write_text("# custom\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert config_path.read_text(encoding="utf-8") == "# custom\n"


def test_init_global_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--global"])

    assert result.exit_code == 0, result.output
    db_path = tmp_path / ".context-mesh" / "memory.db"
    assert db_path.exists()
    assert not (tmp_path / ".gitignore").exists()


def test_init_emits_audit_event(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output

    db_path = tmp_path / ".context-mesh" / "memory.db"
    backend = SqliteVecBackend(db_path)
    try:
        rows = backend.connection.execute(
            "SELECT event_type, actor, metadata FROM audit"
        ).fetchall()
    finally:
        backend.close()

    assert len(rows) == 1
    event_type, actor, metadata = rows[0]
    assert event_type == "init"
    assert actor == "cli:init"
    parsed = json.loads(metadata)
    assert isinstance(parsed, dict)
    assert "path" in parsed
    assert "global" in parsed
    assert "force" in parsed


def test_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code in (0, 2)
    assert "Usage:" in result.output
    assert "init" in result.output
