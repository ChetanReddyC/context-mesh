"""Tests for `Mesh.mark_used` — usage/helpful signal recorder."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

from context_mesh.api import Mesh

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from context_mesh.api.types import MemoryNode


@pytest.fixture()
def mesh(tmp_path: Path) -> Iterator[Mesh]:
    m = Mesh.local(tmp_path / "mu.db")
    m.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("scope-a", "scope-a", "private", 100),
    )
    m.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("ses-1", "repo", "claude-code", 100),
    )
    m.connection.commit()
    try:
        yield m
    finally:
        m.close()


def _seed(mesh_obj: Mesh, body: str = "rule") -> MemoryNode:
    node = mesh_obj.make_node(
        kind="semantic",
        body=body,
        headline=body[:80],
        scope_id="scope-a",
        source_session_id="ses-1",
        source_repo="repo",
    )
    mesh_obj.add(node)
    return node


def test_mark_used_increments_usage_count_each_call(mesh: Mesh) -> None:
    node = _seed(mesh)
    assert node.usage_count == 0

    after_first = mesh.mark_used(node.id, helpful=False)
    assert after_first.usage_count == 1

    after_second = mesh.mark_used(node.id, helpful=False)
    assert after_second.usage_count == 2

    after_third = mesh.mark_used(node.id, helpful=True)
    assert after_third.usage_count == 3


def test_mark_used_helpful_true_increments_helpful_count(mesh: Mesh) -> None:
    node = _seed(mesh)
    assert node.helpful_count == 0

    refreshed = mesh.mark_used(node.id, helpful=True)
    assert refreshed.helpful_count == 1

    refreshed = mesh.mark_used(node.id, helpful=True)
    assert refreshed.helpful_count == 2


def test_mark_used_helpful_false_does_not_increment_helpful_count(mesh: Mesh) -> None:
    node = _seed(mesh)

    refreshed = mesh.mark_used(node.id, helpful=False)
    assert refreshed.helpful_count == 0
    assert refreshed.usage_count == 1

    refreshed = mesh.mark_used(node.id, helpful=False)
    assert refreshed.helpful_count == 0
    assert refreshed.usage_count == 2


def test_mark_used_updates_updated_at(mesh: Mesh) -> None:
    node = _seed(mesh)
    original_updated = node.updated_at

    time.sleep(1.1)
    refreshed = mesh.mark_used(node.id, helpful=True)

    assert refreshed.updated_at > original_updated


def test_mark_used_returns_refreshed_node(mesh: Mesh) -> None:
    node = _seed(mesh)

    refreshed = mesh.mark_used(node.id, helpful=True)

    assert refreshed.id == node.id
    fetched = mesh.get(node.id)
    assert fetched is not None
    assert fetched.usage_count == refreshed.usage_count
    assert fetched.helpful_count == refreshed.helpful_count
    assert fetched.updated_at == refreshed.updated_at


def test_mark_used_unknown_node_raises(mesh: Mesh) -> None:
    with pytest.raises(ValueError, match="not found"):
        mesh.mark_used("nonexistent-id", helpful=True)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_mark_used_empty_node_id_raises(mesh: Mesh, bad: str) -> None:
    with pytest.raises(ValueError, match="node_id"):
        mesh.mark_used(bad, helpful=True)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_mark_used_empty_actor_raises(mesh: Mesh, bad: str) -> None:
    node = _seed(mesh)
    with pytest.raises(ValueError, match="actor"):
        mesh.mark_used(node.id, helpful=True, actor=bad)


def test_mark_used_non_string_notes_raises(mesh: Mesh) -> None:
    node = _seed(mesh)
    with pytest.raises(TypeError, match="notes"):
        mesh.mark_used(node.id, helpful=True, notes=123)  # type: ignore[arg-type]


def test_mark_used_helpful_true_audits_mark_helpful(mesh: Mesh) -> None:
    node = _seed(mesh)

    mesh.mark_used(node.id, helpful=True)

    row = mesh.connection.execute(
        "SELECT event_type, actor, node_ids, metadata FROM audit WHERE event_type = 'mark_helpful'"
    ).fetchone()
    assert row is not None
    event_type, actor, node_ids_json, metadata_json = row
    assert event_type == "mark_helpful"
    assert actor == "mesh"
    assert json.loads(node_ids_json) == [node.id]
    assert metadata_json is None


def test_mark_used_helpful_false_audits_mark_unhelpful(mesh: Mesh) -> None:
    node = _seed(mesh)

    mesh.mark_used(node.id, helpful=False)

    row = mesh.connection.execute(
        "SELECT event_type, actor, node_ids, metadata FROM audit "
        "WHERE event_type = 'mark_unhelpful'"
    ).fetchone()
    assert row is not None
    event_type, actor, node_ids_json, metadata_json = row
    assert event_type == "mark_unhelpful"
    assert actor == "mesh"
    assert json.loads(node_ids_json) == [node.id]
    assert metadata_json is None


def test_mark_used_notes_lands_in_audit_metadata(mesh: Mesh) -> None:
    node = _seed(mesh)

    mesh.mark_used(node.id, helpful=True, notes="confirmed in code review")

    row = mesh.connection.execute(
        "SELECT metadata FROM audit WHERE event_type = 'mark_helpful'"
    ).fetchone()
    assert row is not None
    metadata = json.loads(row[0])
    assert metadata == {"notes": "confirmed in code review"}


def test_mark_used_custom_actor_propagates(mesh: Mesh) -> None:
    node = _seed(mesh)

    mesh.mark_used(node.id, helpful=True, actor="agent:claude-code")

    row = mesh.connection.execute(
        "SELECT actor FROM audit WHERE event_type = 'mark_helpful'"
    ).fetchone()
    assert row[0] == "agent:claude-code"
