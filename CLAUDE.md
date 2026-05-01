# Agent Brief — context-mesh

> **Read this first.** This file is the canonical onboarding document for any AI agent (Claude, Cursor, etc.) opening this project for the first time. By the end of this file you should understand the project's mission, the technical architecture at a glance, where to find every other piece of documentation, and the operating principles you must follow.

---

## 1. Project Identity

**Name:** `context-mesh`

**One-line description:** A federated, agent-native, organization-scale memory system that lets every AI coding agent across every repo in a team draw from a shared, semantically-organized cognitive layer — without burning context window or producing noise.

**Type:** Open-source library + CLI + integration adapters. MIT-licensed.

**Status:** Phase 0 foundation shipped — SQLite + sqlite-vec storage, migration runner, full v1 schema, structlog + audit, `context-mesh init` CLI. Phase 1+ in progress.

---

## 2. Project Norms

**Communication style for working in this repo:**
- Crisp, no-fluff explanations.
- Honest critique over flattery.
- Builder-to-builder tone, not corporate.
- Comfortable with "I don't know yet, let's figure it out."

**Working style:**
- Sequential agent operations, not parallel (parallelism wastes tokens).
- Top 1% quality bar on every sub-agent task.
- Plan before code. Verify before commit.
- Never auto-attribute AI authorship in commits ("Co-Authored-By: Claude" → forbidden).

---

## 3. Technical Context

`context-mesh` sits in the same problem space as recent work on agent-native developer infrastructure (e.g., [Entire.io](https://entire.io), Letta/MemGPT, Anthropic's research on agent memory).

The technical relationship to existing platforms:
- Tools like Entire own the **capture layer** (git-native session metadata, checkpoints).
- The **semantic search** and **internal memory system** layers are emerging across the field.
- `context-mesh` sits at the **organization-scale, agent-accessible memory mesh** layer — it can compose with capture-layer tools (via adapters) or operate as a standalone system.

The intent is a real engineering primitive: production-grade, open-source-grade, with an architecture grounded in measured trade-offs rather than vibes. The active-retrieval-over-passive-injection design is grounded in a 22-bug benchmark study showing that auto-injected memory hurts more often than it helps when the injection is noisy.

---

## 4. Architectural Summary (One Page)

**Core idea:** memory is stored as a **knowledge graph of vector-embedded nodes** (KG+V), and exposed to agents as an **active-retrieval tool** rather than auto-injected into every prompt.

**The five primitives:**

1. **Memory nodes** — atomic units of distilled agent reasoning. Each node has a raw text body, structured fields (decisions, file paths, error codes, tags), an embedding vector, and a memory-kind label (episodic / semantic / procedural).
2. **Memory edges** — typed relationships between nodes. Examples: `caused_by`, `applies_to`, `contradicts`, `generalizes`, `supersedes`. Edges turn the memory store from a flat bag into a structured graph.
3. **Storage layer** — SQLite + the `sqlite-vec` extension. Single-file, embedded, fast, easy to back up, easy to demo. The graph (nodes + edges) lives in standard tables; vectors live in `sqlite-vec` indexes.
4. **Retrieval engine** — hybrid: vector similarity → top-K nodes → graph traversal (1-2 hops) → cluster return. Ranks combine semantic similarity, edge structure, recency, and importance.
5. **Active retrieval surface** — agents access memory by **calling a tool** (`search_team_memory`, `drill_down_memory`, `add_memory`), not by receiving auto-injected context. Memory is just-in-time, not just-in-case.

**Three memory kinds:**
- **Episodic** — what happened in a specific session (decays fast).
- **Semantic** — generalized rules that persist (the team's "law book").
- **Procedural** — how to do specific tasks (deploy commands, dev setup steps).

**Federation model:**
- Each project's local memory writes to a **shared mesh store**.
- Privacy tags (`private` / `team` / `org`) gate cross-repo visibility.
- A central SQLite hub is the source of truth; clients sync.

For details, read `docs/ARCHITECTURE.md`.

---

## 5. Tech Stack (Committed Choices)

- **Language:** Python (primary library + CLI). TypeScript adapter for Node-based agent tools.
- **Storage:** SQLite + `sqlite-vec` extension. Single-file deployable.
- **Embeddings:** Pluggable. Default = HuggingFace Inference API (`all-MiniLM-L6-v2`, 384-dim). Pluggable to OpenAI, Anthropic, local models, etc.
- **Distillation:** Pluggable. Default = Claude CLI (Sonnet) when available, falls back to heuristic extraction.
- **CLI:** Typer (Python). Click as fallback.
- **Config:** TOML (`config.toml` per-project + `~/.context-mesh/config.toml` global).
- **Build / deps:** `uv` with `pyproject.toml` and `[dependency-groups]`.
- **Testing:** pytest. Hypothesis for property-based tests where useful.
- **Lint / format / types:** ruff + mypy `--strict`.
- **CI:** GitHub Actions, Linux, Python 3.11/3.12/3.13 matrix.
- **Docs:** Markdown. MkDocs Material for the website.
- **License:** MIT.

For rationale, read `docs/STORAGE_DESIGN.md`, `docs/DESIGN_PRINCIPLES.md`, and `docs/ADR/0001-phase0-stack.md`.

---

## 6. Project Structure

```
context-mesh/
├── README.md                      Public-facing project overview
├── CLAUDE.md                      THIS FILE — agent onboarding
├── LICENSE.md                     MIT license
├── CONTRIBUTING.md                How to contribute
├── CODE_OF_CONDUCT.md             Standard OSS code of conduct
├── CHANGELOG.md                   Release notes
├── pyproject.toml                 Build config + tool config
├── uv.lock                        Locked dependency tree
│
├── docs/                          Architectural & design documentation
│   ├── ARCHITECTURE.md            Full system architecture
│   ├── SCHEMA.md                  Data schema (nodes, edges)
│   ├── STORAGE_DESIGN.md          SQLite + sqlite-vec specifics
│   ├── RETRIEVAL_DESIGN.md        Active retrieval algorithm + tool surface
│   ├── MEMORY_TYPES.md            Episodic / semantic / procedural
│   ├── INTEGRATION.md             Adapter integration with Entire and others
│   ├── API_DESIGN.md              Public API surface (CLI + library + agent tools)
│   ├── OBSERVABILITY.md           Logging, tracing, metrics
│   ├── SECURITY_PRIVACY.md        Privacy boundaries, access policies
│   ├── EXTENSIBILITY.md           Plugin / adapter architecture
│   ├── DESIGN_PRINCIPLES.md       Engineering values
│   └── ADR/                       Architecture Decision Records (versioned)
│
├── src/context_mesh/              Source code
│   ├── _audit.py                  Append-only audit log
│   ├── _logging.py                Structlog configuration
│   ├── cli/                       Typer CLI app
│   └── storage/                   SQLite + sqlite-vec backend + numbered migrations
│
├── tests/                         Test suite (pytest)
│
└── .github/                       CI workflows + community templates
```

---

## 7. Where To Find Each Piece Of Information

| If you need to know... | Read this |
|---|---|
| What this project is, in plain English | `README.md` |
| The full system architecture | `docs/ARCHITECTURE.md` |
| Exact data schema (tables, fields, types) | `docs/SCHEMA.md` |
| Why we picked SQLite, how it's structured | `docs/STORAGE_DESIGN.md` |
| How agents access memory at runtime | `docs/RETRIEVAL_DESIGN.md` |
| What the three memory kinds are and why | `docs/MEMORY_TYPES.md` |
| How this composes with Entire and other adapters | `docs/INTEGRATION.md` |
| The CLI commands and library functions | `docs/API_DESIGN.md` |
| Privacy and access controls | `docs/SECURITY_PRIVACY.md` |
| The engineering values driving every decision | `docs/DESIGN_PRINCIPLES.md` |
| Locked technical decisions (versioned ADRs) | `docs/ADR/` |
| How to set up dev environment and run quality checks | `CONTRIBUTING.md` |

---

## 8. Operating Principles (Non-Negotiable)

These are NOT preferences. They are rules every agent working on this project MUST follow.

1. **Top 1% quality bar.** Every sub-task is performed at the level of a top-1% professional in that field — top 1% Python engineer, top 1% test writer, top 1% docs author, top 1% systems architect.
2. **Plan before code.** No implementation begins without an explicit plan covering what will be built, what files will change, what tests will exist, and what could go wrong.
3. **Sequential agents only.** When delegating to sub-agents, run them one at a time. No parallel agents — parallelism wastes tokens and creates conflicts on shared state.
4. **No AI attribution in commits.** Never add "Co-Authored-By: Claude" or any AI authorship marker. Commit messages are written as the project's own.
5. **Match scope, don't expand.** A bug fix doesn't include surrounding refactors. A new feature doesn't drag in tangential cleanup. Stay precisely on task.
6. **No backwards-compat shims, no future-proofing for hypothetical requirements.** Build what's needed now. Three similar lines beat a premature abstraction.
7. **Default to no comments.** Add a comment only when the WHY is non-obvious. Don't narrate WHAT the code does.
8. **Verify before reporting done.** Type-checking and test-passing aren't proof of correctness. Run the actual feature. Run integration tests. If you can't verify, say so explicitly.
9. **Be honest about uncertainty.** If a design choice is unclear, surface the trade-off rather than picking arbitrarily.
10. **Every commit message gets a grammar pass.** Crisp imperative sentences. No typos. No run-ons.

---

## 9. Current State

Phases 0–5 shipped: foundation, CRUD, embeddings/retrieval, distillation,
agent surfaces (CLI, HTTP server, tool schemas, config), and source
adapters with sync orchestrator. Phase 6+ in progress.

- ✅ Phase 0 foundation: project scaffold, ADR-0001 (20 locked decisions); structlog + audit; CI matrix; pre-commit hooks.
- ✅ Storage: `SqliteVecBackend`, migration runner, full v1 schema (now 9 tables including `adapter_sync_state`, plus vec0 virtual table).
- ✅ Phase 1 — core memory CRUD: `Mesh.add` / `get` / `list_nodes` / `update` / `delete` / edge CRUD / vector storage with two-table coordination, audit on every mutation.
- ✅ Phase 2 — embeddings & retrieval: `EmbeddingProvider` Protocol, deterministic + HuggingFace providers, `Mesh.search` hybrid retrieval (vector kNN + 1-hop graph expansion + composite ranking + quality gate).
- ✅ Phase 3 — distillation: 15-category secret redactor, kind classifier, `HeuristicDistiller`, `ClaudeCliDistiller` with heuristic fallback, `Mesh.distill` with intra-session edge inference.
- ✅ Phase 4 — agent surfaces:
  - CLI: `init`, `search`, `add`, `show`, `list`, `delete`, `distill`, `stats`, `audit`, `tools`, `serve`, `config`.
  - Library API gaps: `Mesh.find_contradictions`, `Mesh.mark_used`, `Mesh.from_config`.
  - HTTP server: stdlib `ThreadingHTTPServer` exposing 6 endpoints with bearer-token auth.
  - Agent tool schemas: `ANTHROPIC_TOOLS` / `OPENAI_TOOLS` / `MCP_TOOLS`, `tool_for(name, dialect)` lookup.
  - Configuration: `context_mesh.config.load_config` with defaults / global / project / env layering, three env-var overrides, `ConfigError` on bad values.
- ✅ Phase 5 — source adapters:
  - `SourceAdapter` Protocol (7 members), `SessionReference` / `SessionTranscript` / `SyncState` dataclasses, process-local registry.
  - `adapter_sync_state` table (migration 0002), `Mesh.get_sync_state` / `Mesh.set_sync_state`.
  - `AgentMemoryAdapter` — `.agent-memory/` markdown frontmatter, content-hash seen-set.
  - `EntireAdapter` — `entire/checkpoints/v1` git branch, JSON commit-message payloads, force-push recovery.
  - `Mesh.sync` orchestrator: discover ▸ fetch ▸ distill ▸ persist; `SyncResult` + `SkipRecord(stage)`; dry-run; `sync_pull` audit.
  - `context-mesh sync <adapter>` CLI with `--repo` / `--branch` / `--limit` / `--dry-run` / `--distiller` / `--json` / `--db`.
- ⏳ Phase 6+ — federation hub, lifecycle (decay, supersede, promote), polish, release.

---

## 10. What The Next Agent Should Do First

1. **Re-read this file in full.** Then read `README.md` and `docs/ARCHITECTURE.md` in that order.
2. **Read `CHANGELOG.md`** to see the latest landed work.
3. **Review the open task or issue** you're being asked to address.
4. **Write a short plan first** — what you'll build, what files will change, what tests will exist, what could go wrong. Get explicit approval.
5. **Then implement.** Top 1% quality. No shortcuts. No bloat.
6. **Verify.** Run tests. Run the actual feature. Show the output.
7. **Update relevant docs.** If you learned something while implementing that contradicts the docs, fix the docs.
8. **Mark progress in `CHANGELOG.md`.** One-line entry per merged change.
9. **Loop.**

---

## 11. Things You Must Not Do

- Do NOT begin coding before reading `docs/ARCHITECTURE.md` and `docs/SCHEMA.md`.
- Do NOT introduce dependencies that aren't in the committed tech stack without explicit approval.
- Do NOT push to any remote without explicit approval.
- Do NOT add timelines or deadlines anywhere in the docs.
- Do NOT create new top-level files or directories without updating this `CLAUDE.md` AND `README.md` to reflect them.
- Do NOT add AI authorship trailers to commits.

---

## 12. Long-Term Vision Beyond v1.0

After v1.0 (the foundational federated memory mesh), the system extends in several directions:

- **Multi-resolution embeddings** — concept-level / memory-level / field-level vectors for token-efficient retrieval at scale.
- **Causal graph format** — memories as nodes in causal chains, enabling agents to answer "why did we decide this" questions.
- **Skill cards** — structured executable directives the agent reads as function-call inputs rather than prose.
- **Cross-org federation** — privacy-preserving pattern extraction so multiple companies can benefit from shared distilled lessons (with full anonymization).
- **Decay engine** — adaptive memory aging based on usage patterns, contradiction detection, and reinforcement signals.

These are deliberately deferred from v1.0. They become real once the foundation is solid.

---

## Final Word

This project is being built as a meaningful technical artifact, not a demo. The quality bar is high. The architecture is grounded in measured trade-offs. Operate accordingly.

When in doubt: ask. When confident: ship.
