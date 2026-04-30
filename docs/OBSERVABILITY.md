# Observability

If we cannot measure quality, we cannot improve it. Observability is a first-class concern in `context-mesh`, not an afterthought.

This document describes what gets logged, what gets traced, what gets measured, and how to inspect the system at runtime.

---

## Three Layers

1. **Audit log** — durable record of every operation (in `audit` table).
2. **Structured logs** — operational logs (info, warn, error events).
3. **Metrics** — quantitative time-series data (counters, gauges, histograms).

Each layer serves a different purpose and has different retention needs.

---

## 1. Audit Log

Every operation that touches memory is recorded in the `audit` table. This is non-negotiable.

### What Gets Audited

| Event Type | When | Recorded Fields |
|---|---|---|
| `retrieve` | Every `search_team_memory` call | actor, query, result_count, top node IDs, latency |
| `drill_down` | Every `drill_down_memory` call | actor, node_id |
| `add` | Every memory creation | actor, node_id, kind, scope, source |
| `edit` | Every memory modification | actor, node_id, fields_changed |
| `delete` | Every hard delete | actor, node_id, reason |
| `mark_helpful` | Every helpful-vote | actor, node_id |
| `mark_unhelpful` | Every unhelpful-vote | actor, node_id, optional_notes |
| `sync_push` | Every sync push to hub | actor, count, hub_url |
| `sync_pull` | Every sync pull from hub | actor, count, hub_url |
| `decay` | Every decay-engine pass | system, count_decayed |
| `contradiction_detected` | Every time `contradicts` edge inferred | node_a, node_b, confidence |

### Retention

Audit log is append-only. Default retention: 1 year. Configurable via `[observability].audit_retention_days`.

After retention expires, entries are archived to a separate file (`audit-archive-YYYYMM.jsonl`) and removed from the live database. Archives are never deleted by the system.

### Querying The Audit Log

```bash
# Recent activity
context-mesh audit --limit 100

# Filter by event type
context-mesh audit --event retrieve

# Filter by actor (which agent or user)
context-mesh audit --actor claude-code

# Filter by time range
context-mesh audit --since "2026-04-01"

# Export to JSON for external analysis
context-mesh audit --export > audit.jsonl
```

---

## 2. Structured Logs

Operational logs for debugging and monitoring. Written as JSONL by default.

### Log Levels

- `DEBUG` — verbose; enabled only when explicitly requested.
- `INFO` — normal operations (retrievals, syncs, distillation completion).
- `WARN` — degraded behavior (slow query, sync conflict, contradiction detected).
- `ERROR` — failures requiring attention (DB error, distillation failure, sync failure).
- `CRITICAL` — system-level failures (corruption, unrecoverable state).

Default level: `INFO`. Configurable per-component.

### Log Format

```json
{
  "timestamp": "2026-04-28T14:32:11.123Z",
  "level": "INFO",
  "component": "retrieval",
  "event": "search_complete",
  "session_id": "01abc...",
  "actor": "claude-code",
  "duration_ms": 124,
  "result_count": 3,
  "metadata": {
    "query_hash": "9f8e2c..."
  }
}
```

The query itself is NOT logged at INFO (privacy). Only the hash, for correlating with audit log. At DEBUG level, the full query may be logged (with redaction).

### Log Destinations

By default, logs are written to `<context-mesh-dir>/log.jsonl`. Optional destinations:

- `stdout` (for container deployments)
- `syslog` (for system-wide aggregation)
- HTTP endpoint (for centralized log services)

Configured via `[observability].log_destinations`.

### Log Rotation

Log files rotate at 100MB or daily, whichever comes first. Rotated files are kept for 30 days by default.

---

## 3. Metrics

Quantitative time-series data. Compatible with Prometheus and OpenTelemetry.

### Counters

| Metric | Labels | Description |
|---|---|---|
| `cm_retrievals_total` | actor, kind, scope | Total retrieval calls. |
| `cm_distillations_total` | source_adapter, kind | Total distillations. |
| `cm_memories_added_total` | kind, scope, source | Total new memory nodes. |
| `cm_memories_marked_helpful_total` | actor | Helpful votes. |
| `cm_memories_marked_unhelpful_total` | actor | Unhelpful votes. |
| `cm_sync_push_total` | hub | Sync push events. |
| `cm_sync_pull_total` | hub | Sync pull events. |
| `cm_contradictions_detected_total` | scope | Contradictions surfaced. |
| `cm_errors_total` | component, error_type | Errors by component. |

### Gauges

| Metric | Labels | Description |
|---|---|---|
| `cm_total_nodes` | kind, scope | Current node count. |
| `cm_total_edges` | relation | Current edge count. |
| `cm_storage_bytes` | — | Database file size. |
| `cm_pending_sync_items` | hub | Items queued for sync. |
| `cm_decayed_nodes` | kind | Nodes flagged decayed. |

### Histograms

| Metric | Labels | Description |
|---|---|---|
| `cm_retrieval_latency_seconds` | kind, hit_threshold | Retrieval end-to-end latency. |
| `cm_distillation_duration_seconds` | model, kind | Distillation duration. |
| `cm_sync_duration_seconds` | direction, hub | Sync duration. |
| `cm_node_size_bytes` | kind | Distribution of node sizes. |
| `cm_cluster_size` | — | Number of nodes returned per retrieval. |

### Exposing Metrics

```bash
# Run with metrics endpoint
context-mesh serve --metrics-port 9090
```

Prometheus scrapes from `/metrics`. Or, OpenTelemetry export:

```toml
[observability]
otel_endpoint = "http://otel-collector:4317"
otel_service_name = "context-mesh"
```

---

## 4. Tracing

OpenTelemetry traces for end-to-end request flow.

### Spans Emitted

Every operation creates a span. Important spans:

- `cm.search` — full retrieval, child spans for: embed, vec_search, graph_walk, rank
- `cm.distill` — full distillation, child spans for: redact, llm_call, classify, embed, persist
- `cm.sync.push` — push to hub
- `cm.sync.pull` — pull from hub
- `cm.add` — single-node insert

### Span Attributes

Each span includes:
- `cm.actor` — who initiated
- `cm.session_id` — current session if applicable
- `cm.kind` — memory kind if applicable
- `cm.scope` — scope if applicable
- `cm.error` — error if span failed

Sensitive content (memory bodies, queries) is NEVER attached to spans.

### Distributed Context

Traces propagate W3C Trace Context headers across HTTP calls (sync to hub). This lets a team correlate a hub-side trace with the originating client.

---

## 5. Health & Readiness

Standard endpoints for orchestration:

```
GET /healthz   → 200 OK if process is alive
GET /readyz    → 200 OK if database is reachable and migrations applied
```

Used by Kubernetes, Docker health checks, monitoring systems.

---

## 6. Inspection Commands

Developer-facing inspection without needing to query the database directly:

```bash
# Overall stats
context-mesh stats

# Storage size and breakdown
context-mesh storage

# Recent retrievals (last hour)
context-mesh activity

# Slow queries (above latency threshold)
context-mesh slow-queries

# Hot memories (most retrieved)
context-mesh hot

# Memories needing review (agent-added, not yet promoted)
context-mesh review

# Contradictions
context-mesh contradictions
```

These commands are read-only and safe to run anytime.

---

## 7. Privacy In Observability

Observability must NEVER leak content:

- **Logs** never include raw memory bodies, queries, or transcripts at default log level.
- **Audit log** stores hashes of queries, not the queries themselves.
- **Metrics** are aggregate counts and durations only.
- **Traces** carry IDs and metadata, never content.

The principle: observability tells you HOW the system is performing, not WHAT it's processing.

Exceptions for debugging require explicit opt-in via `--debug-content` flag, only in local mode, only for the current session.

---

## 8. Default Dashboards (Reference)

When deployed with Prometheus + Grafana, the recommended dashboard contains these panels:

- Retrieval rate (calls/min) by actor.
- Retrieval latency p50/p95/p99.
- Memories added per day, by kind.
- Helpful vs unhelpful vote ratio (rolling 7-day).
- Storage size over time.
- Sync success rate.
- Contradiction detection rate.
- Top 10 most-retrieved memories.

Used by hub operators to monitor team-level health.

---

## 9. Out Of Scope For v1

- **Distributed tracing across multiple hubs** — v1 has one hub per team; distributed tracing is multi-hub.
- **Anomaly detection** — automated detection of unusual patterns (sudden retrieval spikes, etc.).
- **User-level analytics** — per-user retrieval patterns; raises privacy concerns.
- **Memory effectiveness scoring** — automated quality scoring of memories based on usage; v1.x.
