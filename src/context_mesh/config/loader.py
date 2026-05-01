"""Configuration dataclasses and resolution logic.

The schema mirrors `src/context_mesh/cli/templates/config.toml` exactly. Every
key in the template has a corresponding field on one of the section
dataclasses. The dataclass defaults are the canonical defaults; the template
is a developer-friendly mirror, not the source of truth.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Final, Literal, get_args, get_origin, get_type_hints

from context_mesh._logging import get_logger

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


EmbeddingsProvider = Literal["huggingface", "deterministic"]
DistillationProvider = Literal["claude-cli", "heuristic"]
ScopeLevel = Literal["private", "team", "org"]
LogLevel = Literal["debug", "info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class StorageConfig:
    path: str = ".context-mesh/memory.db"
    backup_dir: str = ".context-mesh/backups"


@dataclass(frozen=True, slots=True)
class EmbeddingsConfig:
    provider: EmbeddingsProvider = "huggingface"
    model: str = "all-MiniLM-L6-v2"
    dimensions: int = 384
    api_key_env: str = "HF_API_KEY"


@dataclass(frozen=True, slots=True)
class DistillationConfig:
    provider: DistillationProvider = "claude-cli"
    model: str = "sonnet"
    fallback_to_heuristic: bool = True


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    default_limit: int = 5
    quality_threshold: float = 40.0
    content_score_threshold: float = 30.0
    token_budget: int = 500


@dataclass(frozen=True, slots=True)
class SyncConfig:
    hub_url: str = ""
    token_env: str = "CONTEXT_MESH_TOKEN"
    sync_interval_seconds: int = 600
    auto_pull_on_session_start: bool = True


@dataclass(frozen=True, slots=True)
class ScopesConfig:
    default_scope: ScopeLevel = "private"


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    log_level: LogLevel = "info"
    log_path: str = ".context-mesh/log.jsonl"
    metrics_enabled: bool = False
    otel_endpoint: str = ""


@dataclass(frozen=True, slots=True)
class AdaptersConfig:
    entire_enabled: bool = False
    agent_memory_enabled: bool = False


@dataclass(frozen=True, slots=True)
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    distillation: DistillationConfig = field(default_factory=DistillationConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    scopes: ScopesConfig = field(default_factory=ScopesConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    adapters: AdaptersConfig = field(default_factory=AdaptersConfig)
    source_paths: tuple[Path, ...] = ()


# ---------------------------------------------------------------------------
# Errors and constants
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when a config file is malformed or has invalid values."""


DEFAULT_GLOBAL_CONFIG_PATH: Final[Path] = Path.home() / ".context-mesh" / "config.toml"


def _default_project_config_path() -> Path:
    return Path.cwd() / ".context-mesh" / "config.toml"


DEFAULT_PROJECT_CONFIG_PATH: Final[Path] = _default_project_config_path()
"""Path of the project-local config file, evaluated at import time.

The loader recomputes the project path from the current CWD on each call,
so tests that `monkeypatch.chdir` work correctly. This module-level
constant is provided for diagnostics and documentation.
"""


_LOGGER_NAME: Final[str] = "context_mesh.config"

_KNOWN_SECTIONS: Final[frozenset[str]] = frozenset(
    {
        "storage",
        "embeddings",
        "distillation",
        "retrieval",
        "sync",
        "scopes",
        "observability",
        "adapters",
    }
)


# Map of (section_name, dataclass_type) pairs. Order is the canonical TOML
# section order — mirrors the template file.
_SECTION_TYPES: Final[tuple[tuple[str, type], ...]] = (
    ("storage", StorageConfig),
    ("embeddings", EmbeddingsConfig),
    ("distillation", DistillationConfig),
    ("retrieval", RetrievalConfig),
    ("sync", SyncConfig),
    ("scopes", ScopesConfig),
    ("observability", ObservabilityConfig),
    ("adapters", AdaptersConfig),
)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None, *, use_global: bool = False) -> Config:
    """Load a fully-resolved `Config`.

    Resolution order (low precedence -> high):

    1. Hardcoded defaults (dataclass field defaults).
    2. Global config: `~/.context-mesh/config.toml`, if it exists.
    3. Project config: `<path>/config.toml` (if `path` given) or
       `./.context-mesh/config.toml`. Skipped when `use_global=True`.
    4. Environment variable overrides (`CONTEXT_MESH_DB`,
       `CONTEXT_MESH_LOG_LEVEL`, `CONTEXT_MESH_HUB_URL`).

    Raises `ConfigError` on malformed TOML or invalid values.
    """
    sources: list[Path] = []
    config = Config()

    global_path = DEFAULT_GLOBAL_CONFIG_PATH
    if global_path.is_file():
        config = _merge_file(config, global_path)
        sources.append(global_path)

    if not use_global:
        project_path = (
            (path / "config.toml") if path is not None else _default_project_config_path()
        )
        if project_path.is_file():
            config = _merge_file(config, project_path)
            sources.append(project_path)

    config = _apply_env_overrides(config)

    return replace(config, source_paths=tuple(sources))


def _merge_file(base: Config, file_path: Path) -> Config:
    """Parse a TOML file and return a new `Config` with its values merged."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config file {file_path}: {exc}") from exc

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {file_path}: {exc}") from exc

    if not isinstance(raw, dict):  # pragma: no cover - tomllib always returns dict
        raise ConfigError(f"top level of {file_path} must be a TOML table")

    logger = get_logger(_LOGGER_NAME)
    for section_name in raw:
        if section_name not in _KNOWN_SECTIONS:
            logger.warning(
                "config_unknown_section",
                section=section_name,
                path=str(file_path),
            )

    updated = base
    for section_name, section_type in _SECTION_TYPES:
        section_raw = raw.get(section_name)
        if section_raw is None:
            continue
        if not isinstance(section_raw, dict):
            raise ConfigError(
                f"[{section_name}] in {file_path} must be a TOML table, "
                f"got {type(section_raw).__name__}"
            )
        current_section = getattr(updated, section_name)
        merged_section = _merge_section(
            current_section,
            section_type,
            section_raw,
            section_name=section_name,
            file_path=file_path,
        )
        updated = replace(updated, **{section_name: merged_section})

    return updated


def _merge_section(
    current: Any,
    section_type: type,
    raw: dict[str, Any],
    *,
    section_name: str,
    file_path: Path,
) -> Any:
    """Merge `raw` into a section dataclass, validating each field."""
    valid_field_names = {f.name for f in fields(section_type)}
    type_hints = get_type_hints(section_type)
    logger = get_logger(_LOGGER_NAME)

    overrides: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in valid_field_names:
            logger.warning(
                "config_unknown_key",
                section=section_name,
                key=key,
                path=str(file_path),
            )
            continue
        coerced = _coerce_value(
            value,
            type_hints[key],
            section_name=section_name,
            field_name=key,
            file_path=file_path,
        )
        overrides[key] = coerced

    return replace(current, **overrides)


def _coerce_value(
    value: Any,
    expected_type: Any,
    *,
    section_name: str,
    field_name: str,
    file_path: Path,
) -> Any:
    """Validate `value` against `expected_type` (a dataclass field annotation).

    Handles `Literal[...]` (enum-style strings) and the four primitive
    scalar types we use: `str`, `int`, `float`, `bool`. Returns the value
    unchanged on success; raises `ConfigError` on mismatch.
    """
    where = f"[{section_name}].{field_name} in {file_path}"

    origin = get_origin(expected_type)
    if origin is Literal:
        allowed = get_args(expected_type)
        if value not in allowed:
            raise ConfigError(f"{where} must be one of {sorted(map(str, allowed))}; got {value!r}")
        return value

    if expected_type is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where} must be a boolean; got {type(value).__name__}")
        return value
    if expected_type is int:
        # tomllib parses both ints and floats; accept ints only here.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where} must be an integer; got {type(value).__name__}")
        return value
    if expected_type is float:
        # Accept ints or floats; promote to float.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} must be a number; got {type(value).__name__}")
        return float(value)
    if expected_type is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where} must be a string; got {type(value).__name__}")
        return value

    # Unknown annotation type: defensively pass through.
    return value


def _apply_env_overrides(base: Config) -> Config:
    """Apply the small set of environment-variable overrides."""
    db_env = os.environ.get("CONTEXT_MESH_DB")
    log_env = os.environ.get("CONTEXT_MESH_LOG_LEVEL")
    hub_env = os.environ.get("CONTEXT_MESH_HUB_URL")

    updated = base
    if db_env:
        updated = replace(updated, storage=replace(updated.storage, path=db_env))
    if log_env is not None:
        normalized = log_env.strip().lower()
        if normalized not in get_args(LogLevel):
            raise ConfigError(
                f"CONTEXT_MESH_LOG_LEVEL must be one of "
                f"{sorted(get_args(LogLevel))}; got {log_env!r}"
            )
        updated = replace(
            updated,
            observability=replace(updated.observability, log_level=normalized),  # type: ignore[arg-type]
        )
    if hub_env is not None:
        updated = replace(updated, sync=replace(updated.sync, hub_url=hub_env))
    return updated


__all__ = [
    "DEFAULT_GLOBAL_CONFIG_PATH",
    "DEFAULT_PROJECT_CONFIG_PATH",
    "AdaptersConfig",
    "Config",
    "ConfigError",
    "DistillationConfig",
    "EmbeddingsConfig",
    "ObservabilityConfig",
    "RetrievalConfig",
    "ScopesConfig",
    "StorageConfig",
    "SyncConfig",
    "load_config",
]
