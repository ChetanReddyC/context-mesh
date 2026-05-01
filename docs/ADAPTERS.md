git# Source Adapters

Source adapters pull raw session data from external systems and feed it
into the distillation engine. This document is the v1 reference: the
`SourceAdapter` Protocol, the two built-in adapters (`AgentMemoryAdapter`
and `EntireAdapter`), the orchestrator (`Mesh.sync`), the
`context-mesh sync` CLI, and the contract for writing your own adapter.

For the conceptual placement of adapters in the architecture, see
`docs/INTEGRATION.md`. For the wider extension model (storage,
embeddings, distillation, frontends), see `docs/EXTENSIBILITY.md`.

## Conceptual Model

An adapter is a one-way pipe from an external session source into the
mesh:

```
external source → discover() → fetch() → distill() → persist()
                       │           │
                       └───────────┴── seen-set + cursor
                                        (durable in adapter_sync_state)
```

Each adapter exposes a stable contract — the `SourceAdapter` Protocol —
and owns its own durable state (one row per adapter in the
`adapter_sync_state` table, keyed by `adapter_name`). The orchestrator
(`Mesh.sync`) sequences the calls, runs distillation, persists nodes,
and updates state in a single pass.

Adapters are **read-only** over their source. They never mutate the
filesystem, the git history, or any external service. All mutation
happens inside the mesh database.

## The SourceAdapter Protocol

`SourceAdapter` is a `@runtime_checkable` `typing.Protocol` defined in
`context_mesh.adapters.protocol`. Any object that satisfies the seven
members below is a valid adapter:

```python
class SourceAdapter(Protocol):
    @property
    def name(self) -> str: ...
    def discover(self) -> list[SessionReference]: ...
    def fetch(self, reference: SessionReference) -> SessionTranscript: ...
    def sync_state(self) -> SyncState | None: ...
    def set_sync_state(self, state: SyncState) -> None: ...
    def seen_key(self, reference: SessionReference) -> str: ...
    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]: ...
```

### Methods

- **`name`** — stable, unique identifier used as the row key in
  `adapter_sync_state` and as the CLI argument. Must be a non-empty
  string and stable across releases.

- **`discover()`** — list every `SessionReference` the adapter can
  produce *that has not yet been ingested*. Already-seen references are
  filtered out by the adapter (using the seen-set persisted in sync
  state). Order is meaningful for cursor-based adapters: oldest first.

- **`fetch(reference)`** — return the full `SessionTranscript` for a
  reference returned by `discover()`. Raising any exception causes the
  orchestrator to record a `SkipRecord(stage="fetch", ...)` and skip the
  reference; other references are still processed.

- **`sync_state()`** — return the current `SyncState` for this adapter
  (typically by delegating to `Mesh.get_sync_state(self.name)`), or
  `None` if no state is persisted yet.

- **`set_sync_state(state)`** — persist the given `SyncState`
  (typically via `Mesh.set_sync_state`). The orchestrator only calls
  this through `Mesh.set_sync_state` itself; adapters generally do not
  need to call it directly during a sync run.

- **`seen_key(reference)`** — return the stable, content-addressable
  identity of a reference (e.g. its content SHA-256 or the commit SHA
  the checkpoint lives at). Must be deterministic given the same
  reference.

- **`merge_state(existing, processed_refs)`** — given the previous
  `SyncState` (or `None`) and the references just successfully
  processed, return `(new_cursor, new_state_json)`. The orchestrator
  passes this back to `Mesh.set_sync_state`.

### Dataclasses

All three are frozen, slots-enabled, and live in
`context_mesh.adapters.protocol`:

- **`SessionReference`** — `adapter_name`, `source_id`, `repo`, `agent`,
  `started_at`, optional `branch` / `commit_sha` / `transcript_uri`, and
  a `metadata: tuple[tuple[str, str], ...]` for adapter-specific bytes
  (the seen-key lookup pulls from this tuple).

- **`SessionTranscript`** — wraps a `SessionReference` plus the literal
  transcript `text` and optional `ended_at` / `turn_count` /
  `token_usage`.

- **`SyncState`** — `adapter_name`, `cursor` (string),
  `last_synced_at` (epoch seconds, server-stamped on persist), and
  `state: Mapping[str, Any]` (free-form adapter JSON, e.g. seen-set
  arrays). The `last_synced_at` value supplied on input is advisory:
  `Mesh.set_sync_state` always stamps it server-side.

### Lifecycle invariants

1. `name` is constant for the lifetime of the adapter instance and
   stable across releases.
2. `discover()` and `fetch()` never write to the filesystem or external
   service.
3. `seen_key(ref)` returns the same value across processes for the same
   underlying source content.
4. `merge_state` is monotonic: re-running it with a superset of
   `processed_refs` yields a state with a superset of seen keys.
5. The adapter's own internal cache (e.g. `self._seen_hashes`) is
   re-hydrated lazily from `sync_state()` on first `discover()` call so
   a fresh adapter instance behaves identically to a long-lived one.

## Built-In Adapters

### AgentMemoryAdapter

`context_mesh.adapters.AgentMemoryAdapter`. Reads `.md` session
transcripts committed under a repo's `.agent-memory/` directory.

```python
from context_mesh.adapters import AgentMemoryAdapter
adapter = AgentMemoryAdapter(mesh, repo_path="./payments")
```

**Construction** — `repo_path` must exist and be a directory.
Constructor pre-resolves the path; the `.agent-memory/` subdirectory is
allowed to be missing (a warning is logged on the first `discover()`
call and an empty list is returned). `name` defaults to `"agent-memory"`.

**Frontmatter grammar.** Each `.md` file must begin with a YAML-style
fenced frontmatter block:

```
---
session_id: <string>      # required
agent: <string>           # required
started_at: <epoch|ISO>   # required
ended_at: <epoch|ISO>     # optional
turn_count: <int>         # optional
token_usage: <int>        # optional
repo: <string>            # optional; falls back to repo_path.name
branch: <string>          # optional
commit_sha: <string>      # optional
tags: a, b, c             # optional, comma-separated
---
<transcript body>
```

Lines that are blank or that start with `#` (after leading whitespace)
inside the frontmatter are ignored. Any other malformed line, or a
missing/unterminated `---` fence, or a missing required key emits a
`malformed_frontmatter` warning event and the file is skipped.

**Idempotency mechanism.** `seen_key` is the SHA-256 of the file's raw
bytes. The set lives in `state["seen_hashes"]` (sorted JSON array).
`discover()` skips any file whose digest is already in the seen set;
within a single walk, duplicate digests at different paths emit a
`duplicate_content` warning and only the first wins.

**Cursor format.** `cursor` is the ISO-8601 UTC timestamp of the last
`merge_state` call. The cursor is informational only — idempotency is
driven entirely by the seen-hash set, so re-running on an empty
working set is cheap.

**Error events.** `agent_memory_dir_missing` (warned once per adapter
instance), `empty_file`, `non_utf8`, `malformed_frontmatter`,
`duplicate_content`. All emit through `context_mesh._logging` at
`warning` level.

### EntireAdapter

`context_mesh.adapters.EntireAdapter`. Reads Entire-style checkpoint
commits from a dedicated git branch (default
`entire/checkpoints/v1`).

```python
from context_mesh.adapters import EntireAdapter
adapter = EntireAdapter(mesh, repo_path="./payments")
# or with overrides:
adapter = EntireAdapter(mesh, repo_path, branch="entire/checkpoints/v1",
                        name="entire", git_binary="git",
                        timeout_seconds=30.0)
```

**Construction** — resolves `repo_path` via `git rev-parse
--show-toplevel`. A path outside any git repository, or a missing `git`
binary, is rejected at construction. `branch` defaults to
`entire/checkpoints/v1`; the branch is allowed to be missing at
construction time (a warning is logged once on first `discover()` and
an empty list is returned).

**Commit-message JSON schema.** Each commit on the branch must carry a
JSON object as its message body:

```json
{
  "session_id":   "<string, required>",
  "agent":        "<string, required>",
  "started_at":   "<epoch int or ISO-8601 string, required>",
  "ended_at":     "<optional, same shape as started_at>",
  "repo":         "<optional string>",
  "branch":       "<optional string or null>",
  "commit_sha":   "<optional string or null>",
  "turn_count":   "<optional int>",
  "token_usage":  "<optional int>",
  "file_changes": ["<optional list of strings>"],
  "transcript":   "<required string when fetched>"
}
```

The commit message body is parsed as a single top-level JSON object.
Empty bodies, non-JSON bodies, non-object payloads, missing required
keys, and type mismatches (e.g. `agent` is not a string) emit a
`malformed_checkpoint` warning and the commit is skipped. Walks
proceed in `git log --reverse` order (oldest first).

**Branch convention.** The default branch name (`entire/checkpoints/v1`)
matches the public Entire CLI surface. Override `branch=` for custom
deployments.

**Cursor mechanics.** The cursor is the SHA of the last successfully
processed commit (or the empty string when no commits have ever been
processed). On each run the adapter walks `<cursor>..<branch>` if the
cursor is reachable, or the full branch otherwise. The seen-set in
`state["seen_shas"]` (sorted JSON array) is the authoritative
idempotency mechanism; the cursor is an optimization that lets the
walk start partway up the branch.

**Force-push recovery.** If the branch is rewritten such that the
cursor SHA is no longer reachable (`git merge-base --is-ancestor` fails),
the adapter logs a `entire_cursor_unreachable` warning and falls back
to walking the full branch. The seen-set still filters out commits
that have already been ingested, so duplicate work is avoided even
across rewrites.

**Error events.** `entire_branch_missing` (warned once),
`entire_cursor_unreachable`, `entire_log_decode_failed`,
`entire_show_failed`, `non_utf8_commit_message`, `malformed_checkpoint`.

## Sync State

### Table — `adapter_sync_state`

Defined in `src/context_mesh/storage/migrations/0002_adapter_sync_state.sql`:

```sql
CREATE TABLE adapter_sync_state (
  adapter_name TEXT PRIMARY KEY,
  cursor TEXT NOT NULL DEFAULT '',
  last_synced_at INTEGER,
  state_json TEXT NOT NULL DEFAULT '{}'
);
```

One row per adapter, keyed by `adapter_name`. `cursor` is human-readable
(an ISO timestamp, a git SHA, etc.). `last_synced_at` is a unix-epoch
integer, server-stamped by `Mesh.set_sync_state`. `state_json` is opaque
JSON that the adapter writes and reads through `merge_state`.

### `state_json` shapes

- `AgentMemoryAdapter` writes `{"seen_hashes": ["<sha256>", ...]}`,
  sorted lexicographically.
- `EntireAdapter` writes `{"seen_shas": ["<commit-sha>", ...]}`, sorted
  lexicographically.

The mesh validates that `state_json` is a JSON object on read; a
corrupted row raises `RuntimeError`.

## Orchestrator: `Mesh.sync`

`context_mesh.api.Mesh.sync` runs one full sync pass:

```python
def sync(
    self,
    *,
    adapter: SourceAdapter,
    distiller: Distiller,
    embedder: EmbeddingProvider,
    limit: int = 100,
    dry_run: bool = False,
    actor: str = "mesh:sync",
) -> SyncResult: ...
```

`embedder` is reserved for future per-sync embedding policies; v1
accepts it for API stability but does not currently thread it through
distillation. `limit` caps how many references are processed in a
single call (`>= 0`). `actor` is the audit-row actor.

The orchestrator:

1. Calls `adapter.discover()`.
2. Slices the result to `limit`.
3. For each reference, calls `adapter.fetch()`, ensures a `scopes` row
   for `"default"` and a `sessions` row for the reference, calls
   `Mesh.distill` for the transcript, and records the reference as
   processed if everything succeeded.
4. Calls `adapter.merge_state(existing, processed_refs)` to compute the
   new cursor + state.
5. Persists the new state via `Mesh.set_sync_state` (only if at least
   one reference was processed; an empty re-run leaves state alone).
6. Emits a single `sync_pull` audit row with the run summary.

The two `_ensure_*` helpers
(`Mesh._ensure_scope_row`, `Mesh._ensure_session_row`) use
`INSERT OR IGNORE` so a re-run is a no-op against existing rows.

### `SyncResult`

`context_mesh.adapters.SyncResult`:

| Field | Type | Description |
| --- | --- | --- |
| `adapter_name` | `str` | Echo of `adapter.name`. |
| `discovered` | `int` | Total references returned from `discover()` (before slicing to `limit`). |
| `fetched` | `int` | References that survived `fetch` + `_ensure_*` + `distill`. |
| `memory_nodes_added` | `int` | Sum of `len(distill(...))` across processed references. |
| `skipped` | `tuple[SkipRecord, ...]` | One entry per reference dropped at any stage. |
| `cursor` | `str` | New cursor returned by `adapter.merge_state`. |
| `state_keys_count` | `int` | Sum of lengths of every list in the new `state_json` (typically the seen-set size). |
| `elapsed_ms` | `int` | Wall-clock duration of the sync call. |
| `dry_run` | `bool` | `True` when `dry_run=True` was passed; the rest of the fields reflect the discover-only path. |

### `SkipRecord`

`context_mesh.adapters.SkipRecord`:

```python
@dataclass(frozen=True, slots=True)
class SkipRecord:
    source_id: str
    stage: SkipStage           # Literal["fetch", "distill", "persist"]
    reason: str
```

The orchestrator records:

- `stage="fetch"` when `adapter.fetch()` raises.
- `stage="persist"` when the `_ensure_*` upserts raise.
- `stage="distill"` when `Mesh.distill` raises (including
  `sqlite3.IntegrityError` on duplicate `content_hash`, with reason
  prefixed `duplicate content_hash:`).

### Dry-run semantics

`dry_run=True`:

- Calls `adapter.discover()` (read-only).
- Reports `discovered`, the existing cursor (or empty string), and the
  existing `state_keys_count`.
- Does **not** call `fetch`, `distill`, or `merge_state`.
- Does **not** emit a `sync_pull` audit row.
- Returns `SyncResult(dry_run=True, fetched=0, memory_nodes_added=0,
  skipped=())`.

Dry-run is the right way to inspect what *would* happen without
mutating the store.

### Audit emission

A real (non-dry-run) `Mesh.sync` emits a `sync_pull` audit row at the
end with metadata:

```json
{
  "action": "sync_run",
  "adapter": "<adapter.name>",
  "discovered": <int>,
  "fetched": <int>,
  "memory_nodes_added": <int>,
  "skipped_count": <int>,
  "limit": <int>,
  "dry_run": false
}
```

`Mesh.set_sync_state` separately emits a `sync_pull` row each time it
upserts state, with `metadata.action="set_sync_state"`. A successful
sync that processed at least one reference therefore leaves two
`sync_pull` rows: one for the state upsert, one for the run summary.

## CLI Usage

### `context-mesh sync <adapter>`

`<adapter>` is `agent-memory` or `entire`.

| Flag | Default | Description |
| --- | --- | --- |
| `--repo <path>` | cwd | Directory the adapter operates on. |
| `--branch <name>` | `entire/checkpoints/v1` | Git branch (Entire only; ignored by `agent-memory`). |
| `--limit <n>` | `100` | Max references to process. Range: `1..10000`. |
| `--dry-run` | off | Discover-only; print the references that would be ingested. |
| `--distiller <name>` | `heuristic` | `heuristic` or `claude-cli`. |
| `--json` | off | Emit a single JSON object instead of the text summary. |
| `--db <path>` | resolved | Override the database path. Resolution order matches every other CLI command (see `docs/CLI.md`). |

### Examples

```bash
# Dry-run the agent-memory adapter, print the references that would be ingested
context-mesh sync agent-memory --repo ./payments --dry-run

# Real sync against an Entire branch on a custom name
context-mesh sync entire --repo ./payments --branch entire/checkpoints/v1 \
    --limit 50 --json
```

### JSON output sample

```json
{
  "adapter_name": "agent-memory",
  "discovered": 1,
  "fetched": 1,
  "memory_nodes_added": 1,
  "skipped": [],
  "cursor": "2026-05-01T09:45:00.000000+00:00",
  "state_keys_count": 1,
  "elapsed_ms": 41,
  "dry_run": false
}
```

Dry-run JSON adds a `would_ingest` array of
`{"source_id", "repo", "agent"}` triples.

### Text output shape

```
adapter: agent-memory
discovered:         1
fetched:            1
memory_nodes_added: 1
skipped:            0
cursor:             2026-05-01T09:45:00.000000+00:00
state_keys_count:   1
elapsed_ms:         41
```

A dry-run prints `(dry-run)` next to the adapter name and a
`would ingest: <n> reference(s)` block listing each reference.

## Idempotency Proof

Re-running a sync against an unchanged source must be a no-op. Both
built-in adapters guarantee this through their seen-set:

```python
mesh = Mesh.local("memory.db")
distiller = HeuristicDistiller()
embedder = DeterministicEmbeddingProvider()

r1 = mesh.sync(adapter=AgentMemoryAdapter(mesh, repo),
               distiller=distiller, embedder=embedder)
r2 = mesh.sync(adapter=AgentMemoryAdapter(mesh, repo),
               distiller=distiller, embedder=embedder)

assert r2.discovered == 0
assert r2.memory_nodes_added == 0
```

Adding a new file or commit and re-running the sync picks up only the
delta — the seen-set filters every previously-ingested reference at
the `discover()` stage. `demo_phase5.py` exercises this end-to-end.

## Writing a Custom Adapter

Implement all seven Protocol members. The skeleton:

```python
from collections.abc import Sequence
from typing import Any
from context_mesh.adapters import (
    SessionReference, SessionTranscript, SourceAdapter, SyncState,
)
from context_mesh.api import Mesh


class MyToolAdapter:
    name = "my-tool"

    def __init__(self, mesh: Mesh, repo_path: str) -> None:
        self._mesh = mesh
        self._repo_path = repo_path
        self._seen: set[str] | None = None

    def discover(self) -> list[SessionReference]:
        if self._seen is None:
            state = self.sync_state()
            self._seen = set(state.state.get("seen_keys", [])) if state else set()
        # ...enumerate sessions, filter out keys already in self._seen,
        #    return list[SessionReference] sorted oldest-first.
        return []

    def fetch(self, reference: SessionReference) -> SessionTranscript:
        # ...read transcript text from your source...
        return SessionTranscript(reference=reference, text="...")

    def sync_state(self) -> SyncState | None:
        return self._mesh.get_sync_state(self.name)

    def set_sync_state(self, state: SyncState) -> None:
        self._mesh.set_sync_state(self.name, cursor=state.cursor, state=dict(state.state))
        self._seen = set(state.state.get("seen_keys", []))

    def seen_key(self, reference: SessionReference) -> str:
        return dict(reference.metadata)["my_tool_id"]

    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]:
        prior = list(existing.state.get("seen_keys", [])) if existing else []
        merged = sorted(set(prior) | {self.seen_key(r) for r in processed_refs})
        cursor = processed_refs[-1].source_id if processed_refs else (
            existing.cursor if existing else ""
        )
        return cursor, {"seen_keys": merged}


assert isinstance(MyToolAdapter(...), SourceAdapter)  # runtime_checkable
```

`SourceAdapter` is `@runtime_checkable`, so an `isinstance` assertion
gives you a fast smoke test that all required members are present.

To call it, pass an instance to `Mesh.sync(adapter=..., ...)`. To make
it accessible from the `context-mesh sync` CLI, wire it into your own
launcher; the bundled CLI only knows about `agent-memory` and
`entire` in v1.

## Concurrency and Safety

- Adapters and `Mesh.sync` are designed for **single-threaded** use.
  SQLite connections are not safe to share across threads, and the
  seen-set caches on adapter instances are not synchronized.
- Two parallel `Mesh.sync` calls against the same database are not
  supported. The adapter sync state is not lock-protected; the second
  caller will silently overwrite the first.
- A failed sync run leaves the store consistent: each successful
  `(fetch → distill → persist)` triple commits independently, and the
  final `set_sync_state` only fires after at least one reference was
  processed. A crash mid-run leaves the seen-set unchanged for the
  unprocessed references, so the next run will retry them.
- Adapters never delete or update existing memory nodes. Re-distilling
  the same transcript would normally produce a duplicate
  `content_hash`; the orchestrator catches `sqlite3.IntegrityError` at
  the distill stage and records a skip rather than aborting the run.

## See Also

- `docs/INTEGRATION.md` — adapter-layer placement in the broader
  ecosystem (Entire, agent-memory, Claude Code, Cursor).
- `docs/EXTENSIBILITY.md` — the five extension points;
  `SourceAdapter` is point 4.
- `docs/SCHEMA.md` — the `adapter_sync_state` table.
- `docs/CLI.md` — the `context-mesh sync` reference.
- `docs/API_DESIGN.md` — `Mesh.sync`, `Mesh.get_sync_state`,
  `Mesh.set_sync_state`.
