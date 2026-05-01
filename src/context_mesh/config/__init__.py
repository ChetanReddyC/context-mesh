"""Configuration loader for `context-mesh`.

Three-layer resolution:

1. Hardcoded defaults (the dataclass field defaults).
2. Global config at `~/.context-mesh/config.toml` (optional).
3. Project config at `./.context-mesh/config.toml` (optional, overrides global).
4. A small set of environment-variable overrides (highest precedence).

Each resolved `Config` is immutable. The `source_paths` tuple records the
files that were merged, in increasing-precedence order, for diagnostics.
"""

from __future__ import annotations

from context_mesh.config.loader import (
    DEFAULT_GLOBAL_CONFIG_PATH,
    DEFAULT_PROJECT_CONFIG_PATH,
    AdaptersConfig,
    Config,
    ConfigError,
    DistillationConfig,
    EmbeddingsConfig,
    ObservabilityConfig,
    RetrievalConfig,
    ScopesConfig,
    StorageConfig,
    SyncConfig,
    load_config,
)

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
