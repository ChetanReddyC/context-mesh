"""Tests for the `context-mesh audit` CLI command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_audit_empty_store(empty_db: Path) -> None:
    result = runner.invoke(app, ["audit", "--db", str(empty_db)])
    assert result.exit_code == 0, result.output
    assert "no audit events" in result.output


def test_audit_lists_seeded_events(seeded_db: Path) -> None:
    result = runner.invoke(app, ["audit", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert "[add]" in result.output


def test_audit_filter_by_event_type(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        ["audit", "--event-type", "add", "--db", str(seeded_db)],
    )
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if line.strip():
            assert "[add]" in line


def test_audit_filter_by_actor(seeded_db: Path) -> None:
    result = runner.invoke(
        app,
        ["audit", "--actor", "mesh", "--db", str(seeded_db)],
    )
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if line.strip():
            assert line.endswith("mesh")


def test_audit_json(seeded_db: Path) -> None:
    result = runner.invoke(app, ["audit", "--db", str(seeded_db), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload
    sample = payload[0]
    for key in ("id", "event_type", "actor", "node_ids", "metadata", "timestamp"):
        assert key in sample


def test_audit_limit_bounds(seeded_db: Path) -> None:
    result = runner.invoke(app, ["audit", "--limit", "0", "--db", str(seeded_db)])
    assert result.exit_code != 0


def test_audit_missing_db_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    result = runner.invoke(app, ["audit", "--db", str(missing)])
    assert result.exit_code == 1
    assert "database not found" in result.stderr
