# OpenAI

Use the OpenAI function-calling dialect when your agent runs against
GPT-4 / GPT-4o or any provider that speaks the same schema.

## Prerequisites

- A `context-mesh` store: `context-mesh init`.
- The server running: `context-mesh serve`.
- The bearer token from `~/.context-mesh/token`.

## Get the schemas

```bash
context-mesh tools --dialect openai > tools.json
```

The file is a list of five entries shaped as
`{"type": "function", "function": {"name", "description", "parameters"}}`.

## Use them in an OpenAI tool-use loop

```python
import json
from openai import OpenAI

client = OpenAI()
tools = json.load(open("tools.json"))

resp = client.chat.completions.create(
    model="gpt-4o",
    tools=tools,
    messages=[
        {"role": "user", "content": "Have we decided how to auth webhooks?"},
    ],
)
```

When the model emits a tool call, dispatch it to the local server.
Tool-name → endpoint map:

| Tool                  | Method | Path                   |
|-----------------------|--------|------------------------|
| `search_team_memory`  | POST   | `/search`              |
| `drill_down_memory`   | GET    | `/node/{node_id}`      |
| `add_memory`          | POST   | `/node`                |
| `mark_memory_used`    | POST   | `/feedback`            |
| `find_contradictions` | POST   | `/contradictions`      |

Example dispatcher for `search_team_memory`:

```python
import os
import httpx

TOKEN = open(os.path.expanduser("~/.context-mesh/token")).read().strip()
BASE = "http://127.0.0.1:7421"

def search_team_memory(args: dict) -> dict:
    r = httpx.post(
        f"{BASE}/search",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json=args,
    )
    r.raise_for_status()
    return r.json()
```

Feed the response back to the model as a `tool` role message.

## Troubleshooting

- **401 Unauthorized**: token mismatch. Re-read `~/.context-mesh/token`.
- **Connection refused**: the server is not running on the configured
  port. Verify with `curl http://127.0.0.1:7421/health -H "Authorization: Bearer $TOKEN"`.
- **`OPENAI_API_KEY` not set**: that's a client-side error from the
  OpenAI SDK, unrelated to context-mesh — set the env var.
