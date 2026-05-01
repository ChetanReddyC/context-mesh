"""Tests for `AgentMemoryAdapter` — discover/fetch over `.agent-memory/`."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import structlog

from context_mesh.adapters import AgentMemoryAdapter, SyncState
from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture()
def mesh() -> Iterator[Mesh]:
    m = Mesh.local(":memory:")
    try:
        yield m
    finally:
        m.close()


def _write_md(path: Path, frontmatter: dict[str, str], body: str) -> None:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _full_frontmatter(session_id: str, started_at: int = 1_700_000_000) -> dict[str, str]:
    return {
        "session_id": session_id,
        "agent": "claude-code",
        "started_at": str(started_at),
        "repo": "demo-repo",
        "branch": "main",
        "commit_sha": "abc123",
        "ended_at": str(started_at + 60),
        "turn_count": "7",
        "token_usage": "12345",
        "tags": "bugfix, infra, perf",
    }


# =============================================================================
# A. Construction
# =============================================================================


def test_init_rejects_missing_repo_path(mesh: Mesh, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError):
        AgentMemoryAdapter(mesh, missing)


def test_init_rejects_file_as_repo_path(mesh: Mesh, tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        AgentMemoryAdapter(mesh, file_path)


def test_init_accepts_custom_name_and_exposes_via_property(mesh: Mesh, tmp_path: Path) -> None:
    adapter = AgentMemoryAdapter(mesh, tmp_path, name="custom-am")
    assert adapter.name == "custom-am"


def test_init_rejects_blank_name(mesh: Mesh, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AgentMemoryAdapter(mesh, tmp_path, name="   ")


# =============================================================================
# B. Empty / missing directory behavior
# =============================================================================


def test_discover_returns_empty_when_agent_memory_dir_absent(mesh: Mesh, tmp_path: Path) -> None:
    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with structlog.testing.capture_logs() as logs:
        first = adapter.discover()
    assert first == []
    assert any(r.get("event") == "agent_memory_dir_missing" for r in logs)

    with structlog.testing.capture_logs() as logs2:
        second = adapter.discover()
    assert second == []
    assert not any(r.get("event") == "agent_memory_dir_missing" for r in logs2)


def test_discover_returns_empty_for_empty_agent_memory_dir(mesh: Mesh, tmp_path: Path) -> None:
    (tmp_path / ".agent-memory").mkdir()
    adapter = AgentMemoryAdapter(mesh, tmp_path)
    assert adapter.discover() == []


# =============================================================================
# C. Happy path
# =============================================================================


def test_discover_and_fetch_round_trip(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    fm = _full_frontmatter("sess-1", started_at=1_700_000_000)
    body = "Decision: switched migration runner to advisory locks.\n\nRationale: ..."
    _write_md(mem / "session-1.md", fm, body)

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    refs = adapter.discover()
    assert len(refs) == 1
    ref = refs[0]
    assert ref.adapter_name == "agent-memory"
    assert ref.source_id == "sess-1"
    assert ref.repo == "demo-repo"
    assert ref.agent == "claude-code"
    assert ref.started_at == 1_700_000_000
    assert ref.branch == "main"
    assert ref.commit_sha == "abc123"
    assert ref.transcript_uri == str((mem / "session-1.md").resolve())

    md = dict(ref.metadata)
    assert md["ended_at"] == str(1_700_000_060)
    assert md["turn_count"] == "7"
    assert md["token_usage"] == "12345"
    assert "content_sha256" in md
    tag_values = sorted(v for k, v in ref.metadata if k == "tag")
    assert tag_values == ["bugfix", "infra", "perf"]

    transcript = adapter.fetch(ref)
    assert transcript.reference is ref
    assert transcript.text == body
    assert transcript.ended_at == 1_700_000_060
    assert transcript.turn_count == 7
    assert transcript.token_usage == 12345


def test_discover_recursive_walk(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    _write_md(
        mem / "a.md",
        {"session_id": "a", "agent": "claude-code", "started_at": "300"},
        "alpha",
    )
    _write_md(
        mem / "sub" / "b.md",
        {"session_id": "b", "agent": "claude-code", "started_at": "100"},
        "beta",
    )
    _write_md(
        mem / "sub" / "deeper" / "c.md",
        {"session_id": "c", "agent": "claude-code", "started_at": "200"},
        "gamma",
    )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    refs = adapter.discover()
    assert [r.source_id for r in refs] == ["b", "c", "a"]


def test_repo_defaults_to_repo_path_name_when_unset(mesh: Mesh, tmp_path: Path) -> None:
    repo_root = tmp_path / "my-repo"
    (repo_root / ".agent-memory").mkdir(parents=True)
    _write_md(
        repo_root / ".agent-memory" / "x.md",
        {"session_id": "x", "agent": "claude-code", "started_at": "1"},
        "body",
    )
    adapter = AgentMemoryAdapter(mesh, repo_root)
    refs = adapter.discover()
    assert refs[0].repo == "my-repo"


# =============================================================================
# D. Idempotency via sync state
# =============================================================================


def test_second_discover_returns_empty_after_state_push(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    for i in range(3):
        _write_md(
            mem / f"s{i}.md",
            {"session_id": f"s{i}", "agent": "claude-code", "started_at": str(100 + i)},
            f"body {i}",
        )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    first = adapter.discover()
    assert len(first) == 3
    hashes = [dict(r.metadata)["content_sha256"] for r in first]

    adapter.set_sync_state(
        SyncState(
            adapter_name="agent-memory",
            cursor="cur-1",
            last_synced_at=None,
            state={"seen_hashes": hashes},
        )
    )

    second = adapter.discover()
    assert second == []


def test_second_discover_returns_only_new_files_after_partial_state_push(
    mesh: Mesh, tmp_path: Path
) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    for i in range(3):
        _write_md(
            mem / f"s{i}.md",
            {"session_id": f"s{i}", "agent": "claude-code", "started_at": str(100 + i)},
            f"body {i}",
        )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    first = adapter.discover()
    persisted = [
        dict(first[0].metadata)["content_sha256"],
        dict(first[1].metadata)["content_sha256"],
    ]
    adapter.set_sync_state(
        SyncState(
            adapter_name="agent-memory",
            cursor="cur-1",
            last_synced_at=None,
            state={"seen_hashes": persisted},
        )
    )

    _write_md(
        mem / "s4.md",
        {"session_id": "s4", "agent": "claude-code", "started_at": "400"},
        "fourth",
    )

    second = adapter.discover()
    assert [r.source_id for r in second] == ["s2", "s4"]


def test_set_sync_state_rejects_mismatched_adapter_name(mesh: Mesh, tmp_path: Path) -> None:
    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with pytest.raises(ValueError):
        adapter.set_sync_state(
            SyncState(
                adapter_name="other",
                cursor="c",
                last_synced_at=None,
                state={"seen_hashes": []},
            )
        )


# =============================================================================
# E. Malformed frontmatter
# =============================================================================


def test_malformed_no_frontmatter_skipped_with_warning(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    (mem / "raw.md").write_text("just text, no fence at all\n", encoding="utf-8")

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    assert any(r.get("event") == "malformed_frontmatter" for r in logs)


def test_malformed_missing_required_keys_skipped(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    _write_md(
        mem / "x.md",
        {"agent": "claude-code", "started_at": "1"},
        "body",
    )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    matches = [r for r in logs if r.get("event") == "malformed_frontmatter"]
    assert matches
    assert "session_id" in matches[0].get("reason", "")


def test_malformed_unterminated_frontmatter_skipped(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    (mem / "broken.md").write_text(
        "---\nsession_id: x\nagent: claude-code\nstarted_at: 1\nbody without close\n",
        encoding="utf-8",
    )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert refs == []
    assert any(r.get("event") == "malformed_frontmatter" for r in logs)


# =============================================================================
# F. Edge cases
# =============================================================================


def test_empty_file_skipped_with_warning(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    (mem / "empty.md").write_text("", encoding="utf-8")
    _write_md(
        mem / "ok.md",
        {"session_id": "ok", "agent": "claude-code", "started_at": "1"},
        "ok body",
    )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert [r.source_id for r in refs] == ["ok"]
    assert any(r.get("event") == "empty_file" for r in logs)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires admin on Windows")
def test_symlink_inside_agent_memory_skipped(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    target = tmp_path / "outside.md"
    _write_md(
        target,
        {"session_id": "outside", "agent": "claude-code", "started_at": "1"},
        "body",
    )
    (mem / "link.md").symlink_to(target)
    _write_md(
        mem / "real.md",
        {"session_id": "real", "agent": "claude-code", "started_at": "2"},
        "real body",
    )

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    refs = adapter.discover()
    assert [r.source_id for r in refs] == ["real"]


def test_duplicate_content_hash_skipped_first_wins(mesh: Mesh, tmp_path: Path) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    fm = {"session_id": "dup", "agent": "claude-code", "started_at": "1"}
    _write_md(mem / "a.md", fm, "identical body")
    _write_md(mem / "b.md", fm, "identical body")

    adapter = AgentMemoryAdapter(mesh, tmp_path)
    with structlog.testing.capture_logs() as logs:
        refs = adapter.discover()
    assert len(refs) == 1
    assert refs[0].transcript_uri == str((mem / "a.md").resolve())
    assert any(r.get("event") == "duplicate_content" for r in logs)


# =============================================================================
# Bonus
# =============================================================================


@pytest.mark.parametrize(
    ("started_at_value", "expected"),
    [
        ("1700000000", 1_700_000_000),
        (
            "2023-11-14T22:13:20+00:00",
            int(datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC).timestamp()),
        ),
        (
            "2023-11-14T22:13:20Z",
            int(datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC).timestamp()),
        ),
    ],
)
def test_started_at_accepts_iso_and_epoch(
    mesh: Mesh, tmp_path: Path, started_at_value: str, expected: int
) -> None:
    mem = tmp_path / ".agent-memory"
    mem.mkdir()
    _write_md(
        mem / "iso.md",
        {"session_id": "iso", "agent": "claude-code", "started_at": started_at_value},
        "body",
    )
    adapter = AgentMemoryAdapter(mesh, tmp_path)
    refs = adapter.discover()
    assert refs[0].started_at == expected
