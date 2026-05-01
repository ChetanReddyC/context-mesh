# Model Context Protocol (MCP)

MCP is a generic protocol for exposing tools, resources, and prompts to
LLM clients. `context-mesh` ships the **schema half** of an MCP
integration — the five tool definitions in MCP shape — but does **not**
ship an MCP server runtime in v1. You wire the schemas into an MCP
server of your choice (e.g. the `@modelcontextprotocol/sdk` stdio
server or `mcp` Python package).

## Get the schemas

```bash
context-mesh tools --dialect mcp > tools.json
```

The file is a list of five entries shaped as
`{"name", "description", "inputSchema"}`. Note the camelCase
`inputSchema` — this is the MCP convention.

## Wire them into an MCP server

Pseudocode for a Python MCP server (`mcp` package):

```python
import json
import httpx
from mcp.server import Server

TOKEN = open("~/.context-mesh/token").read().strip()
BASE = "http://127.0.0.1:7421"
TOOLS = json.load(open("tools.json"))

server = Server("context-mesh")

@server.list_tools()
async def list_tools():
    return TOOLS

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "search_team_memory":
        r = httpx.post(f"{BASE}/search", headers={"Authorization": f"Bearer {TOKEN}"}, json=arguments)
    elif name == "drill_down_memory":
        r = httpx.get(f"{BASE}/node/{arguments['node_id']}", headers={"Authorization": f"Bearer {TOKEN}"})
    elif name == "add_memory":
        r = httpx.post(f"{BASE}/node", headers={"Authorization": f"Bearer {TOKEN}"}, json=arguments)
    elif name == "mark_memory_used":
        r = httpx.post(f"{BASE}/feedback", headers={"Authorization": f"Bearer {TOKEN}"}, json=arguments)
    elif name == "find_contradictions":
        r = httpx.post(f"{BASE}/contradictions", headers={"Authorization": f"Bearer {TOKEN}"}, json=arguments)
    else:
        raise ValueError(f"unknown tool: {name}")
    r.raise_for_status()
    return r.json()
```

Then register that MCP server with your MCP-aware client (Claude
Desktop, an MCP-aware IDE, etc.) per its own configuration docs.

## Troubleshooting

- **Client can't see the tools**: ensure your MCP server returns the
  exact `tools.json` payload from `list_tools` and that the client is
  pointed at your server transport (stdio vs SSE vs websocket).
- **Calls fail with 401**: the bearer token in your dispatcher is
  stale. Re-read `~/.context-mesh/token` after each `serve` restart.
- **`inputSchema` rejected**: confirm the MCP client uses the
  camelCase key. The Anthropic dialect (`input_schema`) is **not**
  interchangeable with the MCP dialect.
