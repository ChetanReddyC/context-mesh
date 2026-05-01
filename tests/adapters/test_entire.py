"""Tests for `EntireAdapter` — discover/fetch over a git checkpoint branch."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
import structlog

from context_mesh.adapters import EntireAdapter, SyncState
from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available on PATH")


_BRANCH = "entire/checkpoints/v1"


@pytest.fixture()
def mesh() -> Iterator[Mesh]:
    m = Mesh.local(":memory:")
    try:
        yield m
    finally:
        m.close()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "x").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


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
    """Append a commit on `branch` with the given payload as commit message body.

    Uses plumbing (`write-tree`, `commit-tree`, `update-ref`) — no working-tree
    checkout, so the test repo's `main` branch is preserved unaltered.
    """
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


def _full_payload(session_id: str, started_at: int = 1_700_000_000) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "agent": "claude-code",
        "started_at": started_at,
        "ended_at": started_at + 60,
        "repo": "demo-repo",
        "branch": "feat/x",
        "commit_sha": "abc123",
        "turn_count": 7,
        "token_usage": 12345,
        "file_changes": ["a.py", "b.py", "c.py"],
        "transcript": "## Decision\n\nUse advisory locks for migrations.",
    }


# =============================================================================
# A. Construction
# =============================================================================


def test_init_rejects_missing_repo_path(mesh: Mesh, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError):
        EntireAdapter(mesh, missing)


def test_init_rejects_file_as_repo_path(mesh: Mesh, tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        EntireAdapter(mesh, file_path)


def test_init_rejects_non_git_directory(
    mesh: Mesh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    # Stop git from walking up the filesystem and discovering an enclosing repo
    # (e.g. a developer's home dir initialized as a dotfiles repo).
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(ValueError):
        EntireAdapter(mesh, plain)


def test_init_rejects_blank_name(mesh: Mesh, git_repo: Path) -> None:
    with pytest.raises(ValueError):
        EntireAdapter(mesh, git_repo, name="   ")


def test_init_rejects_missing_git_binary(mesh: Mesh, git_repo: Path) -> None:
    with pytest.raises(RuntimeError):
        EntireAdapter(mesh, git_repo, git_binary="git-this-does-not-exist-xyz")


# =============================================================================
# B. Branch absence / empty
# =============================================================================


def test_discover_returns_empty_when_branch_missing_with_warning(
    mesh: Mesh, git_repo: Path
) -> None:
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        first = adapter.discover()
    assert first == []
    assert any(r.get("event") == "entire_branch_missing" for r in logs)

    with structlog.testing.capture_logs() as logs2:
        second = adapter.discover()
    assert second == []
    assert not any(r.get("event") == "entire_branch_missing" for r in logs2)


def test_discover_returns_empty_when_branch_exists_but_no_commits_match(
    mesh: Mesh, git_repo: Path
) -> None:
    """Branch exists but only has commits that all fail to parse — returns []."""
    _add_checkpoint_commit(git_repo, _BRANCH, "not-json-at-all", message="not json")
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    assert any(r.get("event") == "malformed_checkpoint" for r in logs)


def test_branch_missing_warning_resets_when_branch_appears(mesh: Mesh, git_repo: Path) -> None:
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        adapter.discover()
    assert any(r.get("event") == "entire_branch_missing" for r in logs)

    _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("s1"))
    refs = adapter.discover()
    assert len(refs) == 1
    assert refs[0].source_id == "s1"


# =============================================================================
# C. Happy path
# =============================================================================


def test_discover_and_fetch_round_trip(mesh: Mesh, git_repo: Path) -> None:
    payload = _full_payload("sess-1", started_at=1_700_000_000)
    sha = _add_checkpoint_commit(git_repo, _BRANCH, payload)

    adapter = EntireAdapter(mesh, git_repo)
    refs = adapter.discover()
    assert len(refs) == 1
    ref = refs[0]
    assert ref.adapter_name == "entire"
    assert ref.source_id == "sess-1"
    assert ref.repo == "demo-repo"
    assert ref.agent == "claude-code"
    assert ref.started_at == 1_700_000_000
    assert ref.branch == "feat/x"
    assert ref.commit_sha == "abc123"
    assert ref.transcript_uri is None

    md = dict(ref.metadata)
    assert md["checkpoint_sha"] == sha
    assert md["ended_at"] == str(1_700_000_060)
    assert md["turn_count"] == "7"
    assert md["token_usage"] == "12345"
    assert md["file_change_count"] == "3"

    transcript = adapter.fetch(ref)
    assert transcript.reference is ref
    assert transcript.text == payload["transcript"]
    assert transcript.ended_at == 1_700_000_060
    assert transcript.turn_count == 7
    assert transcript.token_usage == 12345


def test_discover_preserves_reverse_order(mesh: Mesh, git_repo: Path) -> None:
    sha_a = _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("a", started_at=300))
    sha_b = _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("b", started_at=100))
    sha_c = _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("c", started_at=200))

    adapter = EntireAdapter(mesh, git_repo)
    refs = adapter.discover()
    assert [r.source_id for r in refs] == ["a", "b", "c"]
    metas = [dict(r.metadata)["checkpoint_sha"] for r in refs]
    assert metas == [sha_a, sha_b, sha_c]


def test_repo_defaults_to_repo_path_name_when_unset(mesh: Mesh, git_repo: Path) -> None:
    payload = {
        "session_id": "x",
        "agent": "claude-code",
        "started_at": 1,
        "transcript": "body",
    }
    _add_checkpoint_commit(git_repo, _BRANCH, payload)
    adapter = EntireAdapter(mesh, git_repo)
    refs = adapter.discover()
    assert refs[0].repo == "repo"


# =============================================================================
# D. Idempotency / cursor
# =============================================================================


def test_second_discover_returns_empty_after_state_push(mesh: Mesh, git_repo: Path) -> None:
    shas = [
        _add_checkpoint_commit(git_repo, _BRANCH, _full_payload(f"s{i}", started_at=100 + i))
        for i in range(3)
    ]

    adapter = EntireAdapter(mesh, git_repo)
    first = adapter.discover()
    assert len(first) == 3

    adapter.set_sync_state(
        SyncState(
            adapter_name="entire",
            cursor=shas[-1],
            last_synced_at=None,
            state={"seen_shas": shas},
        )
    )

    second = adapter.discover()
    assert second == []


def test_discover_returns_only_new_after_partial_state_push(mesh: Mesh, git_repo: Path) -> None:
    shas = [
        _add_checkpoint_commit(git_repo, _BRANCH, _full_payload(f"s{i}", started_at=100 + i))
        for i in range(3)
    ]

    adapter = EntireAdapter(mesh, git_repo)
    first = adapter.discover()
    assert [r.source_id for r in first] == ["s0", "s1", "s2"]

    adapter.set_sync_state(
        SyncState(
            adapter_name="entire",
            cursor=shas[2],
            last_synced_at=None,
            state={"seen_shas": shas},
        )
    )

    _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("s3", started_at=400))

    second = adapter.discover()
    assert [r.source_id for r in second] == ["s3"]


def test_unreachable_cursor_falls_back_to_full_walk(mesh: Mesh, git_repo: Path) -> None:
    sha1 = _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("s1", started_at=100))

    adapter = EntireAdapter(mesh, git_repo)
    adapter.set_sync_state(
        SyncState(
            adapter_name="entire",
            cursor=sha1,
            last_synced_at=None,
            state={"seen_shas": [sha1]},
        )
    )

    # Force-rewrite the branch — cursor sha1 becomes unreachable.
    new_sha_a = _add_checkpoint_commit(git_repo, "tmp/new", _full_payload("n1", started_at=200))
    new_sha_b = _add_checkpoint_commit(git_repo, "tmp/new", _full_payload("n2", started_at=300))
    subprocess.run(
        ["git", "update-ref", "--no-deref", f"refs/heads/{_BRANCH}", new_sha_b],
        cwd=git_repo,
        check=True,
    )

    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()

    assert any(r.get("event") == "entire_cursor_unreachable" for r in logs)
    assert [r.source_id for r in refs] == ["n1", "n2"]
    metas = [dict(r.metadata)["checkpoint_sha"] for r in refs]
    assert metas == [new_sha_a, new_sha_b]


# =============================================================================
# E. Malformed payloads
# =============================================================================


def test_malformed_non_json_skipped(mesh: Mesh, git_repo: Path) -> None:
    _add_checkpoint_commit(git_repo, _BRANCH, "this is not json", message="just text")
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    matches = [r for r in logs if r.get("event") == "malformed_checkpoint"]
    assert matches
    assert "json" in matches[0].get("reason", "")


def test_malformed_top_level_not_object_skipped(mesh: Mesh, git_repo: Path) -> None:
    _add_checkpoint_commit(git_repo, _BRANCH, [1, 2, 3])
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    matches = [r for r in logs if r.get("event") == "malformed_checkpoint"]
    assert matches
    assert "object" in matches[0].get("reason", "")


def test_malformed_missing_required_keys_skipped(mesh: Mesh, git_repo: Path) -> None:
    _add_checkpoint_commit(
        git_repo,
        _BRANCH,
        {"agent": "claude-code", "started_at": 1, "transcript": "hi"},
    )
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    matches = [r for r in logs if r.get("event") == "malformed_checkpoint"]
    assert matches
    assert "session_id" in matches[0].get("reason", "")


def test_empty_commit_message_skipped(mesh: Mesh, git_repo: Path) -> None:
    _add_checkpoint_commit(git_repo, _BRANCH, "ignored", message="   ")
    adapter = EntireAdapter(mesh, git_repo)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    matches = [r for r in logs if r.get("event") == "malformed_checkpoint"]
    assert matches
    assert "empty body" in matches[0].get("reason", "")


# =============================================================================
# F. Configuration
# =============================================================================


def test_custom_branch_used_for_discovery(mesh: Mesh, git_repo: Path) -> None:
    custom = "entire/v2"
    _add_checkpoint_commit(git_repo, custom, _full_payload("s1"))
    adapter = EntireAdapter(mesh, git_repo, branch=custom)
    refs = adapter.discover()
    assert len(refs) == 1
    assert refs[0].source_id == "s1"


def test_custom_name_used_for_sync_state(mesh: Mesh, git_repo: Path) -> None:
    sha = _add_checkpoint_commit(git_repo, _BRANCH, _full_payload("s1"))
    adapter = EntireAdapter(mesh, git_repo, name="entire-prod")
    refs = adapter.discover()
    assert refs[0].adapter_name == "entire-prod"

    adapter.set_sync_state(
        SyncState(
            adapter_name="entire-prod",
            cursor=sha,
            last_synced_at=None,
            state={"seen_shas": [sha]},
        )
    )
    persisted = mesh.get_sync_state("entire-prod")
    assert persisted is not None
    assert persisted.cursor == sha


# =============================================================================
# G. Boundary
# =============================================================================


def test_set_sync_state_rejects_mismatched_adapter_name(mesh: Mesh, git_repo: Path) -> None:
    adapter = EntireAdapter(mesh, git_repo)
    with pytest.raises(ValueError):
        adapter.set_sync_state(
            SyncState(
                adapter_name="other",
                cursor="c",
                last_synced_at=None,
                state={"seen_shas": []},
            )
        )


def test_fetch_rejects_reference_without_checkpoint_sha(mesh: Mesh, git_repo: Path) -> None:
    from context_mesh.adapters import SessionReference

    adapter = EntireAdapter(mesh, git_repo)
    bad_ref = SessionReference(
        adapter_name="entire",
        source_id="x",
        repo="r",
        agent="a",
        started_at=1,
        metadata=(),
    )
    with pytest.raises(ValueError):
        adapter.fetch(bad_ref)
