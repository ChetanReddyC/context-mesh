# Architecture

## High-Level System Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         AGENT (Claude Code, Cursor, etc.)            │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │  Tools exposed: search_team_memory, drill_down, add_memory   │   │
│   └──────────────────────────────────────────────────────────────┘   │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ tool calls
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       context-mesh (THIS PROJECT)                    │
│                                                                      │
│   ┌──────────────────────┐    ┌────────────────────────────────┐    │
│   │  Retrieval Engine    │    │  Distillation Engine           │    │
│   │  (vector + graph)    │    │  (raw session → memory node)   │    │
│   └──────────┬───────────┘    └────────────┬───────────────────┘    │
│              │                              │                        │
│              ▼                              ▼                        │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │                  STORAGE LAYER                               │   │
│   │   SQLite + sqlite-vec  (single file, embedded)               │   │
│   │   Tables: nodes, edges, vectors, scopes, sessions, audit     │   │
│   └──────────────────────────────────────────────────────────────┘   │
│              ▲                              ▲                        │
│              │                              │                        │
│   ┌──────────┴───────────┐    ┌────────────┴───────────────────┐    │
│   │  Sync / Federation   │    │  Adapters                      │    │
│   │  (pull/push to hub)  │    │  (Entire CLI, agent-memory)    │    │
│   └──────────────────────┘    └────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
                                 ▲
                                 │ federation sync
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        TEAM MESH HUB                                 │
│       (centralized SQLite database; access-controlled)               │
│       sources of memory: every repo, every team member               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1. Storage Layer (Foundation)

**SQLite + `sqlite-vec` extension.**

Six core tables, all in a single file:

- `nodes` — memory nodes (the atomic unit).
- `edges` — typed relationships between nodes.
- `vectors` — embedding storage (managed by sqlite-vec).
- `scopes` — privacy/access labels and policies.
- `sessions` — references to source sessions (foreign keys).
- `audit` — every retrieval, every injection, every edit (for observability).

For exact schema definitions, see `docs/SCHEMA.md`.

**Why SQLite:**
- Single-file deployment — easy to back up, easy to demo, easy to ship.
- `sqlite-vec` provides production-grade vector search inside SQLite without external infrastructure.
- Supports both write-heavy (distillation) and read-heavy (retrieval) workloads.
- Cross-platform, embedded, MIT-licensed.
- Easy migration path to Postgres/Weaviate/Qdrant when scale requires it.

**Why not [Postgres / Pinecone / Qdrant / Weaviate / Neo4j]:**
Each adds operational overhead. We are building a primitive, not a hosted service. Users and teams can swap the storage adapter later if they need scale; SQLite is the right default.

### 2. Distillation Engine (Write Path)

Converts raw agent session data into structured memory nodes.

**Inputs:**
- A session transcript (from agent tooling, Entire CLI checkpoints, or `agent-memory`).
- Session metadata (repo, branch, files touched, commit hash).
- User signals (manual annotations, importance flags).

**Process:**
1. Pre-clean: strip boilerplate, redact secrets.
2. LLM distillation pass (default: Claude Sonnet via CLI; pluggable to other models).
3. Heuristic extraction (fallback when LLM is unavailable; also runs as a verification pass).
4. Memory-kind classification (episodic / semantic / procedural).
5. Field extraction (decisions, failed approaches, error signatures, file dependencies, rules).
6. Embedding generation (default: HuggingFace `all-MiniLM-L6-v2`).
7. Edge inference (look for relationships to existing memories — `caused_by`, `contradicts`, etc.).
8. Persist to storage.

**Output:** one or more memory nodes + edges, written to the local store and queued for federation sync.

For full details, see `docs/SCHEMA.md` and `docs/DESIGN_PRINCIPLES.md`.

### 3. Retrieval Engine (Read Path)

Hybrid search combining vector similarity and graph traversal.

**Algorithm:**
1. Receive a query (natural language, from an agent tool call).
2. Embed the query.
3. Vector similarity search over `nodes.embedding` → top-K nearest nodes.
4. Apply scope filter (private / team / org based on caller's identity).
5. Apply memory-kind filter (caller can specify: episodic only, semantic only, mixed).
6. Graph traversal: for each top-K node, walk 1-2 hops on edges (`generalizes`, `applies_to`) to find structurally-relevant context.
7. De-duplicate, re-rank by composite score (similarity × edge weight × recency × decision_importance).
8. Apply quality gate: drop nodes whose composite score falls below threshold (prevents noisy injection).
9. Format response as structured tool output.

For details, see `docs/RETRIEVAL_DESIGN.md`.

### 4. Active Retrieval Surface (Agent Interface)

The agent does NOT receive auto-injected context. It calls tools.

**Tools exposed to the agent:**
- `search_team_memory(query: string, kind?: "episodic"|"semantic"|"procedural") → MemoryCluster`
- `drill_down_memory(node_id: string) → FullNode`
- `add_memory(content: string, kind: string, scope: string) → NodeId` — agent can annotate during a session
- `mark_memory_used(node_id: string, helpful: boolean) → void` — feedback loop for ranking
- `find_contradictions(content: string) → MemoryNode[]` — surface conflicting prior decisions

**Why active over passive:**
- Agent decides when context is needed, eliminating noise.
- Agent's context window stays clean for the actual problem.
- Memory becomes part of the agent's reasoning loop, not a pre-load.
- Mirrors how senior engineers work: ask a colleague when stuck, not before starting.

For details, see `docs/RETRIEVAL_DESIGN.md`.

### 5. Sync / Federation Layer

Handles pull/push between local store and team mesh hub.

**Two modes:**
- **Solo mode** — local-only, no federation. Used when developers work alone or in private contexts.
- **Federated mode** — local store syncs with a team hub. Memories tagged `team` or `org` flow up; memories tagged `private` stay local.

**Sync triggers:**
- Periodic background sync (configurable interval).
- On-demand (CLI: `context-mesh sync`).
- Pre-session pull (before agent starts, ensure local mirror is fresh).
- Post-distillation push (after a new memory is created, queue it for upload).

**Conflict handling:**
- Memories are content-addressable (hashed). Identical memories deduplicate.
- Edges added in different repos for the same pair of nodes are merged.
- Contradictions flagged via `contradicts` edges, never silently merged.

For details, see `docs/SECURITY_PRIVACY.md` and `docs/EXTENSIBILITY.md`.

### 6. Adapter Layer

Pluggable interfaces to source systems.

**Initial adapters:**
- **Entire CLI adapter** — reads checkpoints from the `entire/checkpoints/v1` git branch, distills them into nodes.
- **`.agent-memory/` adapter** — imports records from existing `.agent-memory/` directories.
- **Claude Code adapter** — `UserPromptSubmit` hook for active retrieval; `PostToolUse` hook for memory creation.
- **Cursor adapter** — extension-based integration.
- **Generic adapter interface** — any agent tool with tool-use can integrate via the documented protocol.

For details, see `docs/INTEGRATION.md` and `docs/EXTENSIBILITY.md`.

---

## Data Flow Examples

### Flow 1: Agent Starts A New Task

```
1. Agent receives a user prompt: "fix the cart-auth bug"
2. Agent's reasoning triggers a memory search (autonomous decision):
     tool_call: search_team_memory(query="cart auth bug")
3. context-mesh:
     a. Embeds the query.
     b. Vector search → top 5 nodes.
     c. Filter by scope (team-visible).
     d. Walk edges → find 2 "applies_to" rules from another repo.
     e. Re-rank, drop low-relevance.
     f. Return cluster of 3 tight memories.
4. Agent receives:
     - "Last time this happened (3 weeks ago, payments service):
        guest-checkout flow was missed. Always cover both paths."
     - "Team rule: never trust the first webhook timestamp."
     - "Procedure: re-deploy with `make deploy --region=us-west` after auth changes."
5. Agent uses these as scaffolding for its work.
```

### Flow 2: Agent Finishes A Session, New Memory Is Captured

```
1. Session ends (commit pushed to repo).
2. Distillation engine triggered (via post-commit hook or manual).
3. Engine reads:
     - Session transcript
     - Files modified
     - Commit metadata
4. LLM distillation pass produces structured fields:
     - decisions: [...]
     - failed_approaches: [...]
     - error_signatures: [...]
     - key_insight: "..."
     - kind: episodic
5. Embedding generated.
6. Edge inference: matches against existing nodes:
     - caused_by → linked to a prior bug node
     - contradicts → flagged contradiction with an old rule
7. Memory persisted locally.
8. Federation queue: scheduled for sync to team hub.
```

### Flow 3: Federation Sync

```
1. Background scheduler triggers sync.
2. Local store identifies new/updated memories tagged `team` or `org`.
3. Push to hub via authenticated API (only memories within scope).
4. Pull latest hub state since last sync timestamp.
5. Merge:
     - New nodes added to local store (de-duplicated by content hash).
     - New edges added.
     - Contradictions surfaced as warnings.
6. Local index rebuilt for affected nodes.
7. Sync log updated.
```

---

## Key Architectural Decisions And Why

### Decision 1: Active retrieval, not passive injection

**Choice:** Agents call memory tools. Memory is not auto-injected into prompts.

**Why:** The 22-bug benchmark showed auto-injection causes measurable performance degradation when memories are noisy. Active retrieval lets the agent decide when context is genuinely needed, eliminating noise by construction.

**Trade-off:** Agents must have tool-use capability and must know to call memory tools. We accept this — modern agents already operate this way.

### Decision 2: Three memory kinds, not one bag

**Choice:** Memories are typed as episodic, semantic, or procedural. Different retrieval logic, different decay rules.

**Why:** They have fundamentally different shelf lives and use cases. Mixing them collapses retrieval quality.

**Trade-off:** More complexity in the schema. Mitigated by clear documentation and good defaults.

### Decision 3: Knowledge graph + vectors, not pure RAG

**Choice:** Memories form a graph of typed edges, with vectors layered on top.

**Why:** Pure vector search returns "similar" memories that may be contradictory or structurally unrelated. The graph captures structural relationships that similarity misses.

**Trade-off:** Edge inference is harder than embedding. Mitigated by starting with simple edges (`generalizes`, `applies_to`) and adding more as patterns emerge.

### Decision 4: SQLite + sqlite-vec, not a hosted vector DB

**Choice:** Single-file embedded storage.

**Why:** This is a primitive, not a hosted service. Operational overhead must be near-zero. SQLite handles millions of rows comfortably; users who outgrow it can migrate to Postgres/Qdrant via the storage adapter interface.

**Trade-off:** Doesn't scale to billions of vectors out of the box. Acceptable for the v1 target audience (engineering teams of 5-500 people, not Google).

### Decision 5: Federation through a centralized hub, not P2P

**Choice:** A team-owned mesh hub is the source of truth. Clients sync.

**Why:** P2P federation is operationally complex and adds little for the target use case. A hub is simple, auditable, and aligns with how teams already work (centralized git remote, centralized issue tracker).

**Trade-off:** Single point of failure. Mitigated by self-hostability and the local cache (clients work offline against last-synced state).

### Decision 6: Open-source MIT, no SaaS lock-in

**Choice:** Fully open-source primitive. No hosted service in v1.

**Why:** This must be a primitive the entire industry can use. SaaS-first would limit adoption and trust.

**Trade-off:** No revenue model in v1. Acceptable — this is foundational work, not a product play.

---

## What Lives Where

| Component | File / Module |
|---|---|
| Storage layer | `src/storage/` |
| Distillation engine | `src/distillation/` |
| Retrieval engine | `src/retrieval/` |
| Sync / federation | `src/sync/` |
| Adapters (Entire, agent-memory, Claude Code, etc.) | `src/adapters/` |
| CLI | `src/cli/` |
| Library API | `src/api/` |
| Tests | `tests/` |

For detailed module breakdowns, see `docs/STORAGE_DESIGN.md`, `docs/RETRIEVAL_DESIGN.md`, and `docs/API_DESIGN.md`.

---

## Non-Goals

To stay focused, the architecture explicitly excludes:

- **A general-purpose vector database** — many exist; we are not one.
- **A general-purpose graph database** — likewise.
- **A model fine-tuning pipeline** — memory is a runtime resource, not training data.
- **A chat-history search engine** — we operate on distilled structured memories, not raw transcripts.
- **A centralized cloud service** — self-hosted only; no managed offering in v1.

---

## What Comes Next

See `CHANGELOG.md` for shipped work and the project's open issues for current priorities.
