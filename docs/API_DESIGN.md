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

The `context-mesh` command-line interface.

### Setup Commands

```bash
# Initialize for the current project
context-mesh init [--global]

# Connect to a team mesh hub
context-mesh connect <hub-url> [--token <token>]

# Disconnect from a hub
context-mesh disconnect <hub-url>

# Install integration with an agent tool
context-mesh install [claude-code | cursor | git-hooks | codex]

# Uninstall an integration
context-mesh uninstall [claude-code | cursor | git-hooks | codex]
```

### Distillation Commands

```bash
# Distill a single checkpoint or session
context-mesh distill <checkpoint-id | session-id>

# Distill all unprocessed checkpoints
context-mesh distill --all

# Re-distill (overwrites existing distillation)
context-mesh distill --redo <checkpoint-id>
```

### Retrieval Commands

```bash
# Search memories from the CLI (developer-facing)
context-mesh search "<query>" [--kind <kind>] [--limit <n>]

# Show full content of a memory
context-mesh show <node-id>

# List recent memories
context-mesh list [--kind <kind>] [--repo <repo>] [--limit <n>]

# Show graph of related memories
context-mesh graph <node-id> [--depth 2]
```

### Sync Commands

```bash
# Sync with hub now
context-mesh sync

# Show sync status
context-mesh sync status

# Show pending sync items
context-mesh sync pending
```

### Memory Management

```bash
# Add a memory directly (manual)
context-mesh add --kind <kind> --scope <scope> --tags <tags> "<content>"

# Edit a memory
context-mesh edit <node-id>

# Promote an episodic to semantic (after a pattern emerges)
context-mesh promote <node-id> --to semantic

# Mark a memory as superseded
context-mesh supersede <old-id> --by <new-id>

# Delete a memory (hard delete; rarely needed)
context-mesh delete <node-id>
```

### Inspection / Debugging

```bash
# Show overall stats
context-mesh stats

# Show audit log
context-mesh audit [--limit <n>] [--actor <actor>]

# Validate database integrity
context-mesh doctor

# Export everything to JSON (backup)
context-mesh export > backup.json

# Import from JSON
context-mesh import < backup.json
```

### Configuration

```bash
# Show current config
context-mesh config

# Get a specific config value
context-mesh config get <key>

# Set a config value
context-mesh config set <key> <value>
```

### Help

```bash
context-mesh help [command]
context-mesh --version
```

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

When `context-mesh` runs as a local daemon (via `context-mesh serve`), it exposes a REST API:

```
POST /search          → search_team_memory
GET  /node/{id}       → drill_down_memory
POST /node            → add_memory
POST /feedback        → mark_memory_used
POST /contradictions  → find_contradictions
```

All endpoints accept and return JSON. Authentication is via a local-only token written to `~/.context-mesh/token` on first start.

The OpenAPI spec is generated automatically and available at `http://localhost:<port>/docs`.

This protocol is the integration point for any agent or framework not natively supported.

---

## 5. Configuration File Format

`config.toml`:

```toml
[storage]
path = "./.context-mesh/memory.db"
backup_dir = "./.context-mesh/backups"

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
log_path = "./.context-mesh/log.jsonl"
metrics_enabled = false
otel_endpoint = ""

[adapters]
entire_enabled = false
agent_memory_enabled = false
```

Global config: `~/.context-mesh/config.toml`. Project-local config: `./.context-mesh/config.toml`. Project-local overrides global.

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
