"""Tests for the `context-mesh add` CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.api import Mesh
from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_add_creates_node_and_emits_id(empty_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "add",
            "JWT verification is required on every request",
            "--kind",
            "semantic",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "node id:" in result.output
    node_id = result.output.strip().split()[-1]

    mesh = Mesh.local(empty_db)
    try:
        node = mesh.get(node_id)
    finally:
        mesh.close()
    assert node is not None
    assert node.kind == "semantic"
    assert node.body.startswith("JWT verification")


def test_add_derives_headline_from_first_line(empty_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "add",
            "first line title\nthen body details",
            "--kind",
            "semantic",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    node_id = result.output.strip().split()[-1]

    mesh = Mesh.local(empty_db)
    try:
        node = mesh.get(node_id)
    finally:
        mesh.close()
    assert node is not None
    assert node.headline == "first line title"


def test_add_explicit_headline_used(empty_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "add",
            "body content",
            "--kind",
            "episodic",
            "--headline",
            "explicit headline",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    node_id = result.output.strip().split()[-1]

    mesh = Mesh.local(empty_db)
    try:
        node = mesh.get(node_id)
    finally:
        mesh.close()
    assert node is not None
    assert node.headline == "explicit headline"


def test_add_parses_tags(empty_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "add",
            "tagged content",
            "--kind",
            "procedural",
            "--tags",
            "auth, deploy , webhook",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    node_id = result.output.strip().split()[-1]

    mesh = Mesh.local(empty_db)
    try:
        node = mesh.get(node_id)
    finally:
        mesh.close()
    assert node is not None
    assert node.tags == ["auth", "deploy", "webhook"]


def test_add_rejects_empty_content(empty_db: Path) -> None:
    result = runner.invoke(
        app,
        ["add", "   ", "--kind", "semantic", "--db", str(empty_db)],
    )
    assert result.exit_code == 1
    assert "content must not be empty" in result.stderr


def test_add_rejects_invalid_kind(empty_db: Path) -> None:
    result = runner.invoke(
        app,
        ["add", "content", "--kind", "wrong", "--db", str(empty_db)],
    )
    assert result.exit_code == 1
    assert "invalid kind" in result.stderr


def test_add_missing_db_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    result = runner.invoke(
        app,
        ["add", "content", "--kind", "semantic", "--db", str(missing)],
    )
    assert result.exit_code == 1
    assert "database not found" in result.stderr
