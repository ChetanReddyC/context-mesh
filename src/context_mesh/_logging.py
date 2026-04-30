"""Structlog configuration for context-mesh.

This module wires structlog as the project's logging backend and bridges
stdlib `logging.getLogger(__name__)` calls (used by storage/migrations) so they
format identically. It is idempotent: repeated calls to `configure_logging`
overwrite the previous configuration without doubling processors.

Nothing in the library calls `configure_logging` automatically. The CLI entry
point (Unit 4) is the sole caller for production runs. Tests rely on stdlib
`caplog` and do not require structlog to be configured.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

_VALID_LEVELS: Final[frozenset[str]] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and the stdlib root logger.

    Args:
        level: Stdlib log-level name. Case-insensitive.
        json_output: When True, emit one JSON object per record (production /
            CI / audit pipelines). When False, emit the structlog dev console
            renderer (local CLI debugging).

    Calling this function more than once is safe: each call overwrites the
    previous structlog configuration and replaces handlers on the root logger.
    """
    normalized = level.upper()
    if normalized not in _VALID_LEVELS:
        raise ValueError(f"invalid log level {level!r}; expected one of {sorted(_VALID_LEVELS)}")
    numeric_level = logging.getLevelName(normalized)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setLevel(numeric_level)
    root.addHandler(handler)
    root.setLevel(numeric_level)


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a structlog stdlib BoundLogger for the given name."""
    proxy = structlog.stdlib.get_logger(name) if name else structlog.stdlib.get_logger()
    bound: BoundLogger = proxy.bind()
    return bound


__all__ = ["configure_logging", "get_logger"]
