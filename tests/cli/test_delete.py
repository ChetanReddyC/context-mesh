"""Tests for the `context-mesh delete` CLI command."""

from __future__ import annotations

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


def test_delete_with_yes_flag(seeded_db: Path) -> None:
    node_id = _first_node_id(seeded_db)
    result = runner.invoke(app, ["delete", node_id, "--yes", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output
    assert f"deleted {node_id}" in result.output

    mesh = Mesh.local(seeded_db)
    try:
        gone = mesh.get(node_id)
    finally:
        mesh.close()
    assert gone is None


def test_delete_short_flag(seeded_db: Path) -> None:
    node_id = _first_node_id(seeded_db)
    result = runner.invoke(app, ["delete", node_id, "-y", "--db", str(seeded_db)])
    assert result.exit_code == 0, result.output


def test_delete_unknown_id(seeded_db: Path) -> None:
    result = runner.invoke(app, ["delete", "no-such-id", "--yes", "--db", str(seeded_db)])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_delete_aborts_without_confirmation(seeded_db: Path) -> None:
    node_id = _first_node_id(seeded_db)
    result = runner.invoke(app, ["delete", node_id, "--db", str(seeded_db)], input="n\n")
    assert result.exit_code != 0

    mesh = Mesh.local(seeded_db)
    try:
        survives = mesh.get(node_id)
    finally:
        mesh.close()
    assert survives is not None


def test_delete_proceeds_with_y_input(seeded_db: Path) -> None:
    node_id = _first_node_id(seeded_db)
    result = runner.invoke(app, ["delete", node_id, "--db", str(seeded_db)], input="y\n")
    assert result.exit_code == 0, result.output

    mesh = Mesh.local(seeded_db)
    try:
        gone = mesh.get(node_id)
    finally:
        mesh.close()
    assert gone is None


def test_delete_missing_db_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    result = runner.invoke(app, ["delete", "x", "--yes", "--db", str(missing)])
    assert result.exit_code == 1
    assert "database not found" in result.stderr
