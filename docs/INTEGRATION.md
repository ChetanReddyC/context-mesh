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

For each Entire checkpoint:
- The session transcript (prompts, responses, tool calls).
- The agent identifier (`claude-code`, `cursor`, etc.).
- File modifications (paths, diffs).
- Token usage.
- Commit metadata (SHA, branch, author).

### What We Produce

For each checkpoint, the distillation engine produces:
- One or more **episodic** nodes (capturing the session's events).
- Potentially one or more **semantic** nodes (if the session yielded generalizable rules).
- Potentially one or more **procedural** nodes (if the session captured a workflow).
- Edges linking back to the source session.

### How The Adapter Works

1. **Discovery.** The adapter reads the `entire/checkpoints/v1` branch via standard git operations (`git log`, `git show`).
2. **Incremental sync.** Only checkpoints newer than the last sync timestamp are processed.
3. **Distillation.** Each checkpoint's transcript is passed through the distillation engine.
4. **Persistence.** Resulting nodes/edges are written to the `context-mesh` store.
5. **Hooks.** The adapter optionally installs a `post-commit` hook that triggers distillation automatically.

### Composability With Entire's Semantic Search

Entire's own semantic search operates on raw checkpoint content. `context-mesh` operates on distilled, structured memories. They serve different purposes:

- Entire semantic search: *"show me all sessions where someone debugged Stripe webhooks."* → returns raw transcripts.
- `context-mesh`: *"give me the structured rules and patterns we learned about Stripe webhooks."* → returns distilled, queryable knowledge.

A team can use both. They are not competitors; they are layers.

### Composability With Entire's Forthcoming Memory Layer

Entire's public roadmap signals that a memory layer is part of their broader platform direction. When such a layer becomes publicly accessible:

- `context-mesh` can either ingest from it (treating it as an upstream source) or expose to it (treating it as a downstream consumer).
- The integration adapter will be added once a stable interface is published.

For now, `context-mesh` operates on the Entire **checkpoints** primitive (the open-source, shipped CLI surface).

---

## Integration 2: agent-memory

[`agent-memory`](https://github.com/ChetanReddyC/agent-memory) is the predecessor project — a single-repo persistent memory layer. `context-mesh` is its evolution: federated, graph-structured, agent-tool-accessible.

### Migration Path

For users of `agent-memory`:

1. The `agent-memory` adapter reads existing `.agent-memory/` directories.
2. Each `agent-memory` record maps to a `context-mesh` node:
   - The structured fields (decisions, failed_approaches, etc.) carry over directly.
   - The embedding is recomputed in the new system.
   - The session reference is preserved.
3. After migration, `agent-memory` users can either:
   - Run `context-mesh` alongside `agent-memory` (both populated, redundant).
   - Switch fully to `context-mesh` and retire the `agent-memory` install.

### What's Better In `context-mesh`

- Federation across repos.
- Memory kinds (episodic / semantic / procedural).
- Active retrieval as tool, not auto-injection.
- Graph relationships between memories.
- Privacy scopes.

### What `agent-memory` Did Right (And We Keep)

- The 5-signal scoring algorithm.
- The distillation pattern (Claude CLI → structured fields).
- The HuggingFace embedding default.
- The single-repo simplicity (still the default mode).

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
