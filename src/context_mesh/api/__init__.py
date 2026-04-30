"""Public library surface: `Mesh` and the node/edge dataclasses."""

from __future__ import annotations

from context_mesh.api.mesh import Mesh
from context_mesh.api.types import (
    EDGE_COLUMNS,
    NODE_COLUMNS,
    EdgeCreatedBy,
    EdgeRelation,
    MemoryEdge,
    MemoryNode,
    NodeKind,
)

__all__ = [
    "EDGE_COLUMNS",
    "NODE_COLUMNS",
    "EdgeCreatedBy",
    "EdgeRelation",
    "MemoryEdge",
    "MemoryNode",
    "Mesh",
    "NodeKind",
]
