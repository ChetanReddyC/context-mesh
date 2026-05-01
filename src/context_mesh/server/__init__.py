"""HTTP server exposing the agent-facing memory tools.

Phase 4 Unit 3 ships a stdlib-only HTTP server (`http.server.ThreadingHTTPServer`)
that wraps the five `Mesh` agent tools as JSON REST endpoints. Auth is a single
bearer token persisted at `~/.context-mesh/token`. The CLI entry point is
`context-mesh serve`.
"""

from __future__ import annotations

from context_mesh.server.app import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    TOKEN_FILE,
    MeshServer,
    load_or_create_token,
    make_app,
)

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "TOKEN_FILE",
    "MeshServer",
    "load_or_create_token",
    "make_app",
]
