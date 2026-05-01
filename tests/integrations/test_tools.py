"""Tests for the agent tool-schema integrations module."""

from __future__ import annotations

import json
from typing import Any

import pytest

from context_mesh.integrations import (
    ANTHROPIC_TOOLS,
    MCP_TOOLS,
    OPENAI_TOOLS,
    TOOL_NAMES,
    tool_for,
)

EXPECTED_NAMES: tuple[str, ...] = (
    "search_team_memory",
    "drill_down_memory",
    "add_memory",
    "mark_memory_used",
    "find_contradictions",
)


def test_tool_names_match_spec() -> None:
    assert TOOL_NAMES == EXPECTED_NAMES


def test_every_dialect_has_every_tool() -> None:
    assert len(ANTHROPIC_TOOLS) == len(EXPECTED_NAMES)
    assert len(OPENAI_TOOLS) == len(EXPECTED_NAMES)
    assert len(MCP_TOOLS) == len(EXPECTED_NAMES)

    anthropic_names = {t["name"] for t in ANTHROPIC_TOOLS}
    openai_names = {t["function"]["name"] for t in OPENAI_TOOLS}
    mcp_names = {t["name"] for t in MCP_TOOLS}

    assert anthropic_names == set(EXPECTED_NAMES)
    assert openai_names == set(EXPECTED_NAMES)
    assert mcp_names == set(EXPECTED_NAMES)


def test_anthropic_dialect_shape() -> None:
    for tool in ANTHROPIC_TOOLS:
        assert set(tool.keys()) == {"name", "description", "input_schema"}
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]
        schema = tool["input_schema"]
        assert isinstance(schema, dict)
        assert schema["type"] == "object"
        assert "properties" in schema


def test_openai_dialect_shape() -> None:
    for tool in OPENAI_TOOLS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert set(fn.keys()) == {"name", "description", "parameters"}
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert isinstance(params, dict)
        assert params["type"] == "object"
        assert "properties" in params


def test_mcp_dialect_shape() -> None:
    for tool in MCP_TOOLS:
        assert set(tool.keys()) == {"name", "description", "inputSchema"}
        assert "input_schema" not in tool
        schema = tool["inputSchema"]
        assert isinstance(schema, dict)
        assert schema["type"] == "object"
        assert "properties" in schema


def test_schemas_are_json_round_trippable() -> None:
    for bundle in (ANTHROPIC_TOOLS, OPENAI_TOOLS, MCP_TOOLS):
        encoded = json.dumps(bundle)
        decoded = json.loads(encoded)
        assert decoded == bundle


def test_search_input_schema_required_fields() -> None:
    spec = tool_for("search_team_memory", "anthropic")
    schema = spec["input_schema"]
    assert schema["required"] == ["query"]
    props = schema["properties"]
    assert props["kind"]["enum"] == ["episodic", "semantic", "procedural", "all"]
    assert props["scope"]["enum"] == ["private", "team", "org", "all"]
    assert props["limit"]["minimum"] == 1
    assert props["limit"]["maximum"] == 20


def test_add_memory_required_fields() -> None:
    spec = tool_for("add_memory", "anthropic")
    schema = spec["input_schema"]
    assert set(schema["required"]) == {"content", "kind"}
    assert schema["properties"]["kind"]["enum"] == ["episodic", "semantic", "procedural"]


def test_feedback_required_fields() -> None:
    spec = tool_for("mark_memory_used", "anthropic")
    schema = spec["input_schema"]
    assert set(schema["required"]) == {"node_id", "helpful"}
    assert schema["properties"]["helpful"]["type"] == "boolean"


def test_contradictions_required_fields() -> None:
    spec = tool_for("find_contradictions", "anthropic")
    schema = spec["input_schema"]
    assert schema["required"] == ["content"]
    assert schema["properties"]["kind"]["enum"] == ["semantic", "procedural"]


def test_tool_for_returns_anthropic_dict() -> None:
    spec = tool_for("search_team_memory", "anthropic")
    assert spec["name"] == "search_team_memory"
    assert "input_schema" in spec
    assert "Call this FIRST" in spec["description"]


def test_tool_for_returns_openai_dict() -> None:
    spec = tool_for("search_team_memory", "openai")
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "search_team_memory"
    assert "parameters" in spec["function"]


def test_tool_for_returns_mcp_dict() -> None:
    spec = tool_for("search_team_memory", "mcp")
    assert spec["name"] == "search_team_memory"
    assert "inputSchema" in spec


def test_tool_for_returns_a_copy() -> None:
    a = tool_for("search_team_memory", "anthropic")
    b = tool_for("search_team_memory", "anthropic")
    a["description"] = "MUTATED"
    assert b["description"] != "MUTATED"
    assert ANTHROPIC_TOOLS[0]["description"] != "MUTATED"


def test_tool_for_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        tool_for("not_a_tool", "anthropic")


def test_tool_for_unknown_dialect_raises() -> None:
    with pytest.raises(ValueError, match="unknown dialect"):
        tool_for("search_team_memory", "bogus")  # type: ignore[arg-type]


def test_descriptions_are_prescriptive() -> None:
    """Descriptions tell the agent WHEN to call, not just what the tool does."""
    keywords = ("call this", "use", "before", "after", "first", "always")
    for tool in ANTHROPIC_TOOLS:
        desc = tool["description"].lower()
        assert any(k in desc for k in keywords), (
            f"description for {tool['name']!r} should be prescriptive: {desc}"
        )


def test_dialect_projections_share_schema_content() -> None:
    """Anthropic input_schema, OpenAI parameters, and MCP inputSchema match per tool."""
    by_name_a: dict[str, dict[str, Any]] = {t["name"]: t for t in ANTHROPIC_TOOLS}
    by_name_o: dict[str, dict[str, Any]] = {t["function"]["name"]: t for t in OPENAI_TOOLS}
    by_name_m: dict[str, dict[str, Any]] = {t["name"]: t for t in MCP_TOOLS}

    for name in TOOL_NAMES:
        a = by_name_a[name]["input_schema"]
        o = by_name_o[name]["function"]["parameters"]
        m = by_name_m[name]["inputSchema"]
        assert a == o == m
