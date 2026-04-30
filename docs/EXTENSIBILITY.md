# Extensibility

`context-mesh` is built around small, stable extension points so that the project can adapt without requiring forks or core changes.

This document describes the extension points and how to write against them.

---

## Five Extension Points

1. **Storage Backends** — swap SQLite for Postgres, Qdrant, Weaviate, etc.
2. **Embedding Providers** — swap HuggingFace for OpenAI, Anthropic, local models, etc.
3. **Distillation Models** — swap Claude for any LLM.
4. **Source Adapters** — pull from new data sources (Cursor, Codeium, custom tools).
5. **Tool Frontends** — expose `context-mesh` to new agent runtimes.

Each is a documented interface. Implementations live separately and register at runtime.

---

## 1. Storage Backends

### Interface (`StorageBackend`)

```
StorageBackend:
    # Lifecycle
    open(config: dict) → None
    close() → None
    migrate() → None

    # Node operations
    insert_node(node: MemoryNode) → NodeId
    get_node(id: NodeId) → MemoryNode
    update_node(id: NodeId, fields: dict) → None
    delete_node(id: NodeId) → None
    list_nodes(filter: NodeFilter) → list[MemoryNode]

    # Edge operations
    insert_edge(edge: MemoryEdge) → EdgeId
    get_edges(node_id: NodeId, direction: "out"|"in"|"both") → list[MemoryEdge]
    delete_edge(id: EdgeId) → None

    # Vector operations
    insert_vector(node_id: NodeId, embedding: list[float], model: str) → None
    vector_search(query: list[float], filter: NodeFilter, k: int) → list[(NodeId, float)]

    # Audit
    audit(event: AuditEvent) → None
    query_audit(filter: AuditFilter) → list[AuditEvent]
```

### Implementations

- `SqliteVecBackend` (default, ships with v1).
- `PostgresPgvectorBackend` (planned for v1.x).
- `QdrantBackend` (community contribution welcome).
- `WeaviateBackend` (community contribution welcome).

### Selection

Configured in `config.toml`:

```toml
[storage]
backend = "sqlite"  # or "postgres", "qdrant", "weaviate"
# backend-specific config follows
```

The library loads the appropriate backend at runtime via a registry.

---

## 2. Embedding Providers

### Interface (`EmbeddingProvider`)

```
EmbeddingProvider:
    name: str
    dimensions: int
    embed_text(text: str) → list[float]
    embed_batch(texts: list[str]) → list[list[float]]
```

### Implementations

- `HuggingFaceProvider` (default, uses HF Inference API).
- `OpenAIProvider` (`text-embedding-3-small`, `text-embedding-3-large`).
- `AnthropicProvider` (when Anthropic ships a public embeddings API).
- `LocalProvider` (for air-gapped environments using local models via `sentence-transformers`).

### Selection

```toml
[embeddings]
provider = "huggingface"
model = "all-MiniLM-L6-v2"
dimensions = 384
api_key_env = "HF_API_KEY"
```

### Switching Providers

Switching requires re-embedding all existing nodes. The library provides a migration:

```bash
context-mesh re-embed --to huggingface:bge-small-en
```

This processes nodes in batches, updates the vector index, and verifies retrieval quality post-migration.

---

## 3. Distillation Models

### Interface (`Distiller`)

```
Distiller:
    distill(transcript: SessionTranscript) → list[MemoryNode]
    classify_kind(content: str) → "episodic" | "semantic" | "procedural"
    infer_edges(node: MemoryNode, existing: list[MemoryNode]) → list[MemoryEdge]
```

### Implementations

- `ClaudeCliDistiller` (default; uses local Claude CLI for high-quality distillation).
- `ClaudeApiDistiller` (uses Anthropic API directly).
- `OpenAiDistiller` (uses GPT-4-class models).
- `HeuristicDistiller` (no LLM; pattern-based extraction; lower quality but free and offline).
- `HybridDistiller` (LLM-first, falls back to heuristics if LLM unavailable).

### Selection

```toml
[distillation]
provider = "claude-cli"  # or "claude-api", "openai", "heuristic", "hybrid"
model = "sonnet"
max_tokens = 2000
```

---

## 4. Source Adapters

Source adapters pull raw session data from external systems and feed it into the distillation engine.

### Interface (`SourceAdapter`)

```
SourceAdapter:
    name: str
    discover() → list[SessionReference]
    fetch(reference: SessionReference) → SessionTranscript
    sync_state() → SyncState
    set_sync_state(state: SyncState) → None
```

### Built-In Adapters

- `EntireAdapter` — reads from Entire's `entire/checkpoints/v1` git branch.
- `AgentMemoryAdapter` — imports from `.agent-memory/` directories.
- `ClaudeCodeTranscriptAdapter` — reads Claude Code's session log files.
- `CursorAdapter` — reads from Cursor's session storage.

### Writing A Custom Adapter

```python
from context_mesh.adapters import SourceAdapter, SessionReference, SessionTranscript

class MyToolAdapter(SourceAdapter):
    name = "my-tool"

    def discover(self):
        # Find new sessions since last sync
        return [...]

    def fetch(self, reference):
        # Pull the actual transcript
        return SessionTranscript(...)
```

Register the adapter:

```toml
[adapters.my-tool]
enabled = true
config_path = "./.context-mesh/my-tool.toml"
```

---

## 5. Tool Frontends

Tool frontends expose `context-mesh` to specific agent runtimes.

### Interface (`ToolFrontend`)

```
ToolFrontend:
    name: str
    register_tools(mesh: Mesh) → list[ToolDefinition]
    install() → None  # write any required config
    uninstall() → None
```

### Built-In Frontends

- `ClaudeCodeFrontend` — registers tools as Claude Code tools, writes `.claude/` config.
- `CursorFrontend` — registers tools via Cursor extension.
- `OpenAIFunctionsFrontend` — emits OpenAI function definitions for Codex CLI users.
- `HttpServerFrontend` — exposes HTTP API for any agent that speaks REST.

### Writing A Custom Frontend

For an agent runtime not yet supported, implement `ToolFrontend`:

```python
from context_mesh.frontends import ToolFrontend, ToolDefinition

class GeminiFrontend(ToolFrontend):
    name = "gemini"

    def register_tools(self, mesh):
        return [
            ToolDefinition(
                name="search_team_memory",
                description="...",
                parameters={...},
                handler=lambda args: mesh.search(**args)
            ),
            # ...other tools
        ]
```

---

## Plugin Discovery

Plugins are discovered via Python entry points (`entry_points={'context_mesh.plugins': [...]}` in `setup.py`).

Listing installed plugins:

```bash
context-mesh plugins list
```

Enabling/disabling at runtime:

```bash
context-mesh plugins enable my-plugin
context-mesh plugins disable my-plugin
```

---

## Hooks

For lightweight extensions that don't justify a full plugin, register event handlers:

```python
from context_mesh.hooks import register_handler

@register_handler("on_memory_added")
def my_handler(node):
    # called whenever a new memory is added
    pass

@register_handler("on_retrieval_complete")
def my_handler(query, cluster):
    # called after every retrieval
    pass
```

Available hooks:

| Hook | Triggered When |
|---|---|
| `on_memory_added` | New memory node persisted |
| `on_memory_edited` | Existing memory updated |
| `on_memory_deleted` | Memory hard-deleted |
| `on_retrieval_complete` | Retrieval call finished |
| `on_distillation_complete` | Distillation produced new nodes |
| `on_sync_complete` | Sync push or pull finished |
| `on_contradiction_detected` | New `contradicts` edge inferred |

Hooks fire synchronously by default; can be marked async via decorator.

---

## Adapter Compatibility Matrix

| Storage | Embedding | Distillation | Source | Frontend | Compatibility |
|---|---|---|---|---|---|
| SQLite | HF | Claude CLI | Entire | Claude Code | ✅ Tested (default) |
| SQLite | OpenAI | OpenAI | Entire | Claude Code | ✅ Tested |
| SQLite | HF | Heuristic | agent-memory | HTTP | ✅ Tested |
| Postgres | HF | Claude CLI | Entire | Claude Code | 🟡 Planned (v1.x) |
| Qdrant | OpenAI | OpenAI | Custom | HTTP | 🔵 Community |

✅ Tested = part of test matrix. 🟡 Planned = on roadmap. 🔵 Community = welcomed but not maintained by core team.

---

## Stability Promise

Extension interfaces follow the same versioning as the public API:

- **Patch** versions: no interface changes.
- **Minor** versions: new optional methods may be added; existing methods unchanged.
- **Major** versions: interface changes allowed.

When a major version requires breaking changes, a migration guide is provided and the prior major is supported for a deprecation window.

---

## Out Of Scope For v1

- **Live plugin reload** — no hot-swapping plugins; restart required to pick up changes.
- **Sandboxed plugin execution** — plugins run in the same process; isolation is a future concern.
- **Plugin marketplaces** — official plugin distribution channel; v1.x.

For now, plugins are installed via `pip` and managed manually. Good enough for v1.
