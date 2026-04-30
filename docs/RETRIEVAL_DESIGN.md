# Retrieval Design

## Core Principle: Active Retrieval, Not Passive Injection

`context-mesh` does not auto-inject memories into agent prompts. Instead, it exposes memory through **tools that the agent calls when it decides context is needed.**

This is the single most important design decision in the system. It is what makes the difference between a memory layer that helps and a memory layer that hurts.

---

## Why Active Retrieval

Empirical evidence from a 22-bug benchmark study of agent-memory effectiveness:

- **Memory helps when** it gives the agent a keyword or fact it would otherwise have to grep for.
- **Memory hurts when** it gives the agent a theory it would otherwise have to test from scratch.

Auto-injection forces the system to guess what's relevant before the agent has even started reasoning. When the guess is wrong, the injected context becomes a distraction, a misleading anchor, or pure token waste.

Active retrieval defers the decision to the agent. The agent reasons about the problem, identifies what it doesn't know, and **asks** for that specific thing. The retrieval is precisely scoped because the agent itself scopes it.

This mirrors how senior engineers operate: they don't read every prior bug report before starting; they reach for one when they hit something unfamiliar.

---

## Tool Surface

Five tools are exposed to agents. Each is documented in detail in `docs/API_DESIGN.md`. Here we describe their retrieval semantics.

### `search_team_memory(query, kind?, scope?, limit?) → MemoryCluster`

Primary retrieval entry point. The agent provides a natural-language query; the system returns a tightly-scoped cluster of relevant memories.

**Inputs:**
- `query` (required) — natural language description of what the agent is looking for.
- `kind` (optional) — filter by memory kind (`episodic`, `semantic`, `procedural`). Default: all kinds.
- `scope` (optional) — filter by privacy scope. Default: all visible scopes.
- `limit` (optional, default 5) — maximum nodes to return.

**Output (`MemoryCluster`):** a structured object containing:
- A list of memory nodes (each with headline, summary, optional drill-down ID).
- Edge information showing how the returned nodes relate to each other.
- A composite confidence score for the cluster.

### `drill_down_memory(node_id) → FullNode`

When `search_team_memory` returns a node summary, the agent can request the full body if needed.

**Why split this:** to keep the default response token-efficient. Most queries are answered well by headlines and summaries. Full bodies are pulled only when the agent decides it needs them.

### `add_memory(content, kind, scope?, tags?) → NodeId`

Lets the agent contribute new memories during a session.

**Use case:** the agent has just learned something genuinely new (a non-obvious gotcha, a workaround for a stubborn bug). It calls `add_memory` to persist that lesson for future sessions.

**Caution:** agent-added memories are tagged differently than distilled memories and require human review before being promoted to high-confidence status. See `docs/SECURITY_PRIVACY.md`.

### `mark_memory_used(node_id, helpful: bool) → void`

The agent reports whether a retrieved memory was helpful.

**Why this exists:** it's the feedback loop that drives ranking improvements. Memories that are repeatedly retrieved-but-unhelpful get down-ranked; memories that are repeatedly retrieved-and-helpful get up-ranked.

### `find_contradictions(content) → MemoryNode[]`

Surface any existing memories that contradict a proposed action or decision.

**Use case:** before the agent makes a non-trivial decision, it can ask whether the team has already decided the opposite. The system traverses `contradicts` edges to surface tensions.

---

## The Retrieval Algorithm

Pseudo-code for `search_team_memory`:

```
function search_team_memory(query, kind, scope, limit):
    # 1. Embed the query using the same model used for nodes.
    query_embedding = embed(query)

    # 2. Vector search: find top-K by similarity.
    K = max(limit * 4, 20)  # over-fetch for re-ranking
    candidates = vec_search(
        embedding=query_embedding,
        filter={kind: kind, scope: in_visible_scopes(scope)},
        k=K
    )

    # 3. Score each candidate.
    for c in candidates:
        c.semantic_score = c.cosine_similarity * 100
        c.recency_score = exponential_decay(c.created_at)
        c.importance_score = c.importance * 100
        c.usage_score = log(1 + c.usage_count) * sigmoid(c.helpful_count)

    # 4. Drop candidates below content-relevance threshold.
    candidates = [c for c in candidates if c.semantic_score >= MIN_CONTENT_SCORE]

    # 5. Graph expansion: for each top candidate, walk 1-2 hops on key edges.
    expanded = set(candidates)
    for c in candidates[:5]:  # only expand top-5 to avoid blowup
        related = graph_walk(
            start=c.id,
            relations=['applies_to', 'generalizes', 'supersedes'],
            max_hops=2
        )
        for r in related:
            r.relevance_score = compute_edge_relevance(c, r)
            expanded.add(r)

    # 6. Composite ranking.
    for n in expanded:
        n.composite_score = (
            n.semantic_score * 0.50 +
            n.relevance_score * 0.20 +
            n.recency_score * 0.10 +
            n.importance_score * 0.10 +
            n.usage_score * 0.10
        )

    # 7. Deduplicate (a memory can be reached by multiple paths).
    expanded = dedupe_by_id(expanded)

    # 8. Re-rank by composite, take top `limit`.
    expanded.sort(by=composite_score, descending=True)
    selected = expanded[:limit]

    # 9. Apply final quality gate: composite_score >= QUALITY_THRESHOLD.
    selected = [n for n in selected if n.composite_score >= QUALITY_THRESHOLD]

    # 10. Format as cluster, attach edge information.
    cluster = build_cluster(selected, edges_among=selected)

    # 11. Audit-log this retrieval event.
    audit_log(event='retrieve', query=query, result_count=len(cluster.nodes))

    return cluster
```

---

## Ranking Signals

The composite score combines five signals:

| Signal | Weight | What it captures |
|---|---|---|
| `semantic_score` | 0.50 | Embedding similarity to query. The dominant signal. |
| `relevance_score` | 0.20 | Edge-derived structural relevance (e.g., this rule applies to that file). |
| `recency_score` | 0.10 | How fresh the memory is. Decays exponentially. |
| `importance_score` | 0.10 | Importance computed at distillation time (decisions count, etc.). |
| `usage_score` | 0.10 | Historical usefulness (helpful_count / usage_count). |

Weights are starting defaults and will be tuned via the eval harness.

---

## The Quality Gate

A retrieval may return zero results. This is intentional and correct.

If no memory passes the quality gate, the agent is told *"no relevant memories found"* — better than receiving low-quality noise. The agent can then proceed without misleading context.

The default thresholds:
- `MIN_CONTENT_SCORE = 30` — semantic similarity floor (0-100 scale).
- `QUALITY_THRESHOLD = 40` — composite score floor.

These are starting numbers and will be calibrated via the eval harness.

---

## Memory-Kind-Specific Retrieval Logic

Different memory kinds have different retrieval behavior:

### Episodic
- Strict recency decay (relevance fades within ~30 days).
- Returned only when query specifically asks about past events.
- Lower default weight in mixed retrievals.

### Semantic
- Slow decay (rules persist until contradicted or superseded).
- Highest priority in retrievals — these are the team's law book.
- Always checked for `contradicts` edges; conflicting rules surfaced explicitly.

### Procedural
- Medium decay (commands change but slowly).
- Pulled when query matches task-type patterns (deploy, test, build, lint).
- Includes example commands in the response.

For details, see `docs/MEMORY_TYPES.md`.

---

## Multi-Hop Graph Traversal

After vector search identifies candidates, the system walks the edge graph to find structurally-related context.

**Traversal rules in v1:**

- Walk only the following relation types by default: `applies_to`, `generalizes`, `supersedes`.
- Max 2 hops from each starting node.
- `contradicts` edges always surface (one hop), so contradictions aren't hidden.
- `caused_by` is traversed only when the query explicitly suggests causal investigation (heuristic on query terms like "why," "caused," "root cause").

**Why limit traversal to 2 hops:** further hops dilute relevance fast. The 2-hop boundary is empirically the sweet spot in similar systems (GraphRAG, etc.).

---

## Cluster Format (Tool Response)

When a query returns memories, they're packaged as a `MemoryCluster`:

```json
{
  "query": "cart auth bug",
  "result_count": 3,
  "nodes": [
    {
      "id": "0192b3e1-...",
      "kind": "episodic",
      "headline": "Cart auth bug missed guest-checkout path (Apr 15)",
      "summary": "Agent fixed cart auth but only handled logged-in users. Guest checkout broke 4 days later in production.",
      "score": 87.4,
      "drill_down_id": "0192b3e1-..."
    },
    {
      "id": "0192b3f2-...",
      "kind": "semantic",
      "headline": "Always cover both auth paths (logged-in + guest)",
      "summary": "Team rule: any auth-related change must explicitly handle both guest and logged-in flows.",
      "score": 91.2,
      "drill_down_id": "0192b3f2-..."
    },
    {
      "id": "0192b401-...",
      "kind": "procedural",
      "headline": "Run cart auth integration tests with: make test-auth",
      "summary": "The test suite covers both paths if invoked correctly.",
      "score": 78.0,
      "drill_down_id": "0192b401-..."
    }
  ],
  "edges": [
    {
      "from": "0192b3f2-...",
      "to": "0192b3e1-...",
      "relation": "generalizes",
      "weight": 0.9
    }
  ],
  "cluster_confidence": 0.84
}
```

The agent reads this, picks what's relevant, optionally drills down, and proceeds with its task.

---

## Token Budget

A retrieval response is capped to a default token budget (~500 tokens) to ensure it doesn't crowd the agent's context window.

If the cluster exceeds budget, summaries are truncated before bodies are included. The agent can always drill down for full content via `drill_down_memory`.

This token discipline is non-negotiable. The whole point of active retrieval is to NOT eat context.

---

## Failure Modes & Mitigations

### Failure: Agent doesn't call memory tool

If an agent never calls `search_team_memory`, no retrieval happens. This is acceptable — agents that don't need memory don't pay for it.

The CLI can optionally emit a "memory available — consider searching" hint in the agent's system prompt. This is a soft nudge, not a forced injection.

### Failure: Bad query

Agent might phrase a query poorly. Mitigation: if vector search returns nothing above threshold, response includes `"hint": "Try rephrasing more specifically"` so the agent can iterate.

### Failure: Retrieval returns wrong memory

The `mark_memory_used` feedback loop down-ranks consistently-unhelpful memories. Over time, ranking calibrates.

### Failure: Retrieval too slow

Performance budgets:
- p50 < 200ms
- p95 < 500ms
- p99 < 1s

If thresholds are exceeded, the system logs a warning and suggests storage tuning. Hard performance issues trigger a Postgres-backend migration recommendation.

---

## Out Of Scope For v1

- Multi-resolution embeddings (concept-level summary vectors).
- LLM-as-reranker (use a model to re-rank cluster results).
- Adaptive ranking weights (auto-tune signal weights from feedback).
- Cross-language semantic search (English-first in v1).

These extensions are planned for v1.x once v1.0 is stable.
