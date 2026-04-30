"""Tests for `context_mesh.api.types` — dataclass round-trips and column locks."""

from __future__ import annotations

import json
from typing import Any

import pytest

from context_mesh.api.types import (
    EDGE_COLUMNS,
    NODE_COLUMNS,
    MemoryEdge,
    MemoryNode,
)


def _minimal_node_kwargs() -> dict[str, Any]:
    return {
        "id": "node-1",
        "kind": "semantic",
        "body": "Always re-verify webhook timestamps.",
        "headline": "Re-verify webhook timestamps",
        "scope_id": "scope-1",
        "source_session_id": "session-1",
        "source_repo": "payments",
        "created_at": 1_700_000_000,
        "updated_at": 1_700_000_000,
        "content_hash": "deadbeef",
    }


def _full_node_kwargs() -> dict[str, Any]:
    return {
        **_minimal_node_kwargs(),
        "summary": "Webhook timestamps cannot be trusted.",
        "decisions": ["always re-verify with current_time"],
        "failed_approaches": ["trusting first webhook timestamp"],
        "warnings": ["beware replay attacks"],
        "error_signatures": ["StripeWebhookTimeoutError"],
        "cause_chain": ["received webhook", "trusted timestamp", "timeout"],
        "file_dependencies": ["src/payments/webhook.py"],
        "key_insight": "the timestamp is provided by the sender",
        "tags": ["stripe", "webhooks", "security"],
        "source_branch": "main",
        "source_commit": "abc1234",
        "decayed_at": 1_700_900_000,
        "importance": 0.87,
        "usage_count": 4,
        "helpful_count": 3,
        "superseded_by": "node-99",
    }


def _minimal_edge_kwargs() -> dict[str, Any]:
    return {
        "id": "edge-1",
        "from_node_id": "node-1",
        "to_node_id": "node-2",
        "relation": "generalizes",
        "created_at": 1_700_000_000,
        "created_by": "agent",
    }


def test_node_columns_match_schema_count() -> None:
    assert len(NODE_COLUMNS) == 26


def test_edge_columns_match_schema_count() -> None:
    assert len(EDGE_COLUMNS) == 8


def test_memory_node_minimal_construction_defaults_optional_to_none() -> None:
    node = MemoryNode(**_minimal_node_kwargs())

    assert node.summary is None
    assert node.decisions is None
    assert node.failed_approaches is None
    assert node.warnings is None
    assert node.error_signatures is None
    assert node.cause_chain is None
    assert node.file_dependencies is None
    assert node.key_insight is None
    assert node.tags is None
    assert node.source_branch is None
    assert node.source_commit is None
    assert node.decayed_at is None
    assert node.superseded_by is None
    assert node.importance == 0.0
    assert node.usage_count == 0
    assert node.helpful_count == 0


def test_memory_node_to_row_returns_correct_arity() -> None:
    node = MemoryNode(**_minimal_node_kwargs())
    assert len(node.to_row()) == 26


def test_memory_node_to_row_column_order_matches_node_columns() -> None:
    node = MemoryNode(
        id="id-x",
        kind="episodic",
        body="body-x",
        headline="headline-x",
        scope_id="scope-x",
        source_session_id="session-x",
        source_repo="repo-x",
        created_at=11,
        updated_at=22,
        content_hash="hash-x",
        summary="summary-x",
        decisions=["d1"],
        failed_approaches=["f1"],
        warnings=["w1"],
        error_signatures=["e1"],
        cause_chain=["c1"],
        file_dependencies=["path-x"],
        key_insight="insight-x",
        tags=["t1"],
        source_branch="branch-x",
        source_commit="commit-x",
        decayed_at=33,
        importance=0.5,
        usage_count=7,
        helpful_count=2,
        superseded_by="other-x",
    )

    row = node.to_row()
    by_column = dict(zip(NODE_COLUMNS, row, strict=True))

    assert by_column["id"] == "id-x"
    assert by_column["kind"] == "episodic"
    assert by_column["body"] == "body-x"
    assert by_column["headline"] == "headline-x"
    assert by_column["summary"] == "summary-x"
    assert by_column["decisions"] == json.dumps(["d1"])
    assert by_column["failed_approaches"] == json.dumps(["f1"])
    assert by_column["warnings"] == json.dumps(["w1"])
    assert by_column["error_signatures"] == json.dumps(["e1"])
    assert by_column["cause_chain"] == json.dumps(["c1"])
    assert by_column["file_dependencies"] == json.dumps(["path-x"])
    assert by_column["key_insight"] == "insight-x"
    assert by_column["tags"] == json.dumps(["t1"])
    assert by_column["scope_id"] == "scope-x"
    assert by_column["source_session_id"] == "session-x"
    assert by_column["source_repo"] == "repo-x"
    assert by_column["source_branch"] == "branch-x"
    assert by_column["source_commit"] == "commit-x"
    assert by_column["created_at"] == 11
    assert by_column["updated_at"] == 22
    assert by_column["decayed_at"] == 33
    assert by_column["importance"] == 0.5
    assert by_column["usage_count"] == 7
    assert by_column["helpful_count"] == 2
    assert by_column["superseded_by"] == "other-x"
    assert by_column["content_hash"] == "hash-x"


def test_memory_node_to_row_serializes_json_list_fields() -> None:
    node = MemoryNode(
        **_minimal_node_kwargs(),
        decisions=["a", "b"],
        tags=["x", "y", "z"],
    )
    row = node.to_row()
    by_column = dict(zip(NODE_COLUMNS, row, strict=True))

    assert json.loads(by_column["decisions"]) == ["a", "b"]
    assert json.loads(by_column["tags"]) == ["x", "y", "z"]


def test_memory_node_to_row_serializes_none_lists_as_none() -> None:
    node = MemoryNode(**_minimal_node_kwargs())
    row = node.to_row()
    by_column = dict(zip(NODE_COLUMNS, row, strict=True))

    for column in (
        "decisions",
        "failed_approaches",
        "warnings",
        "error_signatures",
        "cause_chain",
        "file_dependencies",
        "tags",
    ):
        assert by_column[column] is None


def test_memory_node_from_row_round_trip() -> None:
    original = MemoryNode(**_minimal_node_kwargs())
    rebuilt = MemoryNode.from_row(original.to_row())
    assert rebuilt == original


def test_memory_node_from_row_round_trip_with_all_optionals_populated() -> None:
    original = MemoryNode(**_full_node_kwargs())
    rebuilt = MemoryNode.from_row(original.to_row())
    assert rebuilt == original


def test_memory_node_from_row_rejects_wrong_arity() -> None:
    bad_row = ("x",) * 25
    with pytest.raises(ValueError, match="expected 26 columns"):
        MemoryNode.from_row(bad_row)


def test_compute_content_hash_deterministic() -> None:
    a = MemoryNode.compute_content_hash("body", "insight", ["d1", "d2"])
    b = MemoryNode.compute_content_hash("body", "insight", ["d1", "d2"])
    assert a == b


def test_compute_content_hash_changes_with_body() -> None:
    a = MemoryNode.compute_content_hash("body-1", "insight", ["d1"])
    b = MemoryNode.compute_content_hash("body-2", "insight", ["d1"])
    assert a != b


def test_compute_content_hash_changes_with_decision_order() -> None:
    a = MemoryNode.compute_content_hash("body", "insight", ["d1", "d2"])
    b = MemoryNode.compute_content_hash("body", "insight", ["d2", "d1"])
    assert a != b


def test_compute_content_hash_handles_none_inputs() -> None:
    a = MemoryNode.compute_content_hash("x", None, None)
    b = MemoryNode.compute_content_hash("x", "", [])
    assert a == b


def test_memory_edge_to_row_returns_correct_arity_and_order() -> None:
    edge = MemoryEdge(
        id="edge-x",
        from_node_id="from-x",
        to_node_id="to-x",
        relation="contradicts",
        created_at=42,
        created_by="manual",
        confidence=0.75,
        metadata={"reason": "manually flagged"},
    )

    row = edge.to_row()
    assert len(row) == 8

    by_column = dict(zip(EDGE_COLUMNS, row, strict=True))
    assert by_column["id"] == "edge-x"
    assert by_column["from_node_id"] == "from-x"
    assert by_column["to_node_id"] == "to-x"
    assert by_column["relation"] == "contradicts"
    assert by_column["confidence"] == 0.75
    assert by_column["created_at"] == 42
    assert by_column["created_by"] == "manual"
    assert json.loads(by_column["metadata"]) == {"reason": "manually flagged"}


def test_memory_edge_to_row_serializes_metadata_dict() -> None:
    edge = MemoryEdge(
        **_minimal_edge_kwargs(),
        metadata={"score": 0.9, "note": "auto-inferred"},
    )
    row = edge.to_row()
    by_column = dict(zip(EDGE_COLUMNS, row, strict=True))
    assert json.loads(by_column["metadata"]) == {"score": 0.9, "note": "auto-inferred"}


def test_memory_edge_round_trip() -> None:
    original = MemoryEdge(
        **_minimal_edge_kwargs(),
        confidence=0.42,
        metadata={"k": "v"},
    )
    rebuilt = MemoryEdge.from_row(original.to_row())
    assert rebuilt == original


def test_memory_edge_default_confidence_is_one() -> None:
    edge = MemoryEdge(**_minimal_edge_kwargs())
    assert edge.confidence == 1.0


def test_memory_edge_from_row_rejects_wrong_arity() -> None:
    bad_row = ("x",) * 7
    with pytest.raises(ValueError, match="expected 8 columns"):
        MemoryEdge.from_row(bad_row)
