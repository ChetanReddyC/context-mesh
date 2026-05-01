"""Tests for the `context-mesh config` CLI subcommand."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CONTEXT_MESH_DB", raising=False)
    monkeypatch.delenv("CONTEXT_MESH_LOG_LEVEL", raising=False)
    monkeypatch.delenv("CONTEXT_MESH_HUB_URL", raising=False)
    from context_mesh.config import loader as loader_module

    monkeypatch.setattr(
        loader_module,
        "DEFAULT_GLOBAL_CONFIG_PATH",
        tmp_path / ".context-mesh" / "config.toml",
    )
    monkeypatch.chdir(tmp_path)


def test_config_prints_valid_toml() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stderr
    parsed = tomllib.loads(result.stdout)
    assert parsed["storage"]["path"] == ".context-mesh/memory.db"
    assert parsed["embeddings"]["dimensions"] == 384
    for section in (
        "storage",
        "embeddings",
        "distillation",
        "retrieval",
        "sync",
        "scopes",
        "observability",
        "adapters",
    ):
        assert section in parsed


def test_config_get_storage_path() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "get", "storage.path"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == ".context-mesh/memory.db"


def test_config_get_int_value() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "get", "embeddings.dimensions"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "384"


def test_config_get_bool_value() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "get", "adapters.entire_enabled"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "false"


def test_config_get_unknown_key_exits_1() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "get", "nope.missing"])
    assert result.exit_code == 1
    assert "unknown" in result.stderr


def test_config_get_bad_format_exits_1() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "get", "no_dot"])
    assert result.exit_code == 1
    assert "section.field" in result.stderr


def test_config_sources_empty_when_no_files(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "sources"])
    assert result.exit_code == 0, result.stderr
    assert "defaults only" in result.stdout


def test_config_sources_lists_project_file(tmp_path: Path) -> None:
    project_cfg = tmp_path / ".context-mesh" / "config.toml"
    project_cfg.parent.mkdir(parents=True, exist_ok=True)
    project_cfg.write_text('[storage]\npath = "x.db"\n', encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["config", "sources"])
    assert result.exit_code == 0, result.stderr
    assert str(project_cfg) in result.stdout
