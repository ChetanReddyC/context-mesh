# Schema

This document defines the data model for `context-mesh`. All other components (storage, retrieval, federation, adapters) are built around this schema.

---

## Overview

Six logical entities, all stored in a single SQLite database via `sqlite-vec`:

1. **Memory Nodes** — atomic units of distilled knowledge.
2. **Memory Edges** — typed relationships between nodes.
3. **Vectors** — embedding storage (managed by `sqlite-vec`).
4. **Scopes** — privacy and access labels.
5. **Sessions** — references to source agent sessions.
6. **Audit Log** — every retrieval, injection, edit, and sync event.

### Identifier Strategy

Every primary `id` column targets **UUID v7** (sortable by creation time).
Python's `uuid.uuid7()` was added to stdlib in Python 3.13. The v1 codebase
runs on a 3.11+ floor (per ADR 0001 §2) and therefore generates `uuid.uuid4()`
at runtime today. Functional behavior is identical (globally unique, opaque
strings); the temporal-ordering benefit of v7 is recovered when the project's
Python floor advances to 3.13 — a one-line swap, no schema change required.

---

## 1. Memory Nodes

The atomic unit. One node = one distilled memory.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | TEXT (uuid v7) | yes | Globally unique identifier. v7 = sortable by creation time. |
| `kind` | TEXT enum | yes | `episodic` \| `semantic` \| `procedural` |
| `body` | TEXT | yes | Human-readable distilled content (~100-300 tokens). |
| `headline` | TEXT | yes | One-line summary of the memory (≤120 chars). |
| `summary` | TEXT | no | 2-3 line summary for tier-2 retrieval. |
| `decisions` | JSON array | no | List of explicit decisions captured. |
| `failed_approaches` | JSON array | no | List of approaches that didn't work. |
| `warnings` | JSON array | no | Things to avoid in similar contexts. |
| `error_signatures` | JSON array | no | Error codes or messages associated with this memory. |
| `cause_chain` | JSON array | no | Sequence of cause→effect steps. |
| `file_dependencies` | JSON array | no | File paths this memory pertains to. |
| `key_insight` | TEXT | no | The single most important takeaway. |
| `tags` | JSON array | no | Free-form tags for filtering. |
| `scope_id` | TEXT FK | yes | Foreign key to `scopes.id`. |
| `source_session_id` | TEXT FK | yes | Foreign key to `sessions.id`. |
| `source_repo` | TEXT | yes | Repository this memory originated from. |
| `source_branch` | TEXT | no | Branch (often `main`). |
| `source_commit` | TEXT | no | Git commit SHA. |
| `created_at` | INTEGER (unix ts) | yes | Creation time. |
| `updated_at` | INTEGER (unix ts) | yes | Last modification time. |
| `decayed_at` | INTEGER (unix ts) | no | Time at which this memory became stale (set by decay engine). |
| `importance` | REAL [0..1] | yes | Composite importance score (decisions count, warnings, etc.). |
| `usage_count` | INTEGER | yes | Number of times this memory has been retrieved (informs ranking). |
| `helpful_count` | INTEGER | yes | Number of times marked helpful by an agent (informs ranking). |
| `superseded_by` | TEXT FK | no | If this memory has been replaced by a newer one. |
| `content_hash` | TEXT | yes | SHA-256 of normalized body+key fields (used for de-duplication during sync). |

### Memory Kind Definitions

- **Episodic** — *what happened in a specific session.*
  - Example: *"On 2026-04-15, agent debugged Stripe webhook timeout in payments service. Root cause: trusted webhook timestamp instead of re-verifying."*
  - Decay: aggressive (relevance fades within weeks).
  - Retrieval: typically only when query specifically asks about past sessions.

- **Semantic** — *generalized rules that persist across sessions.*
  - Example: *"Never trust the first webhook timestamp from Stripe; always re-verify with current_time."*
  - Decay: slow (rules persist until contradicted or superseded).
  - Retrieval: high priority for active queries; surfaces frequently.

- **Procedural** — *how to do specific tasks.*
  - Example: *"To deploy the payments service: run `make deploy --region=us-west`. Requires VPN."*
  - Decay: medium (commands change but slowly).
  - Retrieval: pulled when query matches a task type.

For full details on memory kinds, see `docs/MEMORY_TYPES.md`.

---

## 2. Memory Edges

Typed relationships between nodes. Edges are what make this a graph, not just a vector store.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | TEXT (uuid v7) | yes | Edge identifier. |
| `from_node_id` | TEXT FK | yes | Source node. |
| `to_node_id` | TEXT FK | yes | Target node. |
| `relation` | TEXT enum | yes | One of the relation types (see below). |
| `confidence` | REAL [0..1] | yes | How confident the system is in this edge. |
| `created_at` | INTEGER (unix ts) | yes | When the edge was created. |
| `created_by` | TEXT enum | yes | `auto` (inferred) \| `manual` (user-created) \| `agent` (agent-asserted). |
| `metadata` | JSON | no | Free-form context (e.g., the inference reasoning). |

### Relation Types (v1 Vocabulary)

| Relation | Meaning | Example |
|---|---|---|
| `caused_by` | Memory A's situation was caused by something in memory B. | "Bug X was caused by decision Y." |
| `applies_to` | Memory A (a rule) applies to the file/module/domain in memory B. | "Rule about webhooks applies to payments service." |
| `contradicts` | Memory A and B express conflicting decisions or rules. | "Team A says use Redis; team B says don't." |
| `generalizes` | Memory A is the generalized form of B. | "Generic rule from 3 specific bugs." |
| `supersedes` | Memory A replaces memory B (B is now stale). | "New deploy command supersedes old one." |
| `co_occurs_with` | A and B frequently appear in the same sessions. | "Auth issues co-occur with cache issues." |

The edge vocabulary is intentionally small in v1. New relations are added only when there's a clear retrieval improvement to be gained, not preemptively.

---

## 3. Vectors

Managed by `sqlite-vec`. Indexed by node ID.

### Fields

| Field | Type | Description |
|---|---|---|
| `node_id` | TEXT FK | Foreign key to `nodes.id`. One vector per node in v1. |
| `embedding` | BLOB | The vector, stored as a `sqlite-vec` blob. |
| `model` | TEXT | Embedding model identifier (e.g., `huggingface/all-MiniLM-L6-v2`). |
| `dimensions` | INTEGER | Vector dimensionality (e.g., 384). |
| `embedded_at` | INTEGER | When the embedding was computed. |

In v1, one vector per node, embedding the `body` field. Multi-resolution embeddings (per-field, per-cluster) are deferred to v1.x.

---

## 4. Scopes

Privacy and access boundaries. Every node has one scope.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | TEXT (uuid v7) | yes | Scope identifier. |
| `name` | TEXT | yes | Human-readable name (e.g., `private`, `team:payments`, `org:acme`). |
| `level` | TEXT enum | yes | `private` \| `team` \| `org` |
| `team_id` | TEXT | no | If level is `team`, the team identifier. |
| `org_id` | TEXT | no | If level is `org`, the organization identifier. |
| `created_at` | INTEGER | yes | When the scope was created. |
| `policy_json` | JSON | no | Optional fine-grained access policy (allowlist, denylist, expiry). |

### Default Scopes (Built-In)

- `private` — visible only to the creator.
- `team:default` — visible to all members of the same team mesh.
- `org:default` — visible to all members of the organization.

Custom scopes can be created for fine-grained boundaries (e.g., `team:security` for security-sensitive memories).

For details on access enforcement, see `docs/SECURITY_PRIVACY.md`.

---

## 5. Sessions

Reference data for the agent sessions that produced memories.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | TEXT (uuid v7) | yes | Session identifier (matches Entire CLI session ID format if applicable). |
| `repo` | TEXT | yes | Repository name. |
| `branch` | TEXT | no | Branch name. |
| `agent` | TEXT | yes | Agent identifier (`claude-code`, `cursor`, `codex`, etc.). |
| `started_at` | INTEGER | yes | Session start time. |
| `ended_at` | INTEGER | no | Session end time. |
| `turn_count` | INTEGER | no | Number of agent turns. |
| `token_usage` | INTEGER | no | Total tokens consumed. |
| `commit_sha` | TEXT | no | Resulting commit SHA, if any. |
| `transcript_uri` | TEXT | no | Pointer to the raw transcript (e.g., a path on the Entire checkpoints branch). |

Sessions are reference data — they let memories link back to their source for drill-down. The full transcript is NOT stored in `context-mesh`; only the structured memories distilled from it.

---

## 6. Audit Log

Every operation gets logged. This is non-negotiable; without it we cannot measure quality.

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | TEXT (uuid v7) | yes | Audit entry identifier. |
| `event_type` | TEXT enum | yes | `retrieve` \| `inject` \| `add` \| `edit` \| `delete` \| `sync_push` \| `sync_pull` \| `mark_helpful` \| `mark_unhelpful` |
| `actor` | TEXT | yes | Who performed the operation (agent ID, user ID, or `system`). |
| `node_ids` | JSON array | no | Affected node IDs. |
| `query` | TEXT | no | If retrieval, the query string. |
| `result_count` | INTEGER | no | If retrieval, how many nodes returned. |
| `metadata` | JSON | no | Event-specific context. |
| `timestamp` | INTEGER | yes | Event time. |

Audit log is append-only. It powers usage analytics, debugging, and the `mark_helpful`/`mark_unhelpful` feedback loop that feeds back into ranking.

---

## Database Layout (SQLite Tables)

```sql
-- Conceptual SQL; actual implementation may vary slightly.
-- See src/storage/migrations/ for the canonical DDL once written.

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('episodic','semantic','procedural')),
  body TEXT NOT NULL,
  headline TEXT NOT NULL,
  summary TEXT,
  decisions JSON,
  failed_approaches JSON,
  warnings JSON,
  error_signatures JSON,
  cause_chain JSON,
  file_dependencies JSON,
  key_insight TEXT,
  tags JSON,
  scope_id TEXT NOT NULL REFERENCES scopes(id),
  source_session_id TEXT NOT NULL REFERENCES sessions(id),
  source_repo TEXT NOT NULL,
  source_branch TEXT,
  source_commit TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  decayed_at INTEGER,
  importance REAL NOT NULL DEFAULT 0,
  usage_count INTEGER NOT NULL DEFAULT 0,
  helpful_count INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT REFERENCES nodes(id),
  content_hash TEXT NOT NULL UNIQUE
);

CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_nodes_scope ON nodes(scope_id);
CREATE INDEX idx_nodes_repo ON nodes(source_repo);
CREATE INDEX idx_nodes_decayed ON nodes(decayed_at) WHERE decayed_at IS NOT NULL;

CREATE TABLE edges (
  id TEXT PRIMARY KEY,
  from_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  to_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK (relation IN (
    'caused_by','applies_to','contradicts','generalizes','supersedes','co_occurs_with'
  )),
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL CHECK (created_by IN ('auto','manual','agent')),
  metadata JSON,
  UNIQUE(from_node_id, to_node_id, relation)
);

CREATE INDEX idx_edges_from ON edges(from_node_id);
CREATE INDEX idx_edges_to ON edges(to_node_id);
CREATE INDEX idx_edges_relation ON edges(relation);

-- Vectors: managed by sqlite-vec
CREATE VIRTUAL TABLE vec_nodes USING vec0(
  node_id TEXT PRIMARY KEY,
  embedding FLOAT[384]
);

CREATE TABLE vector_meta (
  node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  embedded_at INTEGER NOT NULL
);

CREATE TABLE scopes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  level TEXT NOT NULL CHECK (level IN ('private','team','org')),
  team_id TEXT,
  org_id TEXT,
  created_at INTEGER NOT NULL,
  policy_json JSON
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  repo TEXT NOT NULL,
  branch TEXT,
  agent TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  turn_count INTEGER,
  token_usage INTEGER,
  commit_sha TEXT,
  transcript_uri TEXT
);

CREATE INDEX idx_sessions_repo ON sessions(repo);
CREATE INDEX idx_sessions_agent ON sessions(agent);

CREATE TABLE audit (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  node_ids JSON,
  query TEXT,
  result_count INTEGER,
  metadata JSON,
  timestamp INTEGER NOT NULL
);

CREATE INDEX idx_audit_timestamp ON audit(timestamp);
CREATE INDEX idx_audit_actor ON audit(actor);
CREATE INDEX idx_audit_event ON audit(event_type);
```

---

## Schema Versioning

The schema is versioned via SQLite migrations. Migration files live in `src/storage/migrations/` and are numbered sequentially.

Schema changes follow semver:
- **Patch** changes (adding non-required columns, indexes) — auto-migrate.
- **Minor** changes (new tables, new optional fields) — auto-migrate with backup.
- **Major** changes (renaming, removing) — require explicit user opt-in via `context-mesh upgrade`.

---

## Sample Data

For concrete examples of memory nodes and edges, see:
- `schemas/memory_node.example.json`
- `schemas/memory_edge.example.json`

---

## Future Schema Extensions (Deferred)

These are explicitly out of v1 scope but anticipated:

- **Multi-resolution embeddings** — multiple vectors per node (concept-level, memory-level, field-level).
- **Causal chain edges** — a richer relation type that captures multi-step causality.
- **Skill cards** — a separate node kind for executable directives.
- **Federated identity** — cross-org user identifiers for shared mesh participation.
- **Memory templates** — reusable structures for common patterns (security rules, deploy procedures, etc.).

These will land when the foundation is solid and the use cases demand them, not before.
