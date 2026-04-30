# Storage Design

## Decision: SQLite + sqlite-vec

`context-mesh` uses **SQLite** as the primary storage engine, augmented with the **`sqlite-vec`** extension for vector search. This is an opinionated choice; this document explains why, what it enables, and what it costs.

---

## Why SQLite

**Operational simplicity.** A single file. No server, no daemon, no port to expose, no replication to configure. Memory mesh works for a solo developer in a checkout, and works for a 500-person engineering org with a self-hosted hub. Same code, same file format.

**Demoability.** You can show someone the entire memory state by opening one file. You can email it. You can commit it to git for an audit log. You can `cp` it to back it up. Vector databases lose this property the moment they require a server.

**Cross-platform.** SQLite works on every operating system, every architecture, every container. There is no environment where `context-mesh` cannot run.

**Mature.** SQLite has been battle-tested for 25 years. It handles concurrent reads at high throughput, writes through WAL mode, and scales comfortably to tens of millions of rows.

**MIT-compatible.** Public domain, no licensing risk for an MIT project.

**Migration path.** When teams outgrow SQLite (millions of memories, dozens of repos with constant write pressure), the storage adapter interface allows swapping to Postgres + pgvector, Qdrant, Weaviate, or any other backend without changing the rest of the system. The adapter contract is the abstraction; SQLite is the default implementation.

---

## Why `sqlite-vec`

`sqlite-vec` (<https://github.com/asg017/sqlite-vec>) is a SQLite extension that adds first-class vector search capabilities. It provides:

- A `vec0` virtual table type for storing vectors.
- KNN search via `MATCH` syntax.
- Quantization options for memory efficiency.
- Pure C, MIT-licensed, embeddable.

It is the right vector store for an embedded library because it composes natively with SQLite — vectors and structured data live in the same database, queries can join across both, and there's no separate index process to manage.

**Alternatives considered and rejected:**

- **`sqlite-vss`** — Older, deprecated in favor of `sqlite-vec`.
- **`faiss` with separate index files** — Operationally clumsy; doesn't compose with SQL queries.
- **`chromadb`** — Heavier dependency, separate process, opinionated about embedding pipeline.
- **`lancedb`** — Reasonable alternative; chosen `sqlite-vec` because SQLite is more universally familiar and the extension footprint is smaller.

---

## Database File Layout

A single `.db` file contains everything:

```
memory.db
├── nodes (table)
├── edges (table)
├── scopes (table)
├── sessions (table)
├── audit (table)
├── vec_nodes (virtual table — sqlite-vec)
├── vector_meta (table)
└── schema_version (table)
```

The full DDL is documented in `docs/SCHEMA.md`.

---

## Storage Locations

### Local Mode

```
<project-root>/.context-mesh/
├── memory.db          ← the SQLite database
├── config.toml        ← project-specific config
└── sync.log           ← sync history
```

The `.context-mesh/` directory is added to `.gitignore` by default during `init`. Memory data is local and not committed (committing it would expose private memories).

### Federation Mode

```
~/.context-mesh/
├── hubs/
│   └── <hub-name>/
│       ├── memory.db    ← local mirror of the hub
│       └── sync.log
└── config.toml          ← global config
```

Each hub the user connects to gets its own local mirror. Sync is bidirectional; the hub itself is hosted on a team-owned machine or service.

### Hub Mode (Server-Side)

```
<hub-server>/data/
├── memory.db            ← authoritative database
├── auth/                ← access tokens, scopes
└── audit/               ← centralized audit logs
```

The hub is just a SQLite database with an HTTP/gRPC API in front of it. Users self-host. There is no cloud service in v1.

---

## Concurrency

SQLite handles `context-mesh`'s concurrency profile well:

- **Many concurrent reads.** Retrieval is read-only; SQLite supports this natively at high throughput in WAL mode.
- **Occasional writes.** Distillation produces nodes once per agent session (not high-frequency). Sync produces batched writes.
- **Single-writer constraint.** SQLite serializes writes. This is acceptable because we don't have write contention; distillation is async, sync is batched.

For the hub, write throughput might eventually require WAL tuning or a Postgres migration. The adapter interface makes this swap straightforward.

---

## Performance Characteristics

Approximate numbers (to be validated by benchmarks):

- **Vector search over 100K nodes:** sub-100ms with sqlite-vec.
- **Graph traversal (1-2 hops over 100K edges):** sub-50ms.
- **Hybrid retrieval (vector + graph + ranking):** sub-300ms end-to-end.
- **Storage size:** ~5-10KB per memory node (body + structured fields + 384-dim vector).

These are starting estimates. Real benchmarks land alongside the retrieval implementation.

---

## Indexing Strategy

Indexes mirror the dominant query patterns:

| Index | Purpose |
|---|---|
| `idx_nodes_kind` | Filter by memory kind (episodic/semantic/procedural). |
| `idx_nodes_scope` | Filter by privacy scope. |
| `idx_nodes_repo` | Filter by source repository (cross-repo retrieval). |
| `idx_nodes_decayed` | Skip decayed nodes during retrieval. |
| `idx_edges_from` / `idx_edges_to` | Fast graph traversal in either direction. |
| `idx_edges_relation` | Filter by relation type during traversal. |
| `vec_nodes` (sqlite-vec) | Vector similarity search. |
| `idx_audit_timestamp` | Audit log queries by time. |

We deliberately do NOT add indexes for fields we haven't proven need them. Indexes are added when query patterns demand them; not preemptively.

---

## Backup & Recovery

**Backup is trivial: copy the `.db` file.** SQLite's atomic file format makes this safe even during writes (use `.backup` from the SQLite CLI for guaranteed consistency).

**Recommended backup strategy:**
- Local mode: nightly snapshot of `memory.db` to local backup.
- Federation mode: hub server takes nightly snapshots; client mirrors are recoverable from the hub.
- Optional: commit the hub's `memory.db` to a backup git repo for versioned audit history.

**Recovery:** restore the file. Done.

---

## Security At Rest

- **Local files** rely on filesystem permissions. The `.context-mesh/` directory inherits the user's permissions; on multi-user systems, set explicit umask.
- **Secrets in memories** must be redacted by the distillation engine BEFORE storage. This is a hard requirement, not optional. See `docs/SECURITY_PRIVACY.md` for redaction rules.
- **Encryption at rest** is not enabled by default in v1 (SQLite supports SQLCipher; we'll add as an optional adapter when there's user demand).

---

## Migrations

Schema changes are managed via numbered SQL migration files in `src/storage/migrations/`:

```
src/storage/migrations/
├── 0001_initial_schema.sql
├── 0002_add_decay_column.sql
└── 0003_...
```

The library applies pending migrations automatically on startup. The `schema_version` table tracks which migrations have been applied.

Migration policy:
- Forward migrations only. No down-migrations in v1 (rare and hard to test).
- Always include a backup step before destructive changes.
- Major-version schema changes require explicit `context-mesh upgrade` from the user.

---

## When SQLite Stops Being Enough

`context-mesh` is built to outgrow SQLite gracefully. Triggers for migrating to a different backend:

| Trigger | Recommended next step |
|---|---|
| > 1M nodes per repo | Move to Postgres + pgvector. |
| > 10M nodes total in hub | Move to Qdrant or Weaviate for vector layer; Postgres for relational. |
| Cross-region replication required | Move to Postgres with read replicas. |
| Hard real-time write throughput requirements | Move to a streaming-friendly backend; SQLite's single-writer becomes the bottleneck. |
| Multi-tenant SaaS deployment | Custom backend; SQLite is single-tenant per file. |

In all cases, the storage adapter interface (`StorageBackend` trait) is the swap point. Application logic doesn't change.

---

## What Lives In Storage vs. What Doesn't

**In storage:**
- Memory nodes (distilled).
- Edges.
- Vectors (with `sqlite-vec`).
- Scopes, sessions, audit logs.

**NOT in storage:**
- Raw session transcripts. We store a pointer (URI) to where the transcript lives (e.g., on the Entire checkpoints branch), but we never persist the raw text in `memory.db`. Transcripts are reference data, not memory data.
- Source code. Same reasoning.
- LLM responses verbatim. Distilled fields only.
- User identity beyond scope membership.

The principle: **memory is a structured, queryable artifact derived from raw data — not the raw data itself.**
