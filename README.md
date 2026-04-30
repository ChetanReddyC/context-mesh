# context-mesh

> A federated, agent-native, organization-scale memory system for AI coding agents.

`context-mesh` is an open-source memory layer that lets every AI coding agent across every repo in a team draw from a shared, semantically-organized cognitive store — without burning context window or producing noise.

It is the answer to a specific problem: *agent memory that actually works at organization scale, without polluting prompts.*

---

## The Problem

When AI agents work on code, they accumulate hard-won knowledge — debugging insights, rejected approaches, team-specific constraints, recurring failure patterns. Today, that knowledge is trapped:

- Inside one session's transcript, lost when the session ends.
- Inside one developer's head, lost when they switch projects.
- Inside one team's Slack threads, lost in scroll.
- Inside one repo's commit history, invisible across other repos.

When a new agent session starts, it begins from scratch — repeating yesterday's mistakes, rediscovering yesterday's lessons, asking yesterday's questions.

The naive solution — dump every past memory into the prompt — does not work. It eats the context window, confuses the agent with irrelevant noise, and degrades performance more often than it helps. (See: [the agent-memory benchmark report](https://github.com/ChetanReddyC/agent-memory) for a 22-bug study confirming this.)

The real solution is **structured, queryable, federated memory** that the agent **actively consults** — like a senior engineer asking a colleague *"have we hit this before?"* — rather than receiving an unsolicited dump of history.

---

## The Solution

`context-mesh` provides:

1. A **knowledge graph + vector embedding** memory store. Memories are nodes with structured fields and vectors; edges encode relationships (`caused_by`, `applies_to`, `contradicts`, `generalizes`, `supersedes`).
2. **Three memory kinds** — episodic (what happened), semantic (generalized rules), procedural (how-to). Each kind has its own retrieval logic and decay rules.
3. **Active retrieval as a tool** — agents call `search_team_memory` when they need context, instead of being force-fed memory at session start. Just-in-time, not just-in-case.
4. **Federation across repos and teams** — memories travel through a centralized mesh store with privacy boundaries. A lesson learned in Repo A surfaces in Repo B when relevant.
5. **A pluggable adapter layer** — integrates with existing checkpoint primitives (e.g., [Entire.io](https://entire.io)), with `agent-memory`, with Claude Code, with Cursor, with any agent that supports tool use.

---

## How It Differs From Existing Approaches

| Approach | What it gets right | What it misses |
|---|---|---|
| **Stateless agents (Claude Code default)** | Simple, predictable | Repeats yesterday's mistakes |
| **Auto-injection (most "agent memory" libraries)** | Easy to retrofit | Eats context, adds noise, degrades performance |
| **Vector RAG over chat logs** | Semantically searchable | No structural relationships; flat retrieval; no federation |
| **Personal memory (per-user)** | Tracks individual preferences | Doesn't capture team-level wisdom |
| **`context-mesh`** | Structured graph + active retrieval + federation + memory kinds | (Real engineering trade-offs documented in `docs/`) |

---

## Status

- ✅ Architecture and design committed
- ✅ Schema, storage, retrieval, and integration designs documented
- ✅ Phase 0 foundation shipped — SQLite + sqlite-vec storage, migration runner, full v1 schema, structlog + audit, `context-mesh init` CLI
- 🚧 Phase 1+ in progress — core CRUD, embeddings, retrieval, distillation, federation

See `docs/` for the reference architecture and design rationale.

---

## Quickstart (Once Released)

> *Note: this section describes the intended interface. The library is not yet implemented. The shape of the interface is documented in `docs/API_DESIGN.md`.*

```bash
# Install (Python)
pip install context-mesh

# Initialize for the current project
context-mesh init

# Connect to your team's mesh hub
context-mesh connect <hub-url>

# Run a session — memories surface automatically through the agent's tool calls
# (No further user action required.)
```

---

## Where To Go Next

| You want to... | Read |
|---|---|
| See the full technical architecture | `docs/ARCHITECTURE.md` |
| Look at the data schema | `docs/SCHEMA.md` |
| Understand storage choices | `docs/STORAGE_DESIGN.md` |
| Understand how agents retrieve memory | `docs/RETRIEVAL_DESIGN.md` |
| Know how to contribute | `CONTRIBUTING.md` |
| Onboard as an AI agent working on this project | `CLAUDE.md` |

---

## License

MIT. See `LICENSE.md`.

---

## Acknowledgements

Influenced by Microsoft's GraphRAG, Letta/MemGPT's hierarchical memory pattern, Anthropic's research on agent memory effectiveness, and the broader emerging ecosystem of agent-native developer infrastructure (including [Entire.io](https://entire.io)'s open-source CLI for git-native session capture).
