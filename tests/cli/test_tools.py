"""Tests for the `context-mesh tools` CLI command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


EXPECTED_NAMES = {
    "search_team_memory",
    "drill_down_memory",
    "add_memory",
    "mark_memory_used",
    "find_contradictions",
}


def test_tools_default_dialect_anthropic() -> None:
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 5
    names = {entry["name"] for entry in payload}
    assert names == EXPECTED_NAMES
    assert all("input_schema" in entry for entry in payload)


def test_tools_dialect_openai() -> None:
    result = runner.invoke(app, ["tools", "--dialect", "openai"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload) == 5
    for entry in payload:
        assert entry["type"] == "function"
        assert "name" in entry["function"]
        assert "parameters" in entry["function"]


def test_tools_dialect_mcp() -> None:
    result = runner.invoke(app, ["tools", "--dialect", "mcp"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload) == 5
    for entry in payload:
        assert "inputSchema" in entry
        assert "input_schema" not in entry


def test_tools_unknown_dialect_exits_1() -> None:
    result = runner.invoke(app, ["tools", "--dialect", "bogus"])
    assert result.exit_code == 1
    assert "unknown dialect" in result.stderr


def test_tools_writes_file_with_out_flag(tmp_path: Path) -> None:
    out = tmp_path / "tools.json"
    result = runner.invoke(app, ["tools", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "wrote 5 tool schema(s)" in result.output
    assert str(out) in result.output

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 5
    names = {entry["name"] for entry in payload}
    assert names == EXPECTED_NAMES


def test_tools_out_flag_with_openai_dialect(tmp_path: Path) -> None:
    out = tmp_path / "openai-tools.json"
    result = runner.invoke(app, ["tools", "--dialect", "openai", "--out", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert all(entry["type"] == "function" for entry in payload)
