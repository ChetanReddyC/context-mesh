# Changelog

All notable changes to `context-mesh` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added — Phase 0: Foundation
- `pyproject.toml` with src-layout, `uv`-managed deps, ruff/mypy/pytest config (ADR-0001).
- ADR-0001 documenting 20 locked Phase 0 decisions.
- `SqliteVecBackend`, migration runner, and `0001_initial_schema.sql` (v1 schema).
- `structlog`-backed logging configuration and `audit.log()` skeleton.
- `context-mesh init` CLI command (creates `.context-mesh/memory.db` and `config.toml`).
- GitHub Actions CI: ruff lint + format, mypy strict, pytest, wheel build, wheel-install smoke; matrix Python 3.11 / 3.12 / 3.13 on Linux.
- Pre-commit hooks: ruff (lint + format) and standard hygiene checks.
- `CONTRIBUTING.md` extended with local development setup and quality-check workflow.

### Added — Phase 1: Core Memory Operations
- `Mesh.add` / `Mesh.get` / `Mesh.list_nodes` / `Mesh.update` / `Mesh.delete` — Node CRUD with audit on every mutation.
- `Mesh.add_edge` / `Mesh.get_edge` / `Mesh.get_edges` / `Mesh.list_edges` / `Mesh.delete_edge` — Edge CRUD with audit.
- `Mesh.set_vector` / `Mesh.get_vector` / `Mesh.has_vector` / `Mesh.delete_vector` — vector storage with atomic two-table coordination, dimension validation, explicit `replace` semantics, and explicit `vec_nodes` cleanup on node deletion.
- `Mesh.make_node` and `Mesh.make_edge` — convenience constructors with auto uuid4, timestamps, and content_hash.
- `MemoryNode` / `MemoryEdge` dataclasses with `to_row` / `from_row` round-trip and `compute_content_hash`.
- Cross-cutting integration tests verifying full lifecycle, audit integrity, and cascade semantics.

### Added — Phase 2: Embeddings & Retrieval
- `EmbeddingProvider` Protocol (`@runtime_checkable`) for pluggable embedding backends.
- `DeterministicEmbeddingProvider` — hash-seeded, network-free, L2-normalized 384-dim vectors for tests.
- `HuggingFaceProvider` — HuggingFace Inference API client (`sentence-transformers/all-MiniLM-L6-v2` default, 384-dim), `httpx`-backed, mock-transport-tested in CI.
- `httpx>=0.27,<1.0` runtime dependency.
- `Mesh.search_by_vector` — vector kNN primitive over `vec_nodes` with optional `kind` / `scope_id` filters.
- `Mesh.search` — public hybrid retrieval. Takes a text query plus an `EmbeddingProvider`, runs vector kNN + 1-hop graph expansion + composite ranking (semantic 0.50 / relevance 0.20 / recency 0.10 / importance 0.10 / usage 0.10) + quality gate. Returns a `MemoryCluster`. Audits each retrieval as `event_type='retrieve'` with the query, result count, embedder name, and filter parameters.
- `MemoryCluster`, `ScoredNode`, `VectorSearchResult` dataclasses for structured retrieval output.
- Minimal retrieval-quality eval harness (5 golden cases) and a 100-node latency smoke test.

### Added — Phase 4: CLI
- `context-mesh search <query>` — hybrid retrieval at the command line with `--kind`, `--scope-id`, `--limit`, and `--json` modes.
- `context-mesh add <content>` — adds a memory node, auto-deriving the headline from the first line of content when one is not supplied.
- `context-mesh show <node-id>` — pretty-prints a node and its incoming/outgoing edges (with optional `--json`).
- `context-mesh list` — newest-first listing of nodes with `--kind`, `--scope-id`, `--limit`, and `--json` filters.
- `context-mesh delete <node-id>` — confirmation-gated deletion (`--yes`/`-y` to skip the prompt).
- `context-mesh distill <session-file>` — reads a transcript and persists distilled nodes via `HeuristicDistiller` (default) or `ClaudeCliDistiller`.
- `context-mesh stats` — store-wide counts (nodes by kind, edges by relation, vectors) plus the latest audit timestamp.
- `context-mesh audit` — recent audit rows with `--actor`, `--event-type`, `--limit`, and `--json` filters.
- `python -m context_mesh.cli` entry point (`src/context_mesh/cli/__main__.py`) so the CLI runs as a module.
- Subprocess-based system-test harness (`tests/system/`) that drives the installed CLI end-to-end through `python -m context_mesh.cli`; gated behind the `system` pytest marker.

### Added — Phase 4: API gaps
- `Mesh.find_contradictions(content, *, embedder, kind="semantic", limit=5, scope_id=None, actor="mesh")` — flat vector kNN over `vec_nodes` filtered to `semantic` or `procedural` nodes, returning the closest `MemoryNode`s by similarity. Audits as `event_type='retrieve'` with `metadata.action='find_contradictions'`. v1 surfaces similarity, not logical-contradiction verdicts.
- `Mesh.mark_used(node_id, helpful, *, notes=None, actor="mesh")` — increments `usage_count` (+1 every call) and `helpful_count` (+1 only when `helpful=True`), stamps `updated_at`, and audits as `mark_helpful` or `mark_unhelpful`. Returns the refreshed node.

### Added — Phase 4: HTTP server
- `context-mesh serve` CLI command — runs a stdlib `http.server.ThreadingHTTPServer` (no FastAPI / Flask / aiohttp). Defaults to `127.0.0.1:7421` (local-only); accepts `--host`, `--port`, and `--db`.
- `MeshServer` class (`context_mesh.server.MeshServer`) with `start()`, `stop()`, and `url`. Each request opens a fresh `Mesh.local` connection so SQLite stays single-threaded per connection.
- `GET /health` — liveness probe returning `{"status": "ok", "version": ...}`.
- `POST /search` — wraps `Mesh.search`; accepts `query`, `kind`, `scope_id`, `limit`; returns a serialized `MemoryCluster`.
- `GET /node/{id}` — wraps `Mesh.get` + `Mesh.get_edges`; returns the node plus its in/out edges, or 404.
- `POST /node` — wraps `Mesh.add`; accepts `content`, `kind`, optional `headline`, `scope_id`, `source_session_id`, `source_repo`, `tags`; returns `{"node_id", "auto_inferred_edges", "requires_review"}`.
- `POST /feedback` — wraps `Mesh.mark_used`; accepts `node_id`, `helpful`, optional `notes`; returns the refreshed counts.
- `POST /contradictions` — wraps `Mesh.find_contradictions`; accepts `content`, `kind` (default `"semantic"`), `limit`, `scope_id`; returns `{"results": [...]}`.
- Bearer-token auth on every endpoint. Token persisted at `~/.context-mesh/token` (mode `0o600` on POSIX), generated on first use via `secrets.token_urlsafe(32)`.
- Per-request structured logging (`context_mesh.server`) of method, path, status, and duration; request bodies are never logged.

### Added — Phase 4: Configuration
- `context_mesh.config` — public configuration loader. Stdlib `tomllib`-backed reader plus eight immutable section dataclasses (`StorageConfig`, `EmbeddingsConfig`, `DistillationConfig`, `RetrievalConfig`, `SyncConfig`, `ScopesConfig`, `ObservabilityConfig`, `AdaptersConfig`) and a top-level `Config` aggregate that records the merged source files in `source_paths`.
- `load_config(path=None, *, use_global=False)` — resolves defaults ▸ `~/.context-mesh/config.toml` (global) ▸ `./.context-mesh/config.toml` or `<path>/config.toml` (project, skipped when `use_global=True`) ▸ environment overrides. Unknown sections and keys log a warning but do not fail the load.
- Environment-variable overrides (intentionally minimal): `CONTEXT_MESH_DB` → `storage.path`, `CONTEXT_MESH_LOG_LEVEL` → `observability.log_level`, `CONTEXT_MESH_HUB_URL` → `sync.hub_url`.
- `ConfigError(ValueError)` — raised on malformed TOML, wrong scalar type, or invalid `Literal` enum value. Includes file path and dotted key in every message.
- `Mesh.from_config(config)` — additive constructor that opens a local `Mesh` at `config.storage.path` (sibling of `Mesh.local` and `Mesh.federated`).
- `context-mesh config` CLI subcommand group: bare `config` prints the resolved TOML to stdout; `config get <section.field>` prints a single value; `config sources` lists merged config files in load order. All three accept `--global` to skip the project file.
- CLI `_resolve_db_path` now consults `storage.path` from a merged config file when neither `--db` nor `$CONTEXT_MESH_DB` is set, before falling back to the historical `./.context-mesh/memory.db` heuristic. The new path is additive; existing CLI invocations are unchanged.

### Added — Phase 4: Agent tool schemas
- `context_mesh.integrations` — public exports of agent-tool schemas in three dialects: `ANTHROPIC_TOOLS` (`name` / `description` / `input_schema`), `OPENAI_TOOLS` (`type: "function"` with nested `function`), and `MCP_TOOLS` (`name` / `description` / camelCase `inputSchema`). Five tools: `search_team_memory`, `drill_down_memory`, `add_memory`, `mark_memory_used`, `find_contradictions`. `TOOL_NAMES` exposes the canonical ordering; `tool_for(name, dialect)` is a single-tool lookup that returns a defensive deep copy.
- `context-mesh tools [--dialect anthropic|openai|mcp] [--out <path>]` CLI command — emits the JSON tool list to stdout (default) or a file. Drop the output straight into an Anthropic SDK call, an OpenAI tool-use loop, or an MCP server.
- `docs/integrations/{claude-code,cursor,openai,mcp}.md` — concrete per-ecosystem walkthroughs (prerequisites, schema generation, dispatcher mapping to the local HTTP server, troubleshooting).

### Added — Phase 3: Distillation
- `redact()` — pure-stdlib pipeline that scrubs 15 categories of secrets (PEM/PGP private keys, credentialed URLs, JWTs, AWS / GitHub / Stripe / OpenAI / Anthropic / HuggingFace / Slack / Google API keys, bearer tokens, generic `password=`/`api_key:` assignments, plus a Shannon-entropy fallback with hash/UUID exclusions). Returns `(scrubbed_text, list[Finding])`; deterministic and idempotent.
- `classify()` — heuristic memory-kind classifier (episodic / semantic / procedural) over weighted regex signals with deterministic tiebreak ladder; returns a `ClassificationResult` carrying per-class scores and matched-signal names. Default-on-tie kind is `semantic` (rationale documented in module docstring).
- `Distiller` Protocol (`@runtime_checkable`) and `DistillerCandidate` dataclass — the contract every backend satisfies.
- `HeuristicDistiller` — pure-stdlib block-splitting + signal-scoring extractor. Runs `redact()` upstream, classifies each kept block, extracts decisions / failed approaches / error signatures / file paths / curated-vocabulary tags, generates a ≤120-char headline, and emits a list of `DistillerCandidate`s. Deterministic, network-free.
- `ClaudeCliDistiller` — calls the local `claude` CLI with `--output-format json` and a strict-JSON prompt template. Tolerant JSON extraction (handles chatty preludes, markdown fences, and the CLI's `{"result":...}` envelope). Falls back to `HeuristicDistiller` on any failure: CLI not on PATH, non-zero exit, timeout, spawn error, unparseable JSON, missing top-level `candidates` key. Each fallback emits a structured `claude_cli.fallback` warning. Redaction runs upstream of the prompt — secrets never reach the model.
- `Mesh.distill(session_text, *, distiller, scope_id, source_session_id, source_repo, actor)` — public entry point. Persists each candidate as a `MemoryNode`, infers intra-session edges (`semantic`→`episodic` as `generalizes`, `procedural`→`episodic` as `applies_to`, `created_by="auto"`, capped at 16 edges/session), and audits each node-add and edge-add row with distiller provenance and redaction-finding summary in `metadata`.

---

## [0.0.0] — Project Initialization

- Repository created.
- Documentation framework established under `docs/`.
- `CLAUDE.md` agent onboarding brief written.
- License (MIT) declared.
- Contributing guidelines written.
