# Design Principles

These are the engineering values that guide every decision in `context-mesh`. When trade-offs are unclear, return to these.

---

## 1. Agent-First, Not Human-First

The primary user of memory is the **AI agent**, not the human developer. Interfaces are designed for agent consumption (tool calls, structured outputs, token efficiency), with human-facing surfaces (CLI, dashboards) as secondary.

**What this means concretely:**
- Tools return tight, structured data — not pretty prose.
- Token budgets are non-negotiable.
- Output formats are stable and parseable, not free-form.
- Human-facing CLI is built on the same primitives, not separate.

**Contrast:** most "AI memory" libraries are designed for humans to query and admin. We invert this.

---

## 2. Active Retrieval Over Passive Injection

Agents pull memory when they need it. We don't push memory at them based on guesses.

**What this means concretely:**
- No auto-injection at session start.
- No system-prompt stuffing.
- Memory is exposed as tools the agent calls.
- Decisions about what's relevant happen in the agent's reasoning loop, not in our retrieval guess.

**Contrast:** most existing systems force-feed context. The 22-bug benchmark proved this hurts more often than it helps.

---

## 3. Structure Over Similarity

Vector similarity finds *relevant*. Graph edges decide which relevant *matters*. Both are required.

**What this means concretely:**
- Pure vector search is insufficient.
- Edges between memories carry semantic meaning (`caused_by`, `applies_to`, `contradicts`, `generalizes`, `supersedes`).
- Retrieval traverses both vector space and the graph.
- Contradictions are surfaced explicitly, not silently averaged.

**Contrast:** flat vector RAG misses structural information that matters most for decision-making.

---

## 4. Three Kinds, Not One Bag

Episodic, semantic, and procedural memories have fundamentally different lifecycles. Mixing them collapses retrieval quality.

**What this means concretely:**
- Schema explicitly types memory kind.
- Retrieval logic differs per kind.
- Decay rules differ per kind.
- Default behavior differs per kind.

**Contrast:** systems that store everything as undifferentiated "memories" produce noisier retrieval.

---

## 5. Privacy As A First-Class Field

Every memory carries a scope. Federation respects boundaries by construction.

**What this means concretely:**
- `scope` is required on every node, not optional.
- Default scope is `private`. Sharing is opt-in.
- Cross-scope leakage is impossible at the schema level — it's not "we forgot to filter," it's "the filter is required."
- Sensitive content is redacted BEFORE storage, not BEFORE retrieval.

**Contrast:** systems that bolt on privacy after the fact have leakage edge cases.

---

## 6. Composability Over Features

This is a primitive. It composes with Entire's checkpoints, with `agent-memory`, with Claude Code, with Cursor, with anyone.

**What this means concretely:**
- Adapters are first-class.
- The HTTP protocol is the lowest common denominator.
- We don't lock users into our format.
- Migration to and from `context-mesh` is supported.

**Contrast:** platform plays trap users; we want the opposite — a clean primitive anyone can use.

---

## 7. Observability Everywhere

If we can't measure it, we can't improve it.

**What this means concretely:**
- Every retrieval, every distillation, every sync is audited.
- Metrics are emitted for time-series analysis.
- Traces propagate across hub-client boundaries.
- Inspection commands surface system state to developers.

**Contrast:** systems treated as black boxes can't be tuned; we treat ourselves as a system to be tuned.

---

## 8. Open Source, MIT, Self-Hosted

`context-mesh` must be a primitive the entire industry can use, not a captured platform.

**What this means concretely:**
- MIT license, no carve-outs.
- No SaaS lock-in. Hub mode is self-hosted.
- No telemetry, no analytics, no phone-home.
- All algorithms documented.
- All schema versioned and migratable.

**Contrast:** "open source core, paid features" approaches; we reject this for v1. Trust comes first.

---

## 9. Default To Strict, Allow Loose

Defaults are conservative. Permissive behavior must be explicitly enabled.

**What this means concretely:**
- Default scope is `private`.
- Default redaction is aggressive.
- Default network access is none beyond explicitly-configured hub.
- Default error behavior is fail-closed.
- Default token budget is tight.

**Contrast:** systems with permissive defaults trap users into accidental sharing.

---

## 10. Verify Quality Empirically

We are not designing on vibes. The 22-bug benchmark validated the architectural premise; future tuning is similarly evidence-based.

**What this means concretely:**
- Eval harness runs continuously against golden retrieval cases.
- Ranking weights are calibrated against real retrieval outcomes.
- Threshold tunings require empirical justification.
- Performance regressions are blockers.

**Contrast:** systems that ship "intuitively right" patterns and never measure are guessing.

---

## 11. Match Scope, Don't Expand

A bug fix doesn't include surrounding refactors. A new feature doesn't drag in tangential cleanup. Stay precisely on task.

**What this means concretely:**
- PRs are tightly focused.
- Code reviews call out scope creep.
- v1 features are sharply bounded; "nice to haves" go to v1.x.
- Refactors happen in dedicated PRs, never alongside features.

**Contrast:** sprawling PRs that touch unrelated code increase risk and reduce reviewability.

---

## 12. Plan Before Code

No implementation begins without an explicit plan covering what will be built, what files will change, what tests will exist, and what could go wrong.

**What this means concretely:**
- Every feature has a written plan reviewed before code lands.
- Every plan has a verification strategy.
- Implementation is the last step of a 4-step process: plan → review plan → implement → verify.

**Contrast:** code-first development on systems this complex produces fragile, hard-to-verify implementations.

---

## 13. No Premature Abstraction

Three similar lines beat a premature abstraction.

**What this means concretely:**
- We don't build plugin systems for things that exist once.
- We don't generalize before we have at least three concrete cases.
- Adapters exist where there are real consumers, not hypothetical ones.

**Contrast:** over-abstracted systems collapse under maintenance burden.

---

## 14. Fail Loudly, Fail Specifically

When things go wrong, the system says exactly what went wrong, where, and what the user can do about it.

**What this means concretely:**
- Errors include context (file path, query, node ID).
- No silent failures.
- No swallowed exceptions.
- No "internal server error" without trace IDs.

**Contrast:** opaque failures that require log archaeology waste user time.

---

## 15. Documentation Is Code

The docs in `docs/` are not afterthoughts. They are the canonical specification of the system.

**What this means concretely:**
- Doc changes go through review.
- Code that contradicts docs is a bug — fix one or the other.
- New features start with doc updates.
- Onboarding (the `CLAUDE.md`) is treated as critical infrastructure.

**Contrast:** systems where docs lag code become incomprehensible to newcomers.

---

## 16. Honest Engineering, Not Marketing

We document what works AND what doesn't. We surface limitations. We don't oversell.

**What this means concretely:**
- Performance numbers are measured, not estimated.
- Trade-offs are documented, not hidden.
- Failures of similar approaches are referenced honestly.
- README claims map to verified capabilities.

**Contrast:** systems that overclaim erode user trust on first contact with reality.

---

## When These Conflict

These principles can pull against each other. Examples:

- **Active retrieval** vs **observability** — observing what the agent retrieves is fine, but observing what it doesn't retrieve (because it didn't ask) is impossible. We accept this gap.
- **Privacy** vs **federation** — federation enables sharing, but sharing risks leakage. We resolve via strict scopes.
- **Open source** vs **operational safety** — open code means anyone can read it, including attackers. We accept this and lean on cryptographic primitives where needed.

When principles conflict, we prefer:
1. Privacy and security over convenience.
2. Composability over feature breadth.
3. Empirical evidence over taste.
4. Default conservative over default permissive.

These secondary preferences resolve most conflicts cleanly.

---

## Final Word

These principles are not aspirational; they are operational. Every PR is reviewed against them. Every design decision is justified against them. They are the project's spine.

If you find yourself violating one of these, stop. Either the principle needs to change (proposed via PR with justification), or the design needs to change. Both are valid; quietly violating principles is not.
