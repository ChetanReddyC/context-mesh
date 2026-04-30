# Memory Types

`context-mesh` distinguishes three kinds of memory: **episodic**, **semantic**, and **procedural**. They have fundamentally different lifecycles, retrieval logic, and use cases. Mixing them collapses retrieval quality.

This document is the canonical definition of each kind.

---

## Episodic Memory

> *What happened in a specific session.*

### Definition

An episodic memory captures a discrete event in the team's working history: a debugging session, a refactor, a postmortem, a one-off discovery. It is **anchored in time and context** — tied to a specific repo, branch, commit, agent session, and date.

### Examples

- *"On 2026-04-15, agent debugged Stripe webhook timeout in payments service. Root cause: trusted webhook timestamp instead of re-verifying with current_time."*
- *"On 2026-03-22, refactored auth middleware in user-service. Took 3 attempts; first two broke session persistence."*
- *"On 2026-04-08, deployment to us-west failed because IAM role lacked S3 permissions. Fixed by updating Terraform module."*

### Lifecycle

- **Created** at the end of an agent session via the distillation engine.
- **Decays fast.** After ~30 days, recency_score drops below the threshold for default retrieval. Old episodics are still searchable on explicit request, but they don't surface unsolicited.
- **Promoted** to a semantic memory if a pattern emerges across multiple episodics. The distillation engine identifies clusters of similar episodics and proposes a generalized rule. The user (or an LLM judge) confirms the promotion.

### Retrieval Behavior

- Returned only when the query explicitly asks about past events ("what did we do last time," "have we hit this before").
- Lower default weight in mixed-kind retrievals — semantic and procedural rules are preferred.
- Recency decay is aggressive: more recent episodics dominate older ones.
- Episodics from the same repo are weighted higher than from other repos (unless cross-repo retrieval is explicitly requested).

### When To Use Episodic Memory

- Capturing debugging discoveries.
- Recording postmortem details.
- Logging non-obvious workarounds for specific incidents.
- Preserving context for "why did we do this on date X" queries.

### When NOT To Use Episodic Memory

- For rules that should persist (use semantic).
- For task instructions (use procedural).
- For raw transcripts (those don't belong in `context-mesh` at all — store them in your transcript system).

---

## Semantic Memory

> *Generalized rules that persist across sessions.*

### Definition

A semantic memory expresses a **rule, principle, or constraint** that applies broadly. It is detached from any specific session — it represents distilled wisdom the team has internalized.

### Examples

- *"Never trust the first webhook timestamp from Stripe; always re-verify with current_time."*
- *"All auth-related changes must explicitly handle both guest and logged-in flows."*
- *"Database migrations on the orders table require a maintenance window — schema locks block reads."*
- *"PRs to the payments service require security team review."*

### Lifecycle

- **Created** in two ways:
  1. By promotion from a cluster of related episodics.
  2. By direct authoring (a senior engineer or team lead writes a rule explicitly).
- **Persists** by default. Decay is slow; semantic memories are the team's law book.
- **Superseded** when a newer rule replaces the old one. Connected via a `supersedes` edge so the history is preserved.
- **Contradicted** when teams disagree. The system surfaces both via `contradicts` edges rather than silently picking one.

### Retrieval Behavior

- Highest priority in mixed-kind retrievals.
- Always checked against the agent's current context: if a relevant rule exists, it surfaces.
- Contradictions surface explicitly so agents see tensions rather than acting on one side blindly.
- Slow decay means semantic memories don't fade just because they're old; they fade only when contradicted, superseded, or marked stale.

### When To Use Semantic Memory

- Coding standards.
- Architectural constraints.
- Security and compliance rules.
- Team conventions.
- "We learned the hard way" generalizations.

### When NOT To Use Semantic Memory

- For one-time events (use episodic).
- For task-specific instructions (use procedural).
- For unverified hypotheses (mark as low-confidence; promote when evidence accumulates).

---

## Procedural Memory

> *How to do specific tasks.*

### Definition

A procedural memory captures **executable knowledge**: the exact command to run, the sequence of steps, the script to invoke, the workflow to follow. It is **task-oriented** and often contains code or commands.

### Examples

- *"To deploy the payments service: run `make deploy --region=us-west`. Requires VPN."*
- *"To reset a stuck migration: `ALTER SYSTEM` ... [full procedure]."*
- *"To set up local dev: clone, run `make bootstrap`, then `docker-compose up`."*
- *"To rollback a release: `kubectl rollout undo deployment/payments`."*

### Lifecycle

- **Created** when a non-obvious workflow is established or rediscovered.
- **Medium decay.** Commands and workflows change, but not as fast as episodic events. Default decay is ~6 months.
- **Versioned.** When a procedure changes, the new version supersedes the old via a `supersedes` edge.
- **Verified.** Procedural memories ideally include a verification step (e.g., "after running, check that the service responds with 200 on /health").

### Retrieval Behavior

- Pulled when the query matches task-type patterns: "how do I deploy," "how to run tests," "what's the command for X."
- Includes the full command/script in the response (not just the headline) — the agent needs the executable form.
- Cross-repo retrieval is common: deploy procedures often apply to multiple services.

### When To Use Procedural Memory

- Deploy commands.
- Build / test / lint commands that aren't in standard locations.
- Workflows for specific recurring tasks.
- Setup instructions.

### When NOT To Use Procedural Memory

- For one-off commands (don't pollute the procedural store with throwaways).
- For things that belong in actual scripts (if it's automatable, automate it).
- For information that lives better in code or docs (don't duplicate; reference instead).

---

## Decision Tree: Which Kind Is It?

When an agent or distillation engine creates a memory, it must classify the kind. The decision tree:

```
Is this a one-time event tied to a specific session?
├── YES → episodic
└── NO
    ├── Is this a command, script, or step-by-step procedure?
    │   ├── YES → procedural
    │   └── NO
    │       ├── Is this a rule, principle, or constraint?
    │       │   ├── YES → semantic
    │       │   └── NO → re-evaluate; if uncertain, default to episodic
```

If the kind is genuinely unclear, default to **episodic** — episodics decay fast and won't pollute long-term memory if the classification was wrong.

---

## Hybrid Memories

Sometimes a memory has elements of multiple kinds:

> *"On 2026-04-15, debugging Stripe webhooks. Root cause: trusted timestamp. **Going forward, always re-verify with current_time.**"*

This is a one-time event (episodic) that produced a generalized rule (semantic).

**The right pattern:** create TWO memories.
1. An episodic node capturing the event.
2. A semantic node capturing the rule.
3. An edge `generalizes` from the semantic to the episodic.

This way each kind retains its own retrieval logic, and the relationship between them is preserved structurally.

The distillation engine is responsible for splitting hybrid memories appropriately.

---

## Storage Implications

All three kinds share the same `nodes` table (with the `kind` field discriminating). Indexes on `kind` make filtering fast.

Different kinds have different default values:
- Episodic: high `decayed_at` (decays fast).
- Semantic: no `decayed_at` set (persists indefinitely).
- Procedural: medium `decayed_at`.

These defaults are tunable per scope (a team might want longer episodic retention, for example).

---

## Future Memory Kinds (Deferred)

Anticipated kinds that may be added in v1.x:

- **Skill cards** — structured executable directives that the agent reads as function-call inputs (different from procedural in that they're machine-parseable, not human-readable).
- **Constraint memories** — explicit invariants the agent must respect (subset of semantic with stricter enforcement).
- **Preference memories** — user/team stylistic preferences (a softer kind than semantic rules).

These are explicitly out of v1 scope. The three kinds above cover ~95% of useful memory at the current stage of the field.
