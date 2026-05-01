"""Tests for `load_or_create_token`."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from context_mesh.server import load_or_create_token

if TYPE_CHECKING:
    from pathlib import Path


def test_creates_token_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "tok"
    assert not target.exists()
    token = load_or_create_token(target)
    assert target.exists()
    assert token
    assert target.read_text(encoding="utf-8").strip() == token


def test_subsequent_calls_return_same_token(tmp_path: Path) -> None:
    target = tmp_path / "tok"
    first = load_or_create_token(target)
    second = load_or_create_token(target)
    assert first == second


def test_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "tok"
    token = load_or_create_token(target)
    assert target.exists()
    assert token


def test_strips_trailing_whitespace(tmp_path: Path) -> None:
    target = tmp_path / "tok"
    target.write_text("  preset-token  \n", encoding="utf-8")
    assert load_or_create_token(target) == "preset-token"


def test_regenerates_when_file_is_empty(tmp_path: Path) -> None:
    target = tmp_path / "tok"
    target.write_text("", encoding="utf-8")
    token = load_or_create_token(target)
    assert token
    assert target.read_text(encoding="utf-8").strip() == token


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission semantics")
def test_token_file_has_0o600_permissions(tmp_path: Path) -> None:
    target = tmp_path / "tok"
    load_or_create_token(target)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600
