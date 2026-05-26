# AWOS MCP Server — Spec

## Output: `mcp_server.py` (new file at project root)

One file. ~120 lines. Uses `mcp` Python SDK (stdio transport).

---

## Full file structure

```python
"""AWOS MCP Server — exposes AWOS capabilities to any MCP client."""
import asyncio
import json
import sys
import os
sys.path.insert(0, "scaffold")

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# --- lazy imports so mcp_server.py works even if AWOS deps are missing ---
def _get_agent():
    from agent.unified_agent import UnifiedAgent
    return UnifiedAgent()

def _get_orchestrator():
    from agent.orchestrator import Orchestrator
    return Orchestrator()

def _get_memory():
    from agent.memory.vector_memory import VectorMemory
    return VectorMemory(persist_dir=".awos/memory")

def _get_budget():
    from agent.budget_ledger import get_ledger
    return get_ledger()

def _get_traces():
    from agent.core.reasoning import ReasoningTraceStore
    return ReasoningTraceStore(persist_dir=".awos/traces")

app = Server("awos")

# ── Tool definitions ──────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="awos_run",
            description="Execute a coding goal through the full AWOS Orchestrator pipeline (Planner → Worker → Verifier). Returns execution summary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The coding goal to accomplish"},
                    "codebase_root": {"type": "string", "description": "Root directory of codebase", "default": "."},
                },
                "required": ["goal"],
            },
        ),
        types.Tool(
            name="awos_chat",
            description="Send a single message to the AWOS UnifiedAgent and get a response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to send"},
                },
                "required": ["message"],
            },
        ),
        types.Tool(
            name="awos_memory_search",
            description="Search AWOS vector memory for past interactions similar to a query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "n": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="awos_budget_status",
            description="Return current month-to-date API budget status.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="awos_traces_list",
            description="List reasoning trace sessions recorded by AWOS.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

# ── Tool implementations ──────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "awos_run":
            orch = _get_orchestrator()
            result = orch.execute_feature(
                goal=arguments["goal"],
                codebase_root=arguments.get("codebase_root", "."),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "awos_chat":
            agent = _get_agent()
            response = agent.handle_request(arguments["message"])
            return [types.TextContent(type="text", text=response)]

        elif name == "awos_memory_search":
            mem = _get_memory()
            results = mem.retrieve(arguments["query"], n_results=arguments.get("n", 3))
            return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "awos_budget_status":
            ledger = _get_budget()
            status = ledger.get_status()
            return [types.TextContent(type="text", text=json.dumps(status, indent=2))]

        elif name == "awos_traces_list":
            store = _get_traces()
            sessions = store.list_sessions()
            return [types.TextContent(type="text", text=json.dumps(sessions, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as exc:
        return [types.TextContent(type="text", text=f"Error: {exc}")]

# ── Entry point ───────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

---

## MCP config update — `.vscode/mcp.json.template`

Add this entry to the `"servers"` object:

```json
"awos": {
  "type": "stdio",
  "command": "python3",
  "args": ["mcp_server.py"],
  "description": "AWOS coding agent. Tools: awos_run, awos_chat, awos_memory_search, awos_budget_status, awos_traces_list."
}
```

---

## requirements.txt addition

Add line: `mcp>=1.0.0`

---

## Test: `test_mcp_server.py`

```python
# Test 1: list_tools returns 5 tools (no server startup needed)
import asyncio, sys
sys.path.insert(0, "scaffold")
from mcp_server import list_tools

tools = asyncio.run(list_tools())
assert len(tools) == 5
names = {t.name for t in tools}
assert names == {"awos_run", "awos_chat", "awos_memory_search", "awos_budget_status", "awos_traces_list"}

# Test 2: awos_budget_status tool returns valid JSON (no network needed)
from mcp_server import call_tool
result = asyncio.run(call_tool("awos_budget_status", {}))
assert result[0].type == "text"
import json
data = json.loads(result[0].text)
assert "spent" in data or "Error" in result[0].text  # error OK if no budget file

# Test 3: unknown tool returns error string
result = asyncio.run(call_tool("not_a_tool", {}))
assert "Unknown tool" in result[0].text
```
