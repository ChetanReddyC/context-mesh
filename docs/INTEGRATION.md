# Integration

`context-mesh` is designed to compose with existing tools, not replace them. This document describes how it integrates with each major surface in the AI-coding-agent ecosystem.

---

## Integration Philosophy

Three principles drive integration design:

1. **Adapters, not forks.** We do not modify upstream tools. We provide adapters that read their data formats and emit our memory nodes.
2. **Read-write parity.** Where another system already captures useful data (e.g., Entire's checkpoints), we read from it. Where the agent needs to consult memory, we expose tools.
3. **No required dependencies.** Every adapter is optional. The system runs with zero adapters as a standalone library.

---

## Integration 1: Entire.io Checkpoints

[Entire](https://entire.io) captures AI-agent session metadata as Git-native checkpoints on a dedicated branch (`entire/checkpoints/v1`). This is the highest-value source of raw data for `context-mesh`.

### What We Read

Each commit on the configured branch (`entire/checkpoints/v1` by default) carries a JSON object as its commit-message body. The adapter — `EntireAdapter` in `context_mesh.adapters` — walks the branch in `git log --reverse` order and parses each message body into a `SessionReference`:

| JSON field | Required | Notes |
| --- | --- | --- |
| `session_id` | yes | string |
| `agent` | yes | string (`claude-code`, `cursor`, ...) |
| `started_at` | yes | epoch integer or ISO-8601 string |
| `ended_at` | no | same shape as `started_at` |
| `repo` | no | falls back to the repo's directory name |
| `branch` | no | string or null |
| `commit_sha` | no | string or null |
| `turn_count` | no | int |
| `token_usage` | no | int |
| `file_changes` | no | array of strings |
| `transcript` | yes (on `fetch`) | the literal session text |

Empty bodies, non-JSON bodies, non-object payloads, missing required keys, and type mismatches emit a `malformed_checkpoint` warning and skip the commit.

### What We Produce

For each checkpoint that survives parsing, the distillation engine produces zero or more `MemoryNode`s — typically one or two, with kinds and structured fields driven by the transcript text. Intra-session edges (`semantic→episodic` as `generalizes`, `procedural→episodic` as `applies_to`) are inferred at distill time. Edges linking back to a node's source session live in the node's `source_session_id` foreign key.

### How The Adapter Works

1. **Discovery.** `EntireAdapter.discover()` shells out to `git rev-parse`, `git log --reverse`, and `git show -s --format=%B`. Read-only over the git tree.
2. **Cursor + seen-set.** Durable state lives in `adapter_sync_state` (one row per adapter, keyed by `name`). `cursor` is the SHA of the last successfully processed commit; `state["seen_shas"]` is a sorted JSON array of every commit that has already been ingested. The cursor is an optimization (the next walk uses `<cursor>..<branch>`); the seen-set is the authoritative idempotency guarantee.
3. **Force-push recovery.** If the cursor SHA becomes unreachable from the branch (force-push or rebase), the adapter logs `entire_cursor_unreachable` and falls back to walking the full branch. The seen-set still filters out previously-ingested commits, so duplicate work is avoided across rewrites.
4. **Distillation.** Each checkpoint's `transcript` is passed through the configured `Distiller`.
5. **Persistence.** Resulting nodes are written to the `context-mesh` store, and `merge_state` updates the cursor + seen-set in a single `Mesh.set_sync_state` call.

The adapter is read-only over the git tree: it never writes commits, branches, hooks, or refs.

### Composability With Entire's Semantic Search

Entire's own semantic search operates on raw checkpoint content. `context-mesh` operates on distilled, structured memories. They serve different purposes:

- Entire semantic search: *"show me all sessions where someone debugged Stripe webhooks."* → returns raw transcripts.
- `context-mesh`: *"give me the structured rules and patterns we learned about Stripe webhooks."* → returns distilled, queryable knowledge.

A team can use both. They are not competitors; they are layers.

`context-mesh` operates on the Entire **checkpoints** primitive (the open-source, shipped CLI surface).

See `docs/ADAPTERS.md` for the full reference (Protocol contract, error events, JSON schema, sync state shape).

---

## Integration 2: `.agent-memory/` directories

For users with existing `.agent-memory/` directories (a single-repo persistent memory format), `context-mesh` ships an adapter — `AgentMemoryAdapter` in `context_mesh.adapters` — that imports records into the mesh.

### Frontmatter grammar

Each `.md` file in `.agent-memory/` is treated as one session transcript. Files must begin with a YAML-style fenced frontmatter block:

```
---
session_id: <string>      # required
agent: <string>           # required
started_at: <epoch|ISO>   # required
ended_at: <epoch|ISO>     # optional
turn_count: <int>         # optional
token_usage: <int>        # optional
repo: <string>            # optional; falls back to the repo's directory name
branch: <string>          # optional
commit_sha: <string>      # optional
tags: a, b, c             # optional, comma-separated
---
<transcript body>
```

Blank lines and `#`-comment lines inside the frontmatter are ignored. Malformed lines, missing or unterminated `---` fences, and missing required keys emit a `malformed_frontmatter` warning and skip the file.

### Cursor + seen-set mechanism

`seen_key` is the SHA-256 of the file's raw bytes; the seen-set lives in `state["seen_hashes"]` (sorted JSON array). The cursor is the ISO-8601 UTC timestamp of the last `merge_state` call — informational only, since the seen-set is the authoritative filter. Re-running a sync against an unchanged `.agent-memory/` directory is a no-op. The `.agent-memory/` directory itself is allowed to be missing; the adapter logs `agent_memory_dir_missing` once and returns an empty discovery list.

See `docs/ADAPTERS.md` for the full reference.

---

## Integration 3: Claude Code

Claude Code supports tool use natively via its hooks system and tool definitions.

### Tools Exposed To Claude Code

The adapter registers `context-mesh`'s five tools:
- `search_team_memory`
- `drill_down_memory`
- `add_memory`
- `mark_memory_used`
- `find_contradictions`

Claude can call these directly during a session. Responses are formatted for token efficiency.

### Hooks Used

- **`UserPromptSubmit`** — optionally emit a system-prompt hint reminding the agent that memory tools are available. Soft nudge only; no auto-injection.
- **`PostToolUse`** — if the user's commit triggers distillation, the adapter dispatches asynchronously.
- **`Stop`** — at session end, finalize any in-progress distillation and queue federation sync.

### Setup

A single CLI command:

```bash
context-mesh install claude-code
```

This writes the appropriate config to `.claude/` and verifies the integration.

---

## Integration 4: Cursor

Cursor supports tool use via its extension system.

### Extension Pattern

The Cursor extension registers `context-mesh`'s tools in the same way they're exposed to Claude Code. The extension package wraps the underlying CLI calls.

### Setup

Install the `context-mesh` extension from the Cursor marketplace (post-v1 release). The extension is configured via a project-local `.context-mesh/cursor.toml`.

---

## Integration 5: OpenAI Codex CLI

Codex's CLI exposes tool-use through its function-calling API.

### Tool Bridge

A small shim exposes `context-mesh`'s tools as OpenAI function-call definitions. The shim is shipped as part of the `context-mesh` library and invoked via the CLI:

```bash
context-mesh expose-tools --format openai-functions > tools.json
```

The user passes `tools.json` to their Codex invocation.

---

## Integration 6: Generic Agent Adapter Interface

For any agent tool not natively supported, `context-mesh` exposes a **generic protocol**:

### The Protocol

- HTTP API on `localhost:` (configurable port).
- JSON request/response.
- Endpoints mirror the tool surface (`POST /search`, `POST /drill_down`, etc.).
- Authentication via local-only token (no remote access in v1).

Any agent or framework can integrate by speaking this protocol. The protocol is documented in `docs/API_DESIGN.md`.

### Why HTTP

It's the lowest-common-denominator interface. Every agent framework, every language, every tool can speak HTTP. No SDK required.

---

## Integration 7: Git Hooks

`context-mesh` can install Git hooks that automate distillation:

- **`post-commit`** — after each commit, kick off distillation of the committed session's transcript.
- **`post-merge`** — re-sync the local store with the team mesh hub.
- **`post-checkout`** — pull memories scoped to the checked-out branch.

Hook installation is opt-in:

```bash
context-mesh install git-hooks
```

The hooks are minimal shell scripts that invoke `context-mesh` commands; they don't modify other git behavior.

---

## Integration 8: CI/CD

Memory can flow into CI pipelines for automated quality checks.

### Use Case: PR Review

A `context-mesh` GitHub Action can run on PR creation:
- Distill the PR's session (if Entire was used).
- Identify any contradictions with existing semantic memories.
- Comment on the PR if conflicts are detected.

### Use Case: Memory-Aware Linting

A custom linter can query `context-mesh` for relevant rules and check the diff against them:
- "Auth changes detected; surfacing relevant team rules: ..."
- "Migration detected on `orders` table; surfacing maintenance-window requirement."

### Use Case: Continuous Distillation

A scheduled job can periodically distill any new checkpoints, sync to the hub, and surface analytics:
- New memories created this week.
- Contradictions detected.
- Most-retrieved memories.

The CI integration is provided as a separate Action in v1.x; not in core v1.

---

## Integration 9: Observability Tools

`context-mesh` emits structured logs and metrics. Standard observability backends can ingest these:

- **OpenTelemetry traces** — every retrieval and distillation produces a trace.
- **Prometheus metrics** — counters for retrievals, distillations, sync events; histograms for latency.
- **Structured JSON logs** — every audit event is logged as JSON for ingestion into Datadog, Splunk, Loki, etc.

Configuration in `docs/OBSERVABILITY.md`.

---

## What We Will NOT Integrate (v1 Scope)

To stay focused, the following integrations are out of scope for v1:

- **Slack / Teams / Discord bots** — chat integrations are a v2 concern.
- **Confluence / Notion connectors** — wiki integration adds significant complexity.
- **JIRA / Linear** — issue tracker integration is interesting but not core.
- **IDE plugins beyond Claude Code and Cursor** — VS Code, JetBrains, etc. are post-v1.

---

## Adapter Development Guide

For contributors writing new adapters, see `docs/EXTENSIBILITY.md`. The adapter interface is small and stable.
