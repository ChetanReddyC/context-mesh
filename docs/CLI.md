# CLI Reference

The `context-mesh` command-line tool. This document is a flat reference for
every command shipped in v1.0; for the conceptual overview see
`docs/API_DESIGN.md`.

Run `context-mesh --help` for the auto-generated short list, or
`context-mesh <command> --help` for any command's detailed help.

## Conventions

- `--db <path>` — database file. Resolution order on every command that
  takes it: explicit `--db <path>` ▸ `$CONTEXT_MESH_DB` ▸ `storage.path`
  from a merged config file ▸ `./.context-mesh/memory.db`.
- `--json` — wherever offered, switches output to machine-readable JSON.
- Exit codes: `0` success, `1` user error / not found / validation
  failure, `2` Typer-level argument parsing error.

---

## `context-mesh init [<path>]`

Initialize a context-mesh store. Creates `<path>/.context-mesh/memory.db`,
runs the migration runner to install the v1 schema, writes a default
`config.toml`, and (in non-global mode) appends `.context-mesh/` to
`<path>/.gitignore`. Audits the operation as `event_type='init'`.

| Flag | Description |
| --- | --- |
| `<path>` | Project root. Defaults to the current working directory. |
| `--global` | Initialize at `~/.context-mesh/` instead. Skips `.gitignore` updates. |
| `--force` | Overwrite an existing `memory.db` (and its `-wal` / `-shm` siblings). |

Example: `context-mesh init ./payments`

---

## `context-mesh add <content>`

Insert a memory node. Always `--kind`-typed; headline is auto-derived
from the first line of `<content>` when omitted. Auto-creates the scope
and session rows if they don't exist yet. Audits as `add`.

| Flag | Default | Description |
| --- | --- | --- |
| `<content>` | required | Body text (free-form). |
| `--kind` | required | `episodic` / `semantic` / `procedural`. |
| `--headline` | first line, ≤120 chars | One-line title. |
| `--scope-id` | `cli` | Scope id for federation/visibility. |
| `--source-session-id` | `cli:adhoc` | Provenance session id. |
| `--source-repo` | `cli` | Provenance repository identifier. |
| `--tags` | none | Comma-separated tags. |
| `--db` | resolved | Override the database path. |

Example: `context-mesh add "always re-verify webhook timestamps" --kind semantic --tags webhooks,security`

---

## `context-mesh show <node-id>`

Pretty-prints a node and its incoming/outgoing edges. Pass `--json` for
the structured payload.

| Flag | Description |
| --- | --- |
| `<node-id>` | Full node id. |
| `--json` | Emit structured JSON instead of the formatted view. |
| `--db` | Override the database path. |

---

## `context-mesh list`

Newest-first listing of nodes.

| Flag | Default | Description |
| --- | --- | --- |
| `--kind` | none | Restrict to one node kind. |
| `--scope-id` | none | Restrict to a single scope id. |
| `--limit` | `20` | Max nodes to print (`1..1000`). |
| `--json` | off | Emit structured JSON. |
| `--db` | resolved | Override the database path. |

---

## `context-mesh delete <node-id>`

Hard-deletes a node by id (and its vector). Confirms interactively unless
`--yes`/`-y` is passed. Audits as `delete`.

| Flag | Description |
| --- | --- |
| `--yes`, `-y` | Skip the interactive confirmation. |
| `--db` | Override the database path. |

---

## `context-mesh search <query>`

Hybrid memory search. Embeds `<query>` with the deterministic provider,
runs vector kNN ▸ 1-hop graph expansion ▸ composite ranking
(semantic 0.50 / relevance 0.20 / recency 0.10 / importance 0.10 /
usage 0.10) ▸ quality gate. Audits as `retrieve`.

| Flag | Default | Description |
| --- | --- | --- |
| `<query>` | required | Free-text query. |
| `--kind` | none | Restrict to one node kind. |
| `--scope-id` | none | Restrict to a single scope id. |
| `--limit` | `5` | Max results (`1..100`). |
| `--json` | off | Emit structured cluster JSON. |
| `--db` | resolved | Override the database path. |

---

## `context-mesh distill <session-file>`

Distills a session transcript into one or more memory nodes. Required
flags below establish provenance. Default backend is `heuristic`
(network-free, deterministic); `claude-cli` calls the local `claude` CLI
with a strict-JSON prompt and falls back to heuristic on any failure.

| Flag | Default | Description |
| --- | --- | --- |
| `<session-file>` | required | Path to a UTF-8 transcript file. |
| `--scope-id` | required | Scope id for distilled nodes. |
| `--source-session-id` | required | Provenance session id. |
| `--source-repo` | required | Provenance repository identifier. |
| `--actor` | `cli:distill` | Audit-row actor. |
| `--distiller` | `heuristic` | `heuristic` or `claude-cli`. |
| `--db` | resolved | Override the database path. |

---

## `context-mesh stats`

Print store-wide counts (nodes, edges, vectors, by kind/relation) and
the latest audit timestamp. Read-only, single connection, safe to run
alongside other commands.

| Flag | Description |
| --- | --- |
| `--db` | Override the database path. |

---

## `context-mesh audit`

List recent audit-log rows, newest first. Filter by actor or event type.

| Flag | Default | Description |
| --- | --- | --- |
| `--limit` | `20` | Max rows (`1..1000`). |
| `--actor` | none | Filter by actor (e.g. `cli:search`). |
| `--event-type` | none | Filter by event type (e.g. `add`, `retrieve`). |
| `--json` | off | Emit structured JSON. |
| `--db` | resolved | Override the database path. |

---

## `context-mesh tools`

Emit the agent tool-schema list for the requested dialect.

| Flag | Default | Description |
| --- | --- | --- |
| `--dialect` | `anthropic` | `anthropic` / `openai` / `mcp`. |
| `--out` | stdout | Write JSON to this path instead of stdout. |

Example: `context-mesh tools --dialect openai --out tools.json`

---

## `context-mesh serve`

Run the context-mesh HTTP server (stdlib `ThreadingHTTPServer`). The
server exposes the five agent-tool endpoints plus `/health`. See
`docs/HTTP_API.md` for the protocol reference.

| Flag | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address (local-only by default). |
| `--port` | `7421` | Bind port (`0` = OS-assigned). |
| `--db` | resolved | Override the database path. |

The bearer token is loaded from (or generated into)
`~/.context-mesh/token` at startup. Mode is `0o600` on POSIX.

---

## `context-mesh config`

Inspect the resolved configuration. Read-only in v1; `config set` is
deferred to a future release.

| Subcommand | Description |
| --- | --- |
| `config` | Print the fully-resolved config as TOML to stdout. |
| `config get <section.field>` | Print a single value (e.g. `storage.path`). |
| `config sources` | List the config files merged, in load order (low ▸ high). |

The `--global` flag (on the parent group) skips the project-local file
and loads only `~/.context-mesh/config.toml` plus env. Resolution order
is documented in `docs/CONFIG.md`.

Example: `context-mesh config get embeddings.dimensions`

---

## `context-mesh --version`

Print the installed package version and exit.
