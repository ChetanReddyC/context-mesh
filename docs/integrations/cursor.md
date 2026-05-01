# Cursor

Cursor uses the same Anthropic tool-use schemas as Claude Code when its
agent runs against a Claude model. The flow below targets Cursor's
custom-tool registration; for OpenAI-backed sessions, see
[`openai.md`](./openai.md).

## Prerequisites

- A `context-mesh` store: `context-mesh init`.
- The server running: `context-mesh serve`.
- The bearer token from `~/.context-mesh/token`.

## Get the schemas

```bash
context-mesh tools --dialect anthropic > tools.json
```

## Wire up the tool dispatcher

Cursor's custom-tool integration expects each tool to be backed by an
HTTP call. Map the five tools to the local server (URL defaults to
`http://127.0.0.1:7421`):

```text
search_team_memory   →  POST /search
drill_down_memory    →  GET  /node/{node_id}
add_memory           →  POST /node
mark_memory_used     →  POST /feedback
find_contradictions  →  POST /contradictions
```

Every request needs:

```text
Authorization: Bearer <token>
Content-Type: application/json
```

A minimal Node.js dispatcher:

```js
import fs from "node:fs";

const token = fs.readFileSync(
  `${process.env.HOME}/.context-mesh/token`,
  "utf8",
).trim();

export async function searchTeamMemory(input) {
  const res = await fetch("http://127.0.0.1:7421/search", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}
```

Register one such function per tool from `tools.json`.

## Troubleshooting

- **Tool calls return 401**: the token cached in your dispatcher is
  stale. Restart `context-mesh serve` or re-read the token file.
- **Tool not found in Cursor**: the schema file path or registration
  hook is wrong. Re-run `context-mesh tools --dialect anthropic` and
  point Cursor at the fresh file.
- **CORS errors from a browser-based runner**: bind the server to
  `127.0.0.1` and call from the same host process; the server is not a
  CORS-enabled public API.
