"""Typer CLI app for context-mesh.

Phase 0 shipped `init` plus a `--version` callback. Phase 4 adds the
developer-facing subcommands (`search`, `add`, `show`, `list`, `delete`,
`distill`, `stats`, `audit`).
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from context_mesh import __version__
from context_mesh._audit import log as audit_log
from context_mesh._logging import configure_logging, get_logger
from context_mesh.adapters import AgentMemoryAdapter, EntireAdapter, SourceAdapter
from context_mesh.api import Mesh
from context_mesh.api.types import (
    VALID_NODE_KINDS,
    MemoryCluster,
    MemoryEdge,
    MemoryNode,
    NodeKind,
)
from context_mesh.config import Config, ConfigError, load_config
from context_mesh.distillation import ClaudeCliDistiller, HeuristicDistiller
from context_mesh.embeddings import DeterministicEmbeddingProvider
from context_mesh.integrations import (
    ANTHROPIC_TOOLS,
    MCP_TOOLS,
    OPENAI_TOOLS,
)
from context_mesh.server import DEFAULT_HOST, DEFAULT_PORT, TOKEN_FILE, MeshServer
from context_mesh.storage import SqliteVecBackend

if TYPE_CHECKING:
    import sqlite3

    from context_mesh.adapters import SessionReference
    from context_mesh.adapters.sync import SyncResult
    from context_mesh.distillation.protocol import Distiller

app = typer.Typer(
    name="context-mesh",
    help="Federated, agent-native memory for AI coding agents.",
    no_args_is_help=True,
    add_completion=False,
)

_GITIGNORE_LINE = ".context-mesh/"
_CONFIG_TEMPLATE_PACKAGE = "context_mesh.cli.templates"
_CONFIG_TEMPLATE_FILENAME = "config.toml"

_SHORT_ID_LEN = 8
_HEADLINE_MAX_LEN = 120


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"context-mesh {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the context-mesh version and exit.",
        ),
    ] = False,
) -> None:
    """Top-level callback. Logging is configured per-command, not here."""


def _resolve_root(path: Path | None, use_global: bool) -> Path:
    if use_global:
        return Path.home() / ".context-mesh"
    base = (path or Path.cwd()).resolve()
    return base / ".context-mesh"


def _read_config_template() -> str:
    from importlib.resources import files

    return (files(_CONFIG_TEMPLATE_PACKAGE) / _CONFIG_TEMPLATE_FILENAME).read_text(encoding="utf-8")


def _update_gitignore(project_root: Path) -> None:
    gitignore = project_root / ".gitignore"
    line = _GITIGNORE_LINE
    try:
        if not gitignore.exists():
            gitignore.write_text(line + "\n", encoding="utf-8")
            return
        existing = gitignore.read_text(encoding="utf-8")
        lines = {entry.strip() for entry in existing.splitlines()}
        if line in lines:
            return
        suffix = "" if existing.endswith("\n") or existing == "" else "\n"
        gitignore.write_text(existing + suffix + line + "\n", encoding="utf-8")
    except OSError as exc:
        get_logger("context_mesh.cli").warning(
            "gitignore_update_failed", path=str(gitignore), error=str(exc)
        )


def _remove_existing_db(memory_db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = memory_db.with_name(memory_db.name + suffix)
        candidate.unlink(missing_ok=True)


def _resolve_db_path(explicit: Path | None) -> Path:
    """Resolve which memory.db to operate on.

    Order: --db arg, then $CONTEXT_MESH_DB, then config `storage.path`,
    then ./.context-mesh/memory.db. Raises typer.Exit(1) if none exist.
    """
    if explicit is not None:
        if not explicit.exists():
            typer.echo(f"error: database not found at {explicit}", err=True)
            raise typer.Exit(code=1)
        return explicit
    env_value = os.environ.get("CONTEXT_MESH_DB")
    if env_value:
        candidate = Path(env_value)
        if not candidate.exists():
            typer.echo(
                f"error: $CONTEXT_MESH_DB points to {candidate} which does not exist",
                err=True,
            )
            raise typer.Exit(code=1)
        return candidate

    config_path = _config_storage_path()
    if config_path is not None and config_path.exists():
        return config_path

    project_local = Path.cwd() / ".context-mesh" / "memory.db"
    if project_local.exists():
        return project_local
    typer.echo(
        "error: no context-mesh database found. "
        "Pass --db <path>, set CONTEXT_MESH_DB, or run `context-mesh init` first.",
        err=True,
    )
    raise typer.Exit(code=1)


def _config_storage_path() -> Path | None:
    """Return `storage.path` from the loaded config when at least one config
    file actually contributed to the resolution.

    Returns `None` when no global or project config file exists, so that
    we fall back to the historical `./.context-mesh/memory.db` heuristic
    rather than insisting on the bare default.
    """
    try:
        cfg = load_config()
    except ConfigError:
        return None
    if not cfg.source_paths:
        return None
    return Path(cfg.storage.path)


def _validate_kind(kind: str | None) -> NodeKind | None:
    if kind is None:
        return None
    if kind not in VALID_NODE_KINDS:
        typer.echo(
            f"error: invalid kind {kind!r}; expected one of {sorted(VALID_NODE_KINDS)}",
            err=True,
        )
        raise typer.Exit(code=1)
    return kind  # type: ignore[return-value]


def _short_id(node_id: str) -> str:
    return node_id[:_SHORT_ID_LEN]


def _epoch_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, default=str)


def _node_to_dict(node: MemoryNode) -> dict[str, Any]:
    return dataclasses.asdict(node)


def _edge_to_dict(edge: MemoryEdge) -> dict[str, Any]:
    return dataclasses.asdict(edge)


def _cluster_to_dict(cluster: MemoryCluster) -> dict[str, Any]:
    return {
        "cluster_confidence": cluster.cluster_confidence,
        "nodes": [
            {
                "node": _node_to_dict(scored.node),
                "semantic_score": scored.semantic_score,
                "recency_score": scored.recency_score,
                "importance_score": scored.importance_score,
                "usage_score": scored.usage_score,
                "relevance_score": scored.relevance_score,
                "composite_score": scored.composite_score,
            }
            for scored in cluster.nodes
        ],
        "edges": [_edge_to_dict(edge) for edge in cluster.edges],
    }


def _parse_tags(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    cleaned = [p for p in parts if p]
    return cleaned or None


def _derive_headline(content: str) -> str:
    stripped = content.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return first_line[:_HEADLINE_MAX_LEN]


@app.command("init")
def init(
    path: Annotated[
        Path | None,
        typer.Argument(
            exists=False,
            file_okay=False,
            dir_okay=True,
            help="Project directory to initialize. Defaults to the current directory.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing memory.db."),
    ] = False,
    use_global: Annotated[
        bool,
        typer.Option(
            "--global",
            help="Initialize at ~/.context-mesh/ instead of the project directory.",
        ),
    ] = False,
) -> None:
    """Initialize a context-mesh store (.context-mesh/memory.db + config)."""
    configure_logging(json_output=False)
    logger = get_logger("context_mesh.cli")

    mesh_dir = _resolve_root(path, use_global)
    memory_db = mesh_dir / "memory.db"
    config_path = mesh_dir / "config.toml"

    mesh_dir.mkdir(parents=True, exist_ok=True)

    if memory_db.exists():
        if not force:
            typer.echo(
                f"error: {memory_db} already exists. Re-run with --force to overwrite.",
                err=True,
            )
            raise typer.Exit(code=1)
        logger.warning("init_overwrite_existing_db", path=str(memory_db))
        _remove_existing_db(memory_db)

    backend = SqliteVecBackend(memory_db)
    try:
        if not config_path.exists():
            config_path.write_text(_read_config_template(), encoding="utf-8")

        if not use_global:
            _update_gitignore(mesh_dir.parent)

        audit_log(
            backend.connection,
            "init",
            actor="cli:init",
            metadata={"path": str(memory_db), "global": use_global, "force": force},
        )
    finally:
        backend.close()

    typer.echo(f"context-mesh initialized at {mesh_dir}")


@app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="Free-text query to search memory.")],
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Restrict to one node kind (episodic/semantic/procedural)."),
    ] = None,
    scope_id: Annotated[
        str | None,
        typer.Option("--scope-id", "--scope", help="Restrict to a single scope id."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100, help="Maximum results to return."),
    ] = 5,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of a table."),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Search memory using hybrid retrieval."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    kind_validated = _validate_kind(kind)
    db_path = _resolve_db_path(db)
    embedder = DeterministicEmbeddingProvider()

    mesh = Mesh.local(db_path)
    try:
        try:
            cluster = mesh.search(
                query,
                embedder=embedder,
                limit=limit,
                kind=kind_validated,
                scope_id=scope_id,
                actor="cli:search",
            )
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        if json_output:
            typer.echo(_dump_json(_cluster_to_dict(cluster)))
            return

        if not cluster.nodes:
            typer.echo("no results")
            return

        confidence = cluster.cluster_confidence if cluster.cluster_confidence is not None else 0.0
        typer.echo(f"{len(cluster.nodes)} results (cluster confidence: {confidence:.2f})")
        typer.echo("-" * 60)
        for idx, scored in enumerate(cluster.nodes, start=1):
            node = scored.node
            typer.echo(
                f"{idx:>2}. [{node.kind}] {_short_id(node.id)}  score={scored.composite_score:.1f}"
            )
            typer.echo(f"    {node.headline}")
    finally:
        mesh.close()


@app.command("add")
def add(
    content: Annotated[str, typer.Argument(help="Body text of the memory node.")],
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="Node kind: episodic, semantic, or procedural.",
        ),
    ],
    headline: Annotated[
        str | None,
        typer.Option("--headline", help="One-line title; derived from content if omitted."),
    ] = None,
    scope_id: Annotated[
        str,
        typer.Option("--scope-id", help="Scope id for federation/visibility."),
    ] = "cli",
    source_session_id: Annotated[
        str,
        typer.Option("--source-session-id", help="Provenance session id."),
    ] = "cli:adhoc",
    source_repo: Annotated[
        str,
        typer.Option("--source-repo", help="Provenance repository identifier."),
    ] = "cli",
    tags: Annotated[
        str | None,
        typer.Option("--tags", help="Comma-separated tags."),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Add a memory node."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    if not content.strip():
        typer.echo("error: content must not be empty", err=True)
        raise typer.Exit(code=1)

    kind_validated = _validate_kind(kind)
    if kind_validated is None:
        typer.echo("error: --kind is required", err=True)
        raise typer.Exit(code=1)

    resolved_headline = headline if headline is not None else _derive_headline(content)
    if not resolved_headline.strip():
        typer.echo("error: headline could not be derived from content; pass --headline", err=True)
        raise typer.Exit(code=1)

    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    try:
        _ensure_scope(mesh.connection, scope_id)
        _ensure_session(mesh.connection, source_session_id, source_repo)
        try:
            node = mesh.make_node(
                kind=kind_validated,
                body=content,
                headline=resolved_headline,
                scope_id=scope_id,
                source_session_id=source_session_id,
                source_repo=source_repo,
                tags=_parse_tags(tags),
            )
            node_id = mesh.add(node)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"node id: {node_id}")
    finally:
        mesh.close()


@app.command("show")
def show(
    node_id: Annotated[str, typer.Argument(help="Full node id to display.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Show a node and its incoming/outgoing edges."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    try:
        node = mesh.get(node_id)
        if node is None:
            typer.echo(f"error: node {node_id!r} not found", err=True)
            raise typer.Exit(code=1)
        out_edges = mesh.get_edges(node_id, direction="out")
        in_edges = mesh.get_edges(node_id, direction="in")

        if json_output:
            payload = {
                "node": _node_to_dict(node),
                "out_edges": [_edge_to_dict(e) for e in out_edges],
                "in_edges": [_edge_to_dict(e) for e in in_edges],
            }
            typer.echo(_dump_json(payload))
            return

        typer.echo(f"id:          {node.id}")
        typer.echo(f"kind:        {node.kind}")
        typer.echo(f"headline:    {node.headline}")
        typer.echo(f"scope_id:    {node.scope_id}")
        typer.echo(f"source:      {node.source_repo} / {node.source_session_id}")
        typer.echo(f"created_at:  {_epoch_to_iso(node.created_at)}")
        typer.echo(f"updated_at:  {_epoch_to_iso(node.updated_at)}")
        typer.echo(f"importance:  {node.importance:.2f}")
        if node.tags:
            typer.echo(f"tags:        {', '.join(node.tags)}")
        typer.echo("body:")
        for line in node.body.splitlines() or [""]:
            typer.echo(f"  {line}")

        typer.echo(f"out_edges ({len(out_edges)}):")
        for edge in out_edges:
            typer.echo(
                f"  -[{edge.relation}]-> {_short_id(edge.to_node_id)}  "
                f"(confidence={edge.confidence:.2f}, by={edge.created_by})"
            )
        typer.echo(f"in_edges ({len(in_edges)}):")
        for edge in in_edges:
            typer.echo(
                f"  <-[{edge.relation}]- {_short_id(edge.from_node_id)}  "
                f"(confidence={edge.confidence:.2f}, by={edge.created_by})"
            )
    finally:
        mesh.close()


@app.command("list")
def list_cmd(
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Restrict to one node kind."),
    ] = None,
    scope_id: Annotated[
        str | None,
        typer.Option("--scope-id", help="Restrict to a single scope id."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=1000, help="Maximum nodes to list."),
    ] = 20,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """List nodes (newest first)."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    kind_validated = _validate_kind(kind)
    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    try:
        try:
            nodes = mesh.list_nodes(kind=kind_validated, scope_id=scope_id)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        nodes = list(reversed(nodes))[:limit]

        if json_output:
            typer.echo(_dump_json([_node_to_dict(n) for n in nodes]))
            return

        if not nodes:
            typer.echo("no nodes")
            return

        for node in nodes:
            created = _epoch_to_iso(node.created_at)
            typer.echo(f"{created}  [{node.kind}]  {_short_id(node.id)}  {node.headline}")
    finally:
        mesh.close()


@app.command("delete")
def delete(
    node_id: Annotated[str, typer.Argument(help="Node id to delete.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the interactive confirmation."),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Delete a node by id."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    db_path = _resolve_db_path(db)

    if not yes:
        typer.confirm(f"delete node {node_id}?", abort=True)

    mesh = Mesh.local(db_path)
    try:
        deleted = mesh.delete(node_id)
        if not deleted:
            typer.echo(f"error: node {node_id!r} not found", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"deleted {node_id}")
    finally:
        mesh.close()


@app.command("distill")
def distill(
    session_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to a session transcript file (utf-8).",
        ),
    ],
    scope_id: Annotated[str, typer.Option("--scope-id", help="Scope id for distilled nodes.")],
    source_session_id: Annotated[
        str,
        typer.Option("--source-session-id", help="Provenance session id."),
    ],
    source_repo: Annotated[
        str,
        typer.Option("--source-repo", help="Provenance repository identifier."),
    ],
    actor: Annotated[
        str,
        typer.Option("--actor", help="Actor recorded on each audit row."),
    ] = "cli:distill",
    distiller_name: Annotated[
        str,
        typer.Option("--distiller", help="Distiller backend: heuristic or claude-cli."),
    ] = "heuristic",
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Distill a session transcript into memory nodes."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    distiller = _construct_distiller(distiller_name)

    session_text = session_file.read_text(encoding="utf-8")

    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    try:
        _ensure_scope(mesh.connection, scope_id)
        _ensure_session(mesh.connection, source_session_id, source_repo)
        try:
            nodes = mesh.distill(
                session_text,
                distiller=distiller,
                scope_id=scope_id,
                source_session_id=source_session_id,
                source_repo=source_repo,
                actor=actor,
            )
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        if not nodes:
            typer.echo("no candidates extracted")
            return

        typer.echo(f"distilled {len(nodes)} node(s):")
        for node in nodes:
            typer.echo(f"  [{node.kind}] {_short_id(node.id)}  {node.headline}")
    finally:
        mesh.close()


@app.command("stats")
def stats(
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Print store-wide counts and the latest audit timestamp."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    try:
        conn = mesh.connection

        node_total = _scalar_int(conn, "SELECT COUNT(*) FROM nodes")
        node_kinds = conn.execute(
            "SELECT kind, COUNT(*) FROM nodes GROUP BY kind ORDER BY kind"
        ).fetchall()
        edge_total = _scalar_int(conn, "SELECT COUNT(*) FROM edges")
        edge_relations = conn.execute(
            "SELECT relation, COUNT(*) FROM edges GROUP BY relation ORDER BY relation"
        ).fetchall()
        vector_total = _scalar_int(conn, "SELECT COUNT(*) FROM vector_meta")
        last_ts_row = conn.execute("SELECT MAX(timestamp) FROM audit").fetchone()
        last_ts = last_ts_row[0] if last_ts_row else None

        typer.echo(f"Nodes: {node_total}")
        for kind_name, count in node_kinds:
            typer.echo(f"  {kind_name:<11} {count}")
        typer.echo(f"Edges: {edge_total}")
        for relation, count in edge_relations:
            typer.echo(f"  {relation:<14} {count}")
        typer.echo(f"Vectors: {vector_total}")
        last_iso = _epoch_to_iso(int(last_ts)) if last_ts is not None else "never"
        typer.echo(f"Last audit event: {last_iso}")
    finally:
        mesh.close()


@app.command("audit")
def audit(
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=1000, help="Maximum audit rows to return."),
    ] = 20,
    actor: Annotated[
        str | None,
        typer.Option("--actor", help="Filter by actor."),
    ] = None,
    event_type: Annotated[
        str | None,
        typer.Option("--event-type", help="Filter by event type."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """List recent audit log rows (newest first)."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, event_type, actor, node_ids, query, result_count, metadata, timestamp "
            f"FROM audit{where} ORDER BY timestamp DESC, id DESC LIMIT ?"
        )
        params.append(limit)
        rows = mesh.connection.execute(sql, params).fetchall()

        if json_output:
            payload = [
                {
                    "id": row[0],
                    "event_type": row[1],
                    "actor": row[2],
                    "node_ids": json.loads(row[3]) if row[3] is not None else None,
                    "query": row[4],
                    "result_count": row[5],
                    "metadata": json.loads(row[6]) if row[6] is not None else None,
                    "timestamp": _epoch_to_iso(int(row[7])),
                }
                for row in rows
            ]
            typer.echo(_dump_json(payload))
            return

        if not rows:
            typer.echo("no audit events")
            return

        for row in rows:
            ts = _epoch_to_iso(int(row[7]))
            typer.echo(f"{ts}  [{row[1]}]  {row[2]}")
    finally:
        mesh.close()


@app.command("tools")
def tools(
    dialect: Annotated[
        str,
        typer.Option(
            "--dialect",
            help="Tool-schema dialect: anthropic, openai, or mcp.",
        ),
    ] = "anthropic",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write JSON to this path instead of stdout."),
    ] = None,
) -> None:
    """Emit the agent tool-schema list for the requested dialect."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    if dialect == "anthropic":
        payload: list[dict[str, Any]] = ANTHROPIC_TOOLS
    elif dialect == "openai":
        payload = OPENAI_TOOLS
    elif dialect == "mcp":
        payload = MCP_TOOLS
    else:
        typer.echo(
            f"error: unknown dialect {dialect!r}; expected 'anthropic', 'openai', or 'mcp'",
            err=True,
        )
        raise typer.Exit(code=1)

    rendered = json.dumps(payload, indent=2, sort_keys=False)

    if out is not None:
        out.write_text(rendered + "\n", encoding="utf-8")
        typer.echo(f"wrote {len(payload)} tool schema(s) to {out}")
        return

    typer.echo(rendered)


config_app = typer.Typer(
    name="config",
    help="Inspect the resolved context-mesh configuration.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)
app.add_typer(config_app, name="config")


def _render_config_toml(config: Config) -> str:
    """Render a `Config` as a TOML document. Stdlib `tomllib` only reads —
    we hand-format a compact but valid TOML output."""
    lines: list[str] = []
    for section_name, _ in _SECTION_TYPES_FOR_RENDER:
        section = getattr(config, section_name)
        lines.append(f"[{section_name}]")
        for f in _section_field_names(section):
            value = getattr(section, f)
            lines.append(f"{f} = {_toml_format_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _section_field_names(section: Any) -> list[str]:
    return [f.name for f in dataclasses.fields(section)]


def _toml_format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_quote_string(value)
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _toml_quote_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


_SECTION_TYPES_FOR_RENDER: tuple[tuple[str, str], ...] = (
    ("storage", "StorageConfig"),
    ("embeddings", "EmbeddingsConfig"),
    ("distillation", "DistillationConfig"),
    ("retrieval", "RetrievalConfig"),
    ("sync", "SyncConfig"),
    ("scopes", "ScopesConfig"),
    ("observability", "ObservabilityConfig"),
    ("adapters", "AdaptersConfig"),
)


@config_app.callback()
def _config_root(
    ctx: typer.Context,
    use_global: Annotated[
        bool,
        typer.Option("--global", help="Load only ~/.context-mesh/config.toml plus env."),
    ] = False,
) -> None:
    """Print the resolved configuration as TOML when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        ctx.obj = {"use_global": use_global}
        return
    configure_logging(json_output=False)
    try:
        cfg = load_config(use_global=use_global)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_render_config_toml(cfg).rstrip())


@config_app.command("get")
def config_get(
    ctx: typer.Context,
    key: Annotated[
        str,
        typer.Argument(help="Dotted config key (e.g. 'storage.path')."),
    ],
) -> None:
    """Print a single resolved config value."""
    configure_logging(json_output=False)
    use_global = bool((ctx.obj or {}).get("use_global", False))
    try:
        cfg = load_config(use_global=use_global)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    parts = key.split(".")
    if len(parts) != 2:
        typer.echo(
            f"error: key must be of the form 'section.field'; got {key!r}",
            err=True,
        )
        raise typer.Exit(code=1)
    section_name, field_name = parts
    section = getattr(cfg, section_name, None)
    if section is None or not dataclasses.is_dataclass(section) or section_name == "source_paths":
        typer.echo(f"error: unknown config section {section_name!r}", err=True)
        raise typer.Exit(code=1)
    if field_name not in {f.name for f in dataclasses.fields(section)}:
        typer.echo(f"error: unknown config key {key!r}", err=True)
        raise typer.Exit(code=1)
    value = getattr(section, field_name)
    if isinstance(value, bool):
        typer.echo("true" if value else "false")
    else:
        typer.echo(str(value))


@config_app.command("sources")
def config_sources(ctx: typer.Context) -> None:
    """List the config files that were merged, in load order (low -> high)."""
    configure_logging(json_output=False)
    use_global = bool((ctx.obj or {}).get("use_global", False))
    try:
        cfg = load_config(use_global=use_global)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not cfg.source_paths:
        typer.echo("(defaults only; no config files merged)")
        return
    for src in cfg.source_paths:
        typer.echo(str(src))


@app.command("serve")
def serve(
    host: Annotated[
        str,
        typer.Option("--host", help="Bind address. Defaults to 127.0.0.1 (local-only)."),
    ] = DEFAULT_HOST,
    port: Annotated[
        int,
        typer.Option("--port", min=0, max=65535, help="Bind port (0 = OS-assigned)."),
    ] = DEFAULT_PORT,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db. Overrides $CONTEXT_MESH_DB."),
    ] = None,
) -> None:
    """Run the context-mesh HTTP server (stdlib http.server, single bearer token)."""
    configure_logging(json_output=False)
    logger = get_logger("context_mesh.cli")

    db_path = _resolve_db_path(db)
    server = MeshServer(db_path=db_path, host=host, port=port)

    typer.echo(f"context-mesh serve listening on {server.url}")
    typer.echo(f"token loaded from {TOKEN_FILE}")
    typer.echo(f"db: {db_path}")

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("serve_keyboard_interrupt")
        server.stop()


def _construct_distiller(name: str) -> Distiller:
    if name == "heuristic":
        return HeuristicDistiller()
    if name == "claude-cli":
        return ClaudeCliDistiller()
    typer.echo(
        f"error: unknown distiller {name!r}; expected 'heuristic' or 'claude-cli'",
        err=True,
    )
    raise typer.Exit(code=1)


def _construct_adapter(
    name: str,
    mesh: Mesh,
    repo: Path,
    *,
    branch: str,
) -> SourceAdapter:
    if name == "agent-memory":
        return AgentMemoryAdapter(mesh, repo)
    if name == "entire":
        return EntireAdapter(mesh, repo, branch=branch)
    typer.echo(
        f"error: unknown adapter {name!r}; expected 'agent-memory' or 'entire'",
        err=True,
    )
    raise typer.Exit(code=1)


def _emit_sync_text(result: SyncResult, *, would_ingest: list[SessionReference] | None) -> None:
    if result.dry_run:
        refs = would_ingest or []
        if not refs:
            typer.echo(f"adapter: {result.adapter_name} (dry-run)")
            typer.echo("would ingest: 0 reference(s)")
            return
        typer.echo(f"adapter: {result.adapter_name} (dry-run)")
        typer.echo(f"would ingest: {len(refs)} reference(s)")
        for ref in refs:
            typer.echo(f"  {ref.source_id}  repo={ref.repo}  agent={ref.agent}")
        return

    typer.echo(f"adapter: {result.adapter_name}")
    typer.echo(f"discovered:         {result.discovered}")
    typer.echo(f"fetched:            {result.fetched}")
    typer.echo(f"memory_nodes_added: {result.memory_nodes_added}")
    typer.echo(f"skipped:            {len(result.skipped)}")
    for skip in result.skipped:
        typer.echo(f"  - {skip.source_id} [{skip.stage}] {skip.reason}")
    typer.echo(f"cursor:             {result.cursor}")
    typer.echo(f"state_keys_count:   {result.state_keys_count}")
    typer.echo(f"elapsed_ms:         {result.elapsed_ms}")


def _emit_sync_json(result: SyncResult, *, would_ingest: list[SessionReference] | None) -> None:
    payload: dict[str, Any] = {
        "adapter_name": result.adapter_name,
        "discovered": result.discovered,
        "fetched": result.fetched,
        "memory_nodes_added": result.memory_nodes_added,
        "skipped": [
            {"source_id": s.source_id, "stage": s.stage, "reason": s.reason} for s in result.skipped
        ],
        "cursor": result.cursor,
        "state_keys_count": result.state_keys_count,
        "elapsed_ms": result.elapsed_ms,
        "dry_run": result.dry_run,
    }
    if result.dry_run:
        refs = would_ingest or []
        payload["would_ingest"] = [
            {"source_id": r.source_id, "repo": r.repo, "agent": r.agent} for r in refs
        ]
    typer.echo(_dump_json(payload))


@app.command("sync")
def sync_cmd(
    adapter_name: Annotated[
        str,
        typer.Argument(help="Adapter to run: 'agent-memory' or 'entire'.", metavar="ADAPTER"),
    ],
    repo: Annotated[
        Path | None,
        typer.Option("--repo", help="Directory the adapter operates on. Default: cwd."),
    ] = None,
    branch: Annotated[
        str,
        typer.Option("--branch", help="Git branch (entire adapter only)."),
    ] = "entire/checkpoints/v1",
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=10_000, help="Max references to process."),
    ] = 100,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Discover-only; do not fetch or persist."),
    ] = False,
    distiller_name: Annotated[
        str,
        typer.Option("--distiller", help="Distiller backend: heuristic or claude-cli."),
    ] = "heuristic",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON summary."),
    ] = False,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Path to memory.db."),
    ] = None,
) -> None:
    """Run one sync pass for ADAPTER, distilling new sessions into memory."""
    configure_logging(json_output=False)
    get_logger("context_mesh.cli")

    repo_resolved = (repo or Path.cwd()).resolve()
    if not repo_resolved.is_dir():
        typer.echo(
            f"error: --repo path not found or not a directory: {repo_resolved}",
            err=True,
        )
        raise typer.Exit(code=1)
    db_path = _resolve_db_path(db)
    mesh = Mesh.local(db_path)
    would_ingest: list[SessionReference] | None = None
    try:
        adapter = _construct_adapter(adapter_name, mesh, repo_resolved, branch=branch)
        distiller = _construct_distiller(distiller_name)
        embedder = DeterministicEmbeddingProvider()
        if dry_run:
            would_ingest = list(adapter.discover())[:limit]
        result = mesh.sync(
            adapter=adapter,
            distiller=distiller,
            embedder=embedder,
            limit=limit,
            dry_run=dry_run,
            actor="cli:sync",
        )
    finally:
        mesh.close()

    if json_output:
        _emit_sync_json(result, would_ingest=would_ingest)
    else:
        _emit_sync_text(result, would_ingest=would_ingest)


def _scalar_int(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def _ensure_scope(conn: sqlite3.Connection, scope_id: str) -> None:
    row = conn.execute("SELECT 1 FROM scopes WHERE id = ?", (scope_id,)).fetchone()
    if row is not None:
        return
    conn.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, 'private', ?)",
        (scope_id, scope_id, int(time.time())),
    )
    conn.commit()


def _ensure_session(conn: sqlite3.Connection, session_id: str, repo: str) -> None:
    row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is not None:
        return
    conn.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, 'cli', ?)",
        (session_id, repo, int(time.time())),
    )
    conn.commit()
