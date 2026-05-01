# Configuration Reference

`context-mesh` reads its configuration from TOML files via
`context_mesh.config.load_config`. The loader is exposed to the CLI as
`context-mesh config`.

## Resolution Order

Low precedence ▸ high precedence:

1. **Defaults** — the dataclass field defaults in `context_mesh.config`.
2. **Global config** — `~/.context-mesh/config.toml` (optional).
3. **Project config** — `./.context-mesh/config.toml` (optional). Skipped
   when `load_config(use_global=True)` or `--global` is used.
4. **Environment variables** — three keys override specific fields:
   - `CONTEXT_MESH_DB` → `storage.path`
   - `CONTEXT_MESH_LOG_LEVEL` → `observability.log_level`
   - `CONTEXT_MESH_HUB_URL` → `sync.hub_url`

`load_config(path=...)` lets callers point at a non-default project
directory; the loader looks for `<path>/config.toml`.

Unknown sections and unknown keys log a `context_mesh.config` warning
but **do not fail** the load (forward compatibility). Type mismatches
and invalid `Literal` enum values raise `ConfigError`.

## Sections

### `[storage]`

| Key | Type | Default | Description | Env override |
| --- | --- | --- | --- | --- |
| `path` | string | `.context-mesh/memory.db` | Path to the SQLite store. | `CONTEXT_MESH_DB` |
| `backup_dir` | string | `.context-mesh/backups` | Directory for future backup tooling. | — |

### `[embeddings]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `provider` | enum | `huggingface` | `huggingface` or `deterministic`. |
| `model` | string | `all-MiniLM-L6-v2` | Model identifier. |
| `dimensions` | int | `384` | Embedding dimensionality (must match the schema). |
| `api_key_env` | string | `HF_API_KEY` | Env var to read the provider API key from. |

### `[distillation]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `provider` | enum | `claude-cli` | `claude-cli` or `heuristic`. |
| `model` | string | `sonnet` | Model alias passed to the Claude CLI. |
| `fallback_to_heuristic` | bool | `true` | Fall back to the heuristic distiller on CLI failure. |

### `[retrieval]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `default_limit` | int | `5` | Default `limit` when callers don't specify. |
| `quality_threshold` | float | `40.0` | Minimum composite score (0..100) to keep a result. |
| `content_score_threshold` | float | `30.0` | Minimum semantic score (0..100) for a candidate to enter the cluster. |
| `token_budget` | int | `500` | Soft cap on returned-cluster tokens (advisory in v1). |

### `[sync]`

| Key | Type | Default | Description | Env override |
| --- | --- | --- | --- | --- |
| `hub_url` | string | `""` | Federation hub URL (Phase 6+). | `CONTEXT_MESH_HUB_URL` |
| `token_env` | string | `CONTEXT_MESH_TOKEN` | Env var to read the hub bearer token from. | — |
| `sync_interval_seconds` | int | `600` | Background sync interval. | — |
| `auto_pull_on_session_start` | bool | `true` | Pull from hub at session start. | — |

### `[scopes]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `default_scope` | enum | `private` | `private` / `team` / `org`. |

### `[observability]`

| Key | Type | Default | Description | Env override |
| --- | --- | --- | --- | --- |
| `log_level` | enum | `info` | `debug` / `info` / `warning` / `error`. | `CONTEXT_MESH_LOG_LEVEL` |
| `log_path` | string | `.context-mesh/log.jsonl` | JSONL log file. | — |
| `metrics_enabled` | bool | `false` | Reserved for future metrics export. | — |
| `otel_endpoint` | string | `""` | Reserved for OTLP export. | — |

### `[adapters]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `entire_enabled` | bool | `false` | Enable the Entire adapter (Phase 5). |
| `agent_memory_enabled` | bool | `false` | Enable the agent-memory adapter (Phase 5). |

In v1 these flags are loaded into the `AdaptersConfig` dataclass but
are **not yet consulted** by `Mesh.sync` or the `context-mesh sync`
command. Sync is driven explicitly: `context-mesh sync <adapter>` on
the CLI, or `Mesh.sync(adapter=AgentMemoryAdapter(...) | EntireAdapter(...))`
in the library. The flags reserve the surface for a future
auto-registration step that will scan registered adapters and run sync
passes for those whose `<name>_enabled` flag is `true`. Until that
ships, treat the flags as advisory only. See `docs/ADAPTERS.md` for
the full source-adapter reference.

## Programmatic Access

```python
from context_mesh.config import load_config
from context_mesh.api import Mesh

cfg = load_config()
print(cfg.storage.path)      # ".context-mesh/memory.db" or whatever override
print(cfg.source_paths)      # tuple of files merged, low ▸ high precedence

with Mesh.from_config(cfg) as mesh:
    ...                      # opens Mesh.local(cfg.storage.path)
```

The `Config` dataclass is immutable (`frozen=True, slots=True`); each
section is its own immutable dataclass. To compose overrides at runtime,
use `dataclasses.replace`.
