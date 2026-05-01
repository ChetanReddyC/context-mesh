"""Tests for the `context-mesh sync` CLI command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


_AGENT_MEMORY_MD = """\
---
session_id: cli-sync-1
agent: claude-code
started_at: 1700000000
repo: demo-repo
---
Decision: We will use bearer tokens.
"""


def _write_md(repo: Path) -> None:
    memdir = repo / ".agent-memory"
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / "s1.md").write_text(_AGENT_MEMORY_MD, encoding="utf-8")


def test_sync_unknown_adapter_errors(empty_db: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        [
            "sync",
            "bogus",
            "--repo",
            str(repo),
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 1
    assert "unknown adapter" in result.stderr


def test_sync_missing_repo_errors(empty_db: Path, tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"
    result = runner.invoke(
        app,
        [
            "sync",
            "agent-memory",
            "--repo",
            str(missing),
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 1
    assert "--repo path not found" in result.stderr


def test_sync_unknown_distiller_errors(empty_db: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_md(repo)
    result = runner.invoke(
        app,
        [
            "sync",
            "agent-memory",
            "--repo",
            str(repo),
            "--distiller",
            "bogus",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 1
    assert "unknown distiller" in result.stderr


def test_sync_dry_run_text_output_shape(empty_db: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_md(repo)

    result = runner.invoke(
        app,
        [
            "sync",
            "agent-memory",
            "--repo",
            str(repo),
            "--dry-run",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(dry-run)" in result.stdout
    assert "would ingest:" in result.stdout
    assert "cli-sync-1" in result.stdout


def test_sync_json_shape(empty_db: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_md(repo)

    result = runner.invoke(
        app,
        [
            "sync",
            "agent-memory",
            "--repo",
            str(repo),
            "--json",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["adapter_name"] == "agent-memory"
    assert payload["discovered"] == 1
    assert payload["fetched"] == 1
    assert payload["memory_nodes_added"] >= 1
    assert payload["dry_run"] is False
    assert isinstance(payload["skipped"], list)
    assert "cursor" in payload
    assert "elapsed_ms" in payload


def test_sync_happy_path_agent_memory(empty_db: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_md(repo)

    result = runner.invoke(
        app,
        [
            "sync",
            "agent-memory",
            "--repo",
            str(repo),
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "adapter: agent-memory" in result.stdout
    assert "discovered:" in result.stdout
    assert "memory_nodes_added:" in result.stdout
