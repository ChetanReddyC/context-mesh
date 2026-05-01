"""Tests for the `context-mesh show` CLI command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.api import Mesh
from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _first_node_id(db: Path) -> str:
    mesh = Mesh.local(db)
    try:
        nodes = mesh.list_nodes(limit=1)
    finally:
        mesh.close()
    assert nodes
    return nodes[0].id


def test_show_prints_fields(seeded_db: Path) -> None:
    node_id = _first_node_id(seeded_db)
    result = runner.invoke(app, ["show", node_id, "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert f"id:          {node_id}" in result.output
    assert "kind:" in result.output
    assert "headline:" in result.output
    assert "body:" in result.output


def test_show_lists_edges(seeded_db: Path) -> None:
    mesh = Mesh.local(seeded_db)
    try:
        nodes = mesh.list_nodes()
    finally:
        mesh.close()
    semantic_node = next(n for n in nodes if n.kind == "semantic")

    result = runner.invoke(app, ["show", semantic_node.id, "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert "out_edges (1)" in result.output
    assert "generalizes" in result.output


def test_show_json(seeded_db: Path) -> None:
    node_id = _first_node_id(seeded_db)
    result = runner.invoke(app, ["show", node_id, "--db", str(seeded_db), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["node"]["id"] == node_id
    assert "out_edges" in payload
    assert "in_edges" in payload


def test_show_unknown_id_exits_1(seeded_db: Path) -> None:
    result = runner.invoke(app, ["show", "does-not-exist", "--db", str(seeded_db)])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_show_missing_db_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    result = runner.invoke(app, ["show", "any", "--db", str(missing)])
    assert result.exit_code == 1
    assert "database not found" in result.stderr
