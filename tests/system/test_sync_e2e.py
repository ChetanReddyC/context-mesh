"""End-to-end tests for `context-mesh sync` against the real subprocess CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

CliRun = Callable[..., "subprocess.CompletedProcess[str]"]

pytestmark = pytest.mark.system


_AGENT_MEMORY_MD = """\
---
session_id: e2e-sync-1
agent: claude-code
started_at: 1700000000
repo: demo-repo
---
Decision: We will use bearer tokens for authentication.
"""


def _init_db(cli_runner: CliRun, project: Path) -> Path:
    cli_runner(["init", str(project)])
    return project / ".context-mesh" / "memory.db"


def test_sync_e2e_agent_memory_idempotent(cli_runner: CliRun, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    db_path = _init_db(cli_runner, project)

    memdir = project / ".agent-memory"
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / "s1.md").write_text(_AGENT_MEMORY_MD, encoding="utf-8")

    first = cli_runner(
        [
            "sync",
            "agent-memory",
            "--repo",
            str(project),
            "--json",
            "--db",
            str(db_path),
        ],
    )
    payload = json.loads(first.stdout)
    assert payload["adapter_name"] == "agent-memory"
    assert payload["discovered"] == 1
    assert payload["fetched"] == 1
    assert payload["memory_nodes_added"] >= 1

    second = cli_runner(
        [
            "sync",
            "agent-memory",
            "--repo",
            str(project),
            "--json",
            "--db",
            str(db_path),
        ],
    )
    payload2 = json.loads(second.stdout)
    assert payload2["discovered"] == 0
    assert payload2["fetched"] == 0
    assert payload2["memory_nodes_added"] == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_sync_e2e_entire_idempotent(cli_runner: CliRun, tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=project, check=True)
    (project / "x").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=project, check=True)

    payload: dict[str, Any] = {
        "session_id": "ckpt-e2e-1",
        "agent": "claude-code",
        "started_at": 1_700_000_000,
        "transcript": "Decision: We will use OpenTelemetry for tracing.",
    }
    _add_checkpoint_commit(project, "entire/checkpoints/v1", payload)

    db_path = _init_db(cli_runner, project)

    first = cli_runner(
        [
            "sync",
            "entire",
            "--repo",
            str(project),
            "--branch",
            "entire/checkpoints/v1",
            "--json",
            "--db",
            str(db_path),
        ],
    )
    p1 = json.loads(first.stdout)
    assert p1["adapter_name"] == "entire"
    assert p1["discovered"] >= 1
    assert p1["fetched"] >= 1
    assert p1["memory_nodes_added"] >= 1

    second = cli_runner(
        [
            "sync",
            "entire",
            "--repo",
            str(project),
            "--branch",
            "entire/checkpoints/v1",
            "--json",
            "--db",
            str(db_path),
        ],
    )
    p2 = json.loads(second.stdout)
    assert p2["discovered"] == 0
    assert p2["fetched"] == 0
    assert p2["memory_nodes_added"] == 0


def _empty_tree_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "write-tree"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={"GIT_INDEX_FILE": str(repo / ".git" / "empty-index")},
    )
    return result.stdout.decode("utf-8").strip()


def _add_checkpoint_commit(
    repo: Path,
    branch: str,
    payload: Any,
    *,
    message: str | None = None,
) -> str:
    tree_sha = _empty_tree_sha(repo)

    parents: list[str] = []
    parent_lookup = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if parent_lookup.returncode == 0:
        parents.append(parent_lookup.stdout.decode("utf-8").strip())

    if message is None:
        message = json.dumps(payload) if not isinstance(payload, str) else payload

    cmd = ["git", "commit-tree", tree_sha]
    for parent in parents:
        cmd.extend(["-p", parent])
    cmd.extend(["-m", message])
    commit = subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    sha = commit.stdout.decode("utf-8").strip()

    subprocess.run(
        ["git", "update-ref", f"refs/heads/{branch}", sha],
        cwd=repo,
        check=True,
    )
    return sha
