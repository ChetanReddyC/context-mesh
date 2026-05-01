# HTTP API Reference

`context-mesh serve` runs a stdlib `http.server.ThreadingHTTPServer` (no
FastAPI / Flask / aiohttp dependency). The server exposes the five agent
tools as JSON REST endpoints, plus a `/health` liveness probe.

Default bind: `127.0.0.1:7421` (local-only). Use `--host 0.0.0.0` only
if you have explicit network access requirements.

## Authentication

Every endpoint, including `/health`, requires:

```
Authorization: Bearer <token>
```

The token is persisted at `~/.context-mesh/token` (mode `0o600` on
POSIX). On first start the server generates one with
`secrets.token_urlsafe(32)`. Compare with `secrets.compare_digest`.

Missing or invalid token → `401 Unauthorized`.

## Errors

All error responses are JSON: `{"error": "<message>"}`. Status codes:

- `400` — request validation failed (bad type, missing field, malformed JSON).
- `401` — missing or invalid bearer token.
- `404` — unknown route, or `/node/{id}` for a missing id.
- `405` — wrong HTTP method (`Allow` header lists permitted methods).
- `500` — unhandled internal error (logged server-side with traceback).

Request bodies are capped at 1 MiB. Bodies are never logged; structured
access logs include method, path, status, and duration only.

---

## `GET /health`

Liveness probe.

Response `200 OK`:

```json
{"status": "ok", "version": "0.1.0"}
```

---

## `POST /search`

Wraps `Mesh.search` (`search_team_memory`).

Request body:

```json
{
  "query": "auth bug",
  "kind": "semantic",          // optional; episodic|semantic|procedural
  "scope_id": "team",          // optional
  "limit": 5                    // optional, default 5
}
```

Response `200 OK`: a serialized `MemoryCluster` —

```json
{
  "cluster_confidence": 0.83,
  "nodes": [
    {
      "node": { /* full MemoryNode */ },
      "semantic_score": 95.2,
      "recency_score": 71.4,
      "importance_score": 0.0,
      "usage_score": 0.0,
      "relevance_score": 0.0,
      "composite_score": 54.7
    }
  ],
  "edges": [ /* MemoryEdge[] connecting selected nodes */ ]
}
```

Audits as `retrieve` with the embedder name and filter parameters.

---

## `GET /node/{id}`

Wraps `Mesh.get` + `Mesh.get_edges` (`drill_down_memory`).

Response `200 OK`:

```json
{
  "node":      { /* full MemoryNode */ },
  "out_edges": [ /* MemoryEdge[] */ ],
  "in_edges":  [ /* MemoryEdge[] */ ]
}
```

`404 Not Found` if the node id is unknown.

---

## `POST /node`

Wraps `Mesh.add` (`add_memory`).

Request body:

```json
{
  "content": "always re-verify webhook timestamps",
  "kind": "semantic",
  "headline": "stripe webhook freshness",      // optional; auto-derived from content
  "scope_id": "team",                           // optional, default "agent"
  "source_session_id": "agent:abc",             // optional, default "agent:adhoc"
  "source_repo": "payments",                    // optional, default "agent"
  "tags": ["webhooks", "stripe"]                // optional
}
```

Auto-creates the scope and session rows if missing.

Response `200 OK`:

```json
{
  "node_id": "uuid-...",
  "auto_inferred_edges": [],
  "requires_review": true
}
```

---

## `POST /feedback`

Wraps `Mesh.mark_used` (`mark_memory_used`).

Request body:

```json
{
  "node_id": "uuid-...",
  "helpful": true,
  "notes": "saved 30 min of debugging"   // optional
}
```

Response `200 OK`:

```json
{
  "node_id": "uuid-...",
  "usage_count": 7,
  "helpful_count": 4
}
```

Audits as `mark_helpful` or `mark_unhelpful` depending on the `helpful`
flag.

---

## `POST /contradictions`

Wraps `Mesh.find_contradictions`.

Request body:

```json
{
  "content": "we should always use Redis for sessions",
  "kind": "semantic",     // optional, default "semantic"; semantic|procedural
  "limit": 5,             // optional, default 5
  "scope_id": "team"      // optional
}
```

Response `200 OK`:

```json
{ "results": [ /* MemoryNode[] sorted by ascending L2 distance */ ] }
```

Note: v1 surfaces *similarity*, not a logical contradiction verdict —
the response is a list of memories most likely to disagree with the
proposed content. A future LLM pass over these results would turn
similarity into a verdict.
