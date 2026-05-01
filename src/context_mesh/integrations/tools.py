"""Canonical agent-tool schemas for the five `Mesh` tools.

Each tool is defined ONCE as a `_ToolSpec` (name, description, input schema),
then projected to three dialects:

- **Anthropic** tool-use: ``{"name", "description", "input_schema"}``.
- **OpenAI** function-calling: ``{"type": "function", "function": {...}}``.
- **MCP** (Model Context Protocol): ``{"name", "description", "inputSchema"}``.

The descriptions are agent-facing — they tell the model WHEN to call the tool,
not what it does internally. Keep them prescriptive.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Final

_SEARCH_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Free-text search query."},
        "kind": {
            "type": "string",
            "enum": ["episodic", "semantic", "procedural", "all"],
            "description": "Filter by memory kind. 'all' returns every kind.",
            "default": "all",
        },
        "scope": {
            "type": "string",
            "enum": ["private", "team", "org", "all"],
            "description": "Visibility scope to search within.",
            "default": "all",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Max nodes to return.",
            "default": 5,
        },
    },
    "required": ["query"],
}

_DRILL_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "description": "Full UUID of the node to fetch."},
    },
    "required": ["node_id"],
}

_ADD_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "content": {"type": "string", "description": "Body text of the memory."},
        "kind": {"type": "string", "enum": ["episodic", "semantic", "procedural"]},
        "scope": {"type": "string", "default": "private"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "related_to": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of related node IDs (currently advisory; v1 ignores).",
        },
    },
    "required": ["content", "kind"],
}

_FEEDBACK_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string"},
        "helpful": {"type": "boolean"},
        "notes": {"type": "string", "description": "Optional explanation."},
    },
    "required": ["node_id", "helpful"],
}

_CONTRADICTIONS_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["semantic", "procedural"],
            "default": "semantic",
        },
    },
    "required": ["content"],
}


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


_SPECS: Final[tuple[_ToolSpec, ...]] = (
    _ToolSpec(
        name="search_team_memory",
        description=(
            "Search the team's shared memory for relevant prior decisions, lessons, and "
            "procedures. Call this FIRST before making non-trivial decisions, when starting a "
            "new task, or when stuck on a familiar-feeling problem. Returns a cluster of "
            "related memories ranked by semantic + structural relevance."
        ),
        input_schema=_SEARCH_INPUT,
    ),
    _ToolSpec(
        name="drill_down_memory",
        description=(
            "Fetch the full body and metadata of a specific memory node by id. Call this "
            "after `search_team_memory` when a result's headline/summary suggests it has the "
            "detail you need."
        ),
        input_schema=_DRILL_INPUT,
    ),
    _ToolSpec(
        name="add_memory",
        description=(
            "Persist a distilled lesson, decision, or procedure to the team's memory. Use "
            "sparingly — capture the GENERALIZABLE rule, not the play-by-play. The system "
            "will mark it for human review before it propagates to the team scope."
        ),
        input_schema=_ADD_INPUT,
    ),
    _ToolSpec(
        name="mark_memory_used",
        description=(
            "Record whether a previously-retrieved memory was helpful. This feeds the "
            "ranking signal so future retrievals improve. Always call after acting on a "
            "retrieved memory."
        ),
        input_schema=_FEEDBACK_INPUT,
    ),
    _ToolSpec(
        name="find_contradictions",
        description=(
            "Before committing to a non-trivial decision, check whether the team has "
            "previously decided differently. Returns memories most likely to contradict the "
            "proposed content."
        ),
        input_schema=_CONTRADICTIONS_INPUT,
    ),
)


TOOL_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in _SPECS)


def _anthropic(spec: _ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": copy.deepcopy(spec.input_schema),
    }


def _openai(spec: _ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": copy.deepcopy(spec.input_schema),
        },
    }


def _mcp(spec: _ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": copy.deepcopy(spec.input_schema),
    }


ANTHROPIC_TOOLS: Final[list[dict[str, Any]]] = [_anthropic(spec) for spec in _SPECS]
OPENAI_TOOLS: Final[list[dict[str, Any]]] = [_openai(spec) for spec in _SPECS]
MCP_TOOLS: Final[list[dict[str, Any]]] = [_mcp(spec) for spec in _SPECS]


__all__ = [
    "ANTHROPIC_TOOLS",
    "MCP_TOOLS",
    "OPENAI_TOOLS",
    "TOOL_NAMES",
]
