"""End-to-end CLI tests run as real subprocesses against `python -m context_mesh.cli`."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from context_mesh import __version__

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

CliRun = Callable[..., "subprocess.CompletedProcess[str]"]

pytestmark = pytest.mark.system


def test_version_flag(cli_runner: CliRun) -> None:
    result = cli_runner(["--version"])
    assert __version__ in result.stdout


def test_no_args_shows_help(cli_runner: CliRun) -> None:
    result = cli_runner([], expect_success=False)
    assert result.returncode in (0, 2)
    combined = result.stdout + result.stderr
    assert "Usage:" in combined
    assert "init" in combined


def test_init_happy_path(cli_runner: CliRun, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    result = cli_runner(["init", str(project)])
    assert "context-mesh initialized" in result.stdout
    assert (project / ".context-mesh" / "memory.db").exists()


def test_search_returns_seeded_node(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(
        [
            "search",
            "auth requires JWT verification on every request",
            "--db",
            str(mesh_db),
        ],
    )
    assert "JWT auth requirement" in result.stdout


def test_search_json_shape(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(
        [
            "search",
            "auth requires JWT verification on every request",
            "--db",
            str(mesh_db),
            "--json",
        ],
    )
    payload = json.loads(result.stdout)
    assert "nodes" in payload
    assert "edges" in payload
    assert "cluster_confidence" in payload


def test_show_prints_fields(cli_runner: CliRun, mesh_db: Path) -> None:
    list_result = cli_runner(["list", "--db", str(mesh_db), "--json"])
    nodes = json.loads(list_result.stdout)
    node_id = nodes[0]["id"]

    result = cli_runner(["show", node_id, "--db", str(mesh_db)])
    assert node_id in result.stdout
    assert "kind:" in result.stdout
    assert "body:" in result.stdout


def test_show_unknown_id_exits_1(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(
        ["show", "ghost-id", "--db", str(mesh_db)],
        expect_success=False,
    )
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_list_filter_by_kind(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(["list", "--kind", "semantic", "--db", str(mesh_db)])
    assert "[semantic]" in result.stdout
    assert "[episodic]" not in result.stdout


def test_list_newest_first(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(["list", "--db", str(mesh_db), "--json"])
    nodes = json.loads(result.stdout)
    timestamps = [n["created_at"] for n in nodes]
    assert timestamps == sorted(timestamps, reverse=True)


def test_add_show_round_trip(cli_runner: CliRun, tmp_path: Path) -> None:
    project = tmp_path / "rt"
    project.mkdir()
    cli_runner(["init", str(project)])
    db_path = project / ".context-mesh" / "memory.db"

    add_result = cli_runner(
        [
            "add",
            "round trip body",
            "--kind",
            "semantic",
            "--db",
            str(db_path),
        ],
    )
    node_id = add_result.stdout.strip().split()[-1]
    show_result = cli_runner(["show", node_id, "--db", str(db_path)])
    assert "round trip body" in show_result.stdout


def test_delete_yes(cli_runner: CliRun, mesh_db: Path) -> None:
    list_result = cli_runner(["list", "--db", str(mesh_db), "--json"])
    nodes = json.loads(list_result.stdout)
    node_id = nodes[0]["id"]

    result = cli_runner(["delete", node_id, "--yes", "--db", str(mesh_db)])
    assert f"deleted {node_id}" in result.stdout

    after = cli_runner(["show", node_id, "--db", str(mesh_db)], expect_success=False)
    assert after.returncode == 1


def test_stats_baseline_shape(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(["stats", "--db", str(mesh_db)])
    assert "Nodes:" in result.stdout
    assert "Edges:" in result.stdout
    assert "Vectors:" in result.stdout
    assert "Last audit event:" in result.stdout


def test_audit_shows_prior_events(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(["audit", "--db", str(mesh_db)])
    assert "[add]" in result.stdout


def test_audit_filter_by_event_type(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(["audit", "--event-type", "add", "--db", str(mesh_db), "--json"])
    payload = json.loads(result.stdout)
    assert payload
    for row in payload:
        assert row["event_type"] == "add"


def test_db_resolution_via_env_var(cli_runner: CliRun, mesh_db: Path) -> None:
    result = cli_runner(
        ["stats"],
        env={"CONTEXT_MESH_DB": str(mesh_db)},
    )
    assert "Nodes: 3" in result.stdout


def test_db_resolution_via_project_local_cwd(
    cli_runner: CliRun, mesh_db: Path, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    (project / ".context-mesh").mkdir(parents=True)
    shutil.copy(mesh_db, project / ".context-mesh" / "memory.db")

    result = cli_runner(["stats"], cwd=project)
    assert "Nodes: 3" in result.stdout


def test_missing_db_error(cli_runner: CliRun, tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    result = cli_runner(["stats"], cwd=fresh, expect_success=False)
    assert result.returncode != 0
    assert "context-mesh init" in result.stderr


def test_serve_command_help(cli_runner: CliRun) -> None:
    result = cli_runner(["serve", "--help"])
    combined = result.stdout + result.stderr
    assert "--host" in combined
    assert "--port" in combined
    assert "--db" in combined


def test_config_command_shows_defaults(cli_runner: CliRun, tmp_path: Path) -> None:
    fresh = tmp_path / "fresh-cfg"
    fresh.mkdir()
    result = cli_runner(["config"], cwd=fresh)
    assert "[storage]" in result.stdout
    assert 'path = ".context-mesh/memory.db"' in result.stdout
    assert "[embeddings]" in result.stdout
    assert "[adapters]" in result.stdout


def test_config_get_storage_path(cli_runner: CliRun, tmp_path: Path) -> None:
    fresh = tmp_path / "fresh-cfg-get"
    fresh.mkdir()
    result = cli_runner(["config", "get", "storage.path"], cwd=fresh)
    assert result.stdout.strip() == ".context-mesh/memory.db"


def test_tools_command_dumps_anthropic(cli_runner: CliRun) -> None:
    result = cli_runner(["tools"])
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 5
    names = {entry["name"] for entry in payload}
    assert names == {
        "search_team_memory",
        "drill_down_memory",
        "add_memory",
        "mark_memory_used",
        "find_contradictions",
    }
    for entry in payload:
        assert "input_schema" in entry
