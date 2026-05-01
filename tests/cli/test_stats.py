"""Tests for the `context-mesh stats` CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_stats_empty_store(empty_db: Path) -> None:
    result = runner.invoke(app, ["stats", "--db", str(empty_db)])
    assert result.exit_code == 0, result.output
    assert "Nodes: 0" in result.output
    assert "Edges: 0" in result.output
    assert "Vectors: 0" in result.output
    assert "Last audit event: never" in result.output


def test_stats_seeded_store(seeded_db: Path) -> None:
    result = runner.invoke(app, ["stats", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert "Nodes: 3" in result.output
    assert "episodic" in result.output
    assert "semantic" in result.output
    assert "procedural" in result.output
    assert "Edges: 2" in result.output
    assert "Vectors: 3" in result.output
    assert "Last audit event:" in result.output


def test_stats_missing_db_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    result = runner.invoke(app, ["stats", "--db", str(missing)])
    assert result.exit_code == 1
    assert "database not found" in result.stderr
