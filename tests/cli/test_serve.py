"""Tests for the `context-mesh serve` CLI command."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import patch

from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def test_serve_help_lists_options() -> None:
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0, result.output
    plain = _ANSI_RE.sub("", result.output)
    assert "--host" in plain
    assert "--port" in plain
    assert "--db" in plain


def test_serve_missing_db_exits_1(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    result = runner.invoke(
        app,
        ["serve", "--port", "0", "--db", str(missing)],
    )
    assert result.exit_code == 1
    assert "database not found" in result.stderr


def test_serve_constructs_server_and_starts(seeded_db: Path) -> None:
    """The command parses and constructs a server with the right db_path; we mock start."""
    with patch("context_mesh.cli.MeshServer") as mock_cls:
        instance = mock_cls.return_value
        instance.url = "http://127.0.0.1:0"
        instance.start.return_value = None
        result = runner.invoke(
            app,
            ["serve", "--port", "0", "--db", str(seeded_db)],
        )
    assert result.exit_code == 0, result.output
    mock_cls.assert_called_once()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["db_path"] == seeded_db
    assert kwargs["port"] == 0
    instance.start.assert_called_once()


def test_serve_keyboard_interrupt_calls_stop(seeded_db: Path) -> None:
    with patch("context_mesh.cli.MeshServer") as mock_cls:
        instance = mock_cls.return_value
        instance.url = "http://127.0.0.1:0"
        instance.start.side_effect = KeyboardInterrupt()
        result = runner.invoke(
            app,
            ["serve", "--port", "0", "--db", str(seeded_db)],
        )
    assert result.exit_code == 0, result.output
    instance.stop.assert_called_once()
