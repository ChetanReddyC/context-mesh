"""Tests for the `context-mesh distill` CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from context_mesh.api import Mesh
from context_mesh.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


_SAMPLE_TRANSCRIPT = """\
Decision: We will always use JWT for auth on every API request.
This decision is mandatory for new services across the team.

Decision: All secrets must be loaded from the environment, never hard-coded.
This decision applies team-wide.

Run pytest -x to stop on the first failing test. Use --pdb to drop into a debugger.

We tried using session cookies but it didn't work due to cross-domain limits.
Reverted that change after rollback.
"""


def test_distill_persists_nodes(empty_db: Path, tmp_path: Path) -> None:
    transcript = tmp_path / "session.txt"
    transcript.write_text(_SAMPLE_TRANSCRIPT, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "distill",
            str(transcript),
            "--scope-id",
            "team",
            "--source-session-id",
            "sess-1",
            "--source-repo",
            "repo-x",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "distilled" in result.output

    mesh = Mesh.local(empty_db)
    try:
        nodes = mesh.list_nodes()
    finally:
        mesh.close()
    assert len(nodes) >= 1


def test_distill_rejects_unknown_distiller(empty_db: Path, tmp_path: Path) -> None:
    transcript = tmp_path / "session.txt"
    transcript.write_text("noop", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "distill",
            str(transcript),
            "--scope-id",
            "team",
            "--source-session-id",
            "s1",
            "--source-repo",
            "r1",
            "--distiller",
            "bogus",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 1
    assert "unknown distiller" in result.stderr


def test_distill_missing_file(empty_db: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    result = runner.invoke(
        app,
        [
            "distill",
            str(missing),
            "--scope-id",
            "team",
            "--source-session-id",
            "s1",
            "--source-repo",
            "r1",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code != 0


def test_distill_empty_transcript_yields_no_candidates(empty_db: Path, tmp_path: Path) -> None:
    transcript = tmp_path / "session.txt"
    transcript.write_text("   \n   \n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "distill",
            str(transcript),
            "--scope-id",
            "team",
            "--source-session-id",
            "s1",
            "--source-repo",
            "r1",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no candidates extracted" in result.output


def test_distill_invalid_scope_id(empty_db: Path, tmp_path: Path) -> None:
    transcript = tmp_path / "session.txt"
    transcript.write_text(_SAMPLE_TRANSCRIPT, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "distill",
            str(transcript),
            "--scope-id",
            "   ",
            "--source-session-id",
            "s1",
            "--source-repo",
            "r1",
            "--db",
            str(empty_db),
        ],
    )
    assert result.exit_code == 1
    assert "scope_id must not be empty" in result.stderr
