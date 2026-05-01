"""Tests for the `context-mesh search` CLI command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_search_returns_results_for_seeded_match(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        ["search", "auth requires JWT verification on every request", "--db", str(seeded_db)],
    )
    assert result.exit_code == 0, result.output
    assert "results (cluster confidence:" in result.output
    assert "JWT auth requirement" in result.output


def test_search_no_results_message(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "search",
            "totally unrelated kubernetes terraform",
            "--db",
            str(seeded_db),
            "--limit",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no results" in result.output


def test_search_json_shape(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "search",
            "auth requires JWT verification on every request",
            "--db",
            str(seeded_db),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "cluster_confidence" in payload
    assert "nodes" in payload
    assert "edges" in payload
    assert isinstance(payload["nodes"], list)


def test_search_filters_by_kind(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "search",
            "auth requires JWT verification on every request",
            "--kind",
            "semantic",
            "--db",
            str(seeded_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[semantic]" in result.output
    assert "[episodic]" not in result.output


def test_search_rejects_invalid_kind(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        ["search", "anything", "--kind", "bogus", "--db", str(seeded_db)],
    )
    assert result.exit_code == 1
    assert "invalid kind" in result.stderr


def test_search_missing_db_errors(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    result = runner.invoke(app, ["search", "anything", "--db", str(missing)])
    assert result.exit_code == 1
    assert "database not found" in result.stderr


def test_search_limit_bounds_enforced(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        ["search", "anything", "--db", str(seeded_db), "--limit", "0"],
    )
    assert result.exit_code != 0
