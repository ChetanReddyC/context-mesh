"""Tests for the `context-mesh list` CLI command."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.api import Mesh
from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_list_empty_store(empty_db: Path) -> None:
    result = runner.invoke(app, ["list", "--db", str(empty_db)])
    assert result.exit_code == 0, result.output
    assert "no nodes" in result.output


def test_list_seeded_store_table_lines(seeded_db: Path) -> None:
    result = runner.invoke(app, ["list", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 3
    for line in lines:
        assert "[" in line and "]" in line


def test_list_filter_by_kind(seeded_db: Path) -> None:
    result = runner.invoke(app, ["list", "--kind", "semantic", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert "[semantic]" in result.output
    assert "[episodic]" not in result.output
    assert "[procedural]" not in result.output


def test_list_filter_by_scope(seeded_db: Path) -> None:
    result = runner.invoke(app, ["list", "--scope-id", "other", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert "[procedural]" in result.output
    assert "[semantic]" not in result.output


def test_list_json(seeded_db: Path) -> None:
    result = runner.invoke(app, ["list", "--db", str(seeded_db), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 3


def test_list_rejects_invalid_kind(seeded_db: Path) -> None:
    result = runner.invoke(app, ["list", "--kind", "wrong", "--db", str(seeded_db)])
    assert result.exit_code == 1
    assert "invalid kind" in result.stderr


def test_list_limit_bounds(seeded_db: Path) -> None:
    result = runner.invoke(app, ["list", "--limit", "0", "--db", str(seeded_db)])
    assert result.exit_code != 0


def test_list_limit_returns_newest_n(tmp_path: Path) -> None:
    """`--limit N` over more-than-N nodes returns the N newest, not the N oldest."""
    db_path = tmp_path / "memory.db"
    mesh = Mesh.local(db_path)
    try:
        conn = mesh.connection
        conn.execute(
            "INSERT INTO scopes (id, name, level, created_at) VALUES ('s', 's', 'private', 1)"
        )
        conn.execute(
            "INSERT INTO sessions (id, repo, agent, started_at) VALUES ('ses', 'r', 'cli', 1)"
        )
        conn.commit()

        for i in range(6):
            node = mesh.make_node(
                kind="episodic",
                body=f"body {i}",
                headline=f"node-{i}",
                scope_id="s",
                source_session_id="ses",
                source_repo="r",
            )
            stamped = dataclasses.replace(node, created_at=1_000 + i, updated_at=1_000 + i)
            mesh.add(stamped)
    finally:
        mesh.close()

    result = runner.invoke(app, ["list", "--limit", "3", "--db", str(db_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    returned_headlines = [n["headline"] for n in payload]

    assert returned_headlines == ["node-5", "node-4", "node-3"]
