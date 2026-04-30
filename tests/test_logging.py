"""Tests for context_mesh._logging."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest
import structlog

from context_mesh._logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Callable


def _capture_emission(emit_callable: Callable[[], None]) -> str:
    """Run emit_callable after swapping root's handler to a StringIO sink."""
    buf = io.StringIO()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(buf)
    handler.setLevel(root.level)
    root.addHandler(handler)
    emit_callable()
    return buf.getvalue()


def test_configure_logging_default_emits_json() -> None:
    configure_logging()
    output = _capture_emission(lambda: get_logger("test").info("evt", k="v"))
    parsed = json.loads(output.strip())
    assert parsed["event"] == "evt"
    for key in ("timestamp", "level", "event", "logger", "k"):
        assert key in parsed


def test_configure_logging_pretty_mode_is_not_json() -> None:
    configure_logging(json_output=False)
    output = _capture_emission(lambda: get_logger("test").info("evt", k="v"))
    with pytest.raises(ValueError):
        json.loads(output.strip())


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    assert len(logging.getLogger().handlers) == 1
    configure_logging()
    assert len(logging.getLogger().handlers) == 1
    configure_logging()
    assert len(logging.getLogger().handlers) == 1

    output = _capture_emission(lambda: get_logger("test").info("once"))
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event"] == "once"


def test_get_logger_returns_bound_logger() -> None:
    configure_logging()
    logger = get_logger("ctx")
    assert isinstance(logger, structlog.stdlib.BoundLogger)
    logger.info("hello")


def test_configure_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        configure_logging(level="WARN")
