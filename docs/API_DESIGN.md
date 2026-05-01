# API Design

`context-mesh` exposes three surfaces:

1. **Agent tools** — what an AI agent calls during a session.
2. **CLI** — what a developer types in their terminal.
3. **Library API** — what other software imports and calls programmatically.

All three surfaces are built on top of the same internal core. The core handles the actual logic; the surfaces are thin wrappers.

---

## 1. Agent Tools

Five tools exposed to agents via tool-use. Documented earlier in `docs/RETRIEVAL_DESIGN.md`; here we specify the exact signatures.

### `search_team_memory`

```
search_team_memory(
  query: string,
  kind?: "episodic" | "semantic" | "procedural" | "all",
  scope?: "private" | "team" | "org" | "all",
  limit?: integer  // default 5, max 20
) → MemoryCluster
```

Returns a `MemoryCluster` with:
- `nodes`: list of summarized memories
- `edges`: edges connecting the returned nodes
- `cluster_confidence`: aggregate confidence score
- `result_count`: number of nodes returned

### `drill_down_memory`

```
drill_down_memory(
  node_id: string
) → FullNode
```

Returns the complete content of a node:
- `headline`, `summary`, `body`
- All structured fields (decisions, failed_approaches, warnings, etc.)
- Source session reference
- Related edges (incoming and outgoing)
- Confidence and usage stats

### `add_memory`

```
add_memory(
  content: string,           // free-form description
  kind: "episodic" | "semantic" | "procedural",
  scope?: string,            // default: "private"
  tags?: string[],
  related_to?: string[]      // optional list of related node IDs
) → AddMemoryResponse
```

Returns:
- `node_id`: the new node's ID
- `auto_inferred_edges`: edges the system inferred from `content`
- `requires_review`: boolean flag (true for agent-added, until human approves)

### `mark_memory_used`

```
mark_memory_used(
  node_id: string,
  helpful: boolean,
  notes?: string  // optional explanation
) → void
```

Updates the audit log. Feeds into ranking signals.

### `find_contradictions`

```
find_contradictions(
  content: string,
  kind?: "semantic" | "procedural"  // contradictions are usually semantic
) → MemoryNode[]
```

Returns a list of nodes that contradict the proposed content. Used proactively before the agent commits to a non-trivial decision.

---

## 2. CLI

The `context-mesh` command-line interface. The full reference for shipped
commands lives in `docs/CLI.md`; the table below is the short-form summary.

### Shipped in v1.0

```bash
# Setup
context-mesh init [<path>] [--global] [--force]

# Memory CRUD
context-mesh add <content> --kind <kind> [--headline <h>] [--scope-id <s>] \
    [--source-session-id <id>] [--source-repo <r>] [--tags <a,b,c>] [--db <path>]
context-mesh show <node-id> [--json] [--db <path>]
context-mesh list [--kind <kind>] [--scope-id <s>] [--limit <n>] [--json] [--db <path>]
context-mesh delete <node-id> [--yes] [--db <path>]

# Retrieval and distillation
context-mesh search "<query>" [--kind <kind>] [--scope-id <s>] [--limit <n>] [--json] [--db <path>]
context-mesh distill <session-file> --scope-id <s> --source-session-id <id> \
    --source-repo <r> [--actor <a>] [--distiller heuristic|claude-cli] [--db <path>]

# Inspection
context-mesh stats [--db <path>]
context-mesh audit [--limit <n>] [--actor <a>] [--event-type <e>] [--json] [--db <path>]

# Agent surfaces
context-mesh tools [--dialect anthropic|openai|mcp] [--out <path>]
context-mesh serve [--host <h>] [--port <p>] [--db <path>]

# Configuration
context-mesh config [--global]
context-mesh config get <section.field> [--global]
context-mesh config sources [--global]

# Meta
context-mesh --version
```

`--db` resolution order: explicit `--db <path>` ▸ `$CONTEXT_MESH_DB` ▸
`storage.path` from a merged config file ▸ `./.context-mesh/memory.db`.

### Reserved for v1.x or post-v1

These commands appear in product roadmap but are **not implemented yet** —
they will land in later phases (federation, lifecycle, polish):

- `context-mesh connect <hub-url> [--token <token>]` — connect to a team hub (Phase 6).
- `context-mesh disconnect <hub-url>` — disconnect from a hub (Phase 6).
- `context-mesh sync` / `sync status` / `sync pending` — federation sync (Phase 6).
- `context-mesh install / uninstall [claude-code | cursor | git-hooks | codex]` — adapter installer (Phase 8).
- `context-mesh edit <node-id>` — interactive memory editor (Phase 8).
- `context-mesh promote <node-id> --to semantic` — episodic→semantic promotion (Phase 7).
- `context-mesh supersede <old-id> --by <new-id>` — mark a memory superseded (Phase 7).
- `context-mesh doctor` — DB integrity check (Phase 8).
- `context-mesh export` / `import` — JSON backup/restore (Phase 8).
- `context-mesh graph <node-id> [--depth 2]` — graph visualization helper (post-v1).
- `context-mesh config set <key> <value>` — mutating config writer (post-v1; v1 surfaces are read-only).

---

## 3. Library API

For programmatic use from Python.

### Core Object: `Mesh`

```python
from context_mesh import Mesh

# Local-only mesh
mesh = Mesh.local(path="./.context-mesh/memory.db")

# Federated mesh (connect to a hub)
mesh = Mesh.federated(
    hub_url="https://team.example.com",
    token=os.getenv("CONTEXT_MESH_TOKEN"),
    local_cache_path="~/.context-mesh/hubs/team/memory.db"
)
```

### Querying

```python
# Search
cluster = mesh.search(
    query="cart auth bug",
    kind="all",
    scope="team",
    limit=5
)

for node in cluster.nodes:
    print(node.headline, node.score)

# Drill down
full_node = mesh.get(node_id)

# Find contradictions
contradictions = mesh.find_contradictions(
    "we should always use Redis for sessions"
)
```

### Mutating

```python
# Add a memory
node_id = mesh.add(
    content="Always re-verify webhook timestamps with current_time.",
    kind="semantic",
    scope="team",
    tags=["webhooks", "stripe", "security"]
)

# Connect with an edge
mesh.add_edge(
    from_id=semantic_node_id,
    to_id=episodic_node_id,
    relation="generalizes",
    confidence=0.9
)

# Mark used
mesh.mark_used(node_id, helpful=True)
```

### Distillation

```python
from context_mesh.distillation import Distiller

distiller = Distiller(mesh=mesh, model="claude-sonnet")
nodes_created = distiller.distill_session(
    transcript=transcript_data,
    repo="payments",
    branch="main",
    commit_sha="abc1234"
)
```

### Adapters

```python
from context_mesh.adapters import EntireAdapter, AgentMemoryAdapter

# Pull from Entire checkpoints branch
entire = EntireAdapter(mesh=mesh, repo_path="./payments")
new_nodes = entire.sync()

# Migrate from existing agent-memory installation
am_adapter = AgentMemoryAdapter(mesh=mesh)
migrated = am_adapter.import_directory("./.agent-memory")
```

### Hooks

```python
from context_mesh.hooks import register_handler

@register_handler("on_memory_added")
def my_handler(node):
    # custom logic, e.g., notification, automation
    pass
```

---

## 4. HTTP Protocol (For Generic Adapters)

When `context-mesh` runs as a local daemon (via `context-mesh serve`), it
exposes a REST API. The server is built on stdlib `http.server`
(`ThreadingHTTPServer`) — no FastAPI / Flask / aiohttp dependency.

```
GET  /health           liveness probe
POST /search           Mesh.search           (search_team_memory)
GET  /node/{id}        Mesh.get + edges      (drill_down_memory)
POST /node             Mesh.add              (add_memory)
POST /feedback         Mesh.mark_used        (mark_memory_used)
POST /contradictions   Mesh.find_contradictions
```

All endpoints accept and return JSON. **Every endpoint** (including
`/health`) requires `Authorization: Bearer <token>`. The token is
persisted at `~/.context-mesh/token` (mode `0o600` on POSIX); the server
generates one on first start via `secrets.token_urlsafe(32)`. Default
bind is `127.0.0.1:7421` (local-only).

See `docs/HTTP_API.md` for the full reference (request/response payloads,
status codes, examples). Auto-generated OpenAPI / `/docs` is **not** part
of v1 — re-evaluate post-v1 if a generic schema is needed.

This protocol is the integration point for any agent or framework not
natively supported. The agent tool dispatcher in `docs/integrations/`
maps each agent-tool call to one of these endpoints.

---

## 5. Configuration File Format

`config.toml`:

```toml
[storage]
path = ".context-mesh/memory.db"
backup_dir = ".context-mesh/backups"

[embeddings]
provider = "huggingface"
model = "all-MiniLM-L6-v2"
dimensions = 384
api_key_env = "HF_API_KEY"

[distillation]
provider = "claude-cli"
model = "sonnet"
fallback_to_heuristic = true

[retrieval]
default_limit = 5
quality_threshold = 40
content_score_threshold = 30
token_budget = 500

[sync]
hub_url = ""
token_env = "CONTEXT_MESH_TOKEN"
sync_interval_seconds = 600
auto_pull_on_session_start = true

[scopes]
default_scope = "private"

[observability]
log_level = "info"
log_path = ".context-mesh/log.jsonl"
metrics_enabled = false
otel_endpoint = ""

[adapters]
entire_enabled = false
agent_memory_enabled = false
```

**Resolution order** (low precedence ▸ high precedence):

1. Hardcoded defaults (the section dataclass defaults in
   `context_mesh.config`).
2. Global config: `~/.context-mesh/config.toml` (if it exists).
3. Project config: `./.context-mesh/config.toml` (if it exists). Skipped
   when the loader is invoked with `use_global=True`.
4. Environment-variable overrides:
   - `CONTEXT_MESH_DB` → `storage.path`
   - `CONTEXT_MESH_LOG_LEVEL` → `observability.log_level`
   - `CONTEXT_MESH_HUB_URL` → `sync.hub_url`

Project-local overrides global; env overrides everything. Unknown sections
and unknown keys log a warning but do **not** fail the load (forward
compatibility).

The resolved config is exposed via `context_mesh.config.load_config()`
(returns an immutable `Config`) and via the `context-mesh config` CLI.
See `docs/CONFIG.md` for the per-key reference.

---

## 6. Versioning Strategy

The library follows [semantic versioning](https://semver.org/):

- **Patch** (1.0.x) — bug fixes, doc updates, no breaking changes.
- **Minor** (1.x.0) — new features, new tools, new adapters. Backward compatible.
- **Major** (x.0.0) — breaking changes to API surface, schema, or behavior.

The CLI, library, and HTTP API versions are aligned: all three move together.

---

## 7. Stability Guarantees

For v1.0+:

- **Schema migrations** are forward-compatible. New columns / tables can be added in minor releases; removals require a major.
- **CLI commands** are stable across minor versions. New commands and flags can be added; existing ones never change semantics without a major.
- **Library API** at the `Mesh` class level is stable across minors. Lower-level modules may evolve.
- **HTTP API** is stable across minors. Each major version is a new path prefix (`/v1/...`, `/v2/...`).
- **Tool signatures exposed to agents** are stable across minors. New optional parameters allowed; existing ones never change.

---

## 8. What's Out Of Scope For The API In v1

- **Streaming responses** — the API returns complete responses, not streams. Streaming retrieval is a v1.x consideration.
- **Real-time subscriptions** — no websockets for live updates. Polling-based sync is sufficient.
- **GraphQL endpoint** — REST only in v1.
- **Bulk import endpoints** — single-item endpoints with batching at the client level.

These can be added later without breaking existing surfaces.
