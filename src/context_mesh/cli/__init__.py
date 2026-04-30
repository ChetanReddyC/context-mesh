"""Typer CLI app for context-mesh.

Phase 0 ships exactly one command (`init`) plus a `--version` callback.
Additional commands land in Phase 4 (search, distill, audit, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from context_mesh import __version__
from context_mesh._audit import log as audit_log
from context_mesh._logging import configure_logging, get_logger
from context_mesh.storage import SqliteVecBackend

app = typer.Typer(
    name="context-mesh",
    help="Federated, agent-native memory for AI coding agents.",
    no_args_is_help=True,
    add_completion=False,
)

_GITIGNORE_LINE = ".context-mesh/"
_CONFIG_TEMPLATE_PACKAGE = "context_mesh.cli.templates"
_CONFIG_TEMPLATE_FILENAME = "config.toml"


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
