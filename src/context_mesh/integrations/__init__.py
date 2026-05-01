"""Public agent-tool schema surface.

Exports machine-readable tool definitions for three agent ecosystems:
Anthropic tool-use, OpenAI function-calling, and the generic Model
Context Protocol (MCP). The schemas describe the same five tools that
the HTTP server in `context_mesh.server` exposes.

Use `tool_for(name, dialect)` for single-tool lookups, or import the
full lists (`ANTHROPIC_TOOLS`, `OPENAI_TOOLS`, `MCP_TOOLS`) when
registering the entire surface with an agent SDK.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from context_mesh.integrations.tools import (
    ANTHROPIC_TOOLS,
    MCP_TOOLS,
    OPENAI_TOOLS,
    TOOL_NAMES,
)

Dialect = Literal["anthropic", "openai", "mcp"]

_DIALECTS: tuple[Dialect, ...] = ("anthropic", "openai", "mcp")


def _name_of(tool: dict[str, Any], dialect: Dialect) -> str:
    if dialect == "openai":
        return str(tool["function"]["name"])
    return str(tool["name"])


def tool_for(name: str, dialect: Dialect) -> dict[str, Any]:
    """Return the schema for `name` in the requested `dialect`.

    Raises:
        ValueError: if `name` is not one of `TOOL_NAMES` or `dialect` is
            not one of ``"anthropic"``, ``"openai"``, ``"mcp"``.
    """
    if name not in TOOL_NAMES:
        raise ValueError(
            f"unknown tool {name!r}; expected one of {list(TOOL_NAMES)}",
        )
    if dialect not in _DIALECTS:
        raise ValueError(
            f"unknown dialect {dialect!r}; expected one of {list(_DIALECTS)}",
        )

    tools: list[dict[str, Any]]
    if dialect == "anthropic":
        tools = ANTHROPIC_TOOLS
    elif dialect == "openai":
        tools = OPENAI_TOOLS
    else:
        tools = MCP_TOOLS

    for tool in tools:
        if _name_of(tool, dialect) == name:
            return copy.deepcopy(tool)

    # Unreachable: the lists are built from `_SPECS` whose names are `TOOL_NAMES`.
    raise ValueError(f"tool {name!r} missing from {dialect!r} projection")


__all__ = [
    "ANTHROPIC_TOOLS",
    "MCP_TOOLS",
    "OPENAI_TOOLS",
    "TOOL_NAMES",
    "Dialect",
    "tool_for",
]
