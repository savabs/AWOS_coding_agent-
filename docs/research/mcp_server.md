# AWOS MCP Server — Research

## What is MCP?
Model Context Protocol (MCP) — JSON-RPC 2.0 over stdio or HTTP.
Any MCP client (Windsurf, Cursor, Claude Desktop) can call tools exposed by an MCP server.

## Why build AWOS as an MCP server?
Currently AWOS is CLI-only (`awos run`, `awos chat`).
If AWOS exposes itself as an MCP server, ANY MCP-enabled IDE can call:
- `awos_run(goal, codebase_root)` → run the full Orchestrator pipeline
- `awos_chat(message)` → single-turn UnifiedAgent response
- `awos_memory_search(query)` → search VectorMemory
- `awos_budget_status()` → return budget JSON
- `awos_traces_list()` → list reasoning sessions

This makes AWOS first-class in Windsurf, Claude Desktop, etc. without any CLI invocation.

## Python MCP SDK
Package: `mcp` (official Anthropic SDK)
```bash
pip install mcp
```

Server pattern (stdio transport):
```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("awos")

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(name="awos_run", description="...", inputSchema={...})]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "awos_run":
        result = orchestrator.execute_feature(arguments["goal"])
        return [types.TextContent(type="text", text=json.dumps(result))]

async def main():
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())

asyncio.run(main())
```

## Tools to expose (5 tools)

| Tool | Args | Returns |
|------|------|---------|
| `awos_run` | `goal: str`, `codebase_root: str = "."` | execution result dict |
| `awos_chat` | `message: str` | response string |
| `awos_memory_search` | `query: str`, `n: int = 3` | list of matches |
| `awos_budget_status` | (none) | budget dict |
| `awos_traces_list` | (none) | list of session summaries |

## MCP config to add to `.vscode/mcp.json`

```json
"awos": {
  "type": "stdio",
  "command": "python3",
  "args": ["mcp_server.py"],
  "cwd": "/home/becmachlean/2024/projects/AWOS_coding_agent",
  "description": "AWOS coding agent. Run goals, search memory, check budget, list traces."
}
```

## Dependencies
- `mcp` — install via `pip install mcp`
- `asyncio` — stdlib
- All AWOS modules already available
