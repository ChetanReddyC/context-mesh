# Claude Code

Connect Claude Code to a running `context-mesh` server using the Anthropic
tool-use schemas.

## Prerequisites

- A `context-mesh` store initialized: `context-mesh init`.
- The HTTP server running: `context-mesh serve` (default `http://127.0.0.1:7421`).
- The bearer token. On first start `serve` writes one to
  `~/.context-mesh/token` (mode `0o600` on POSIX). Print it with:

  ```bash
  cat ~/.context-mesh/token
  ```

## Get the schemas

```bash
context-mesh tools --dialect anthropic > tools.json
```

`tools.json` is a list of five entries: `search_team_memory`,
`drill_down_memory`, `add_memory`, `mark_memory_used`,
`find_contradictions`. Each has `name`, `description`, and `input_schema`.

## Register with the Anthropic SDK

```python
import json
import anthropic

client = anthropic.Anthropic()
tools = json.load(open("tools.json"))

resp = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "Have we decided how to auth webhooks?"}],
)
```

When the model returns a `tool_use` block, dispatch the call to the local
server. Example for `search_team_memory`:

```bash
TOKEN=$(cat ~/.context-mesh/token)
curl -s -X POST http://127.0.0.1:7421/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "webhook auth", "limit": 5}'
```

The tool-name → endpoint map:

| Tool                  | Method | Path                   |
|-----------------------|--------|------------------------|
| `search_team_memory`  | POST   | `/search`              |
| `drill_down_memory`   | GET    | `/node/{node_id}`      |
| `add_memory`          | POST   | `/node`                |
| `mark_memory_used`    | POST   | `/feedback`            |
| `find_contradictions` | POST   | `/contradictions`      |

Feed the JSON response back to the model as a `tool_result` block.

## Troubleshooting

- **401 Unauthorized**: bearer token missing or wrong. Re-read
  `~/.context-mesh/token` and prefix with `Bearer `.
- **Connection refused**: the server isn't running, or it bound to a
  different port. Check `context-mesh serve --port 7421`.
- **Port already in use**: another process holds 7421. Pick a free
  port: `context-mesh serve --port 7422` and update your client URL.
