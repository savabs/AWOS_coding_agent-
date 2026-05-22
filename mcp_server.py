#!/usr/bin/env python3
"""
AWOS MCP Server — exposes AWOS capabilities to any MCP client.
Uses mcp.server.fastmcp (high-level API).

Tools:
  - awos_run(goal, codebase_root)        → full Orchestrator pipeline
  - awos_chat(message)                   → single-turn UnifiedAgent
  - awos_memory_search(query, n)         → vector memory search
  - awos_budget_status()                 → month-to-date budget
  - awos_traces_list()                   → reasoning trace sessions

Usage:
    python3 mcp_server.py          # stdio transport (for IDE)
"""
import sys
import os
import json

sys.path.insert(0, "scaffold")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("awos")


# ── Helper: safe JSON stringify ───────────────────────────────────────
def _json(obj: dict) -> str:
    return json.dumps(obj, indent=2, default=str)


# ── Tool 1: awos_run ──────────────────────────────────────────────────

@mcp.tool()
def awos_run(goal: str, codebase_root: str = ".") -> str:
    """Execute a coding goal through the full AWOS Orchestrator pipeline.

    Args:
        goal: The coding goal to accomplish (e.g. "add user auth")
        codebase_root: Root directory of the codebase (default: current dir)

    Returns:
        JSON string with execution summary (success, tasks_completed, errors, etc.)
    """
    try:
        from agent.orchestrator import Orchestrator
        orch = Orchestrator()
        result = orch.execute_feature(goal=goal, codebase_root=codebase_root)
        return _json(result)
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})


# ── Tool 2: awos_chat ───────────────────────────────────────────────

@mcp.tool()
def awos_chat(message: str) -> str:
    """Send a single message to the AWOS UnifiedAgent and get a response.

    Args:
        message: The message / request to send

    Returns:
        Agent's response text
    """
    try:
        from agent.unified_agent import UnifiedAgent
        agent = UnifiedAgent()
        return agent.handle_request(message)
    except Exception as exc:
        return f"Error: {exc}"


# ── Tool 3: awos_memory_search ──────────────────────────────────────

@mcp.tool()
def awos_memory_search(query: str, n: int = 3) -> str:
    """Search AWOS vector memory for past interactions similar to a query.

    Args:
        query: Search query string
        n: Number of results to return (default: 3)

    Returns:
        JSON list of matches with similarity scores
    """
    try:
        from agent.memory.vector_memory import VectorMemory
        mem = VectorMemory(persist_dir=".awos/memory")
        results = mem.retrieve(query, n_results=n)
        return _json({"results": results})
    except Exception as exc:
        return _json({"error": str(exc)})


# ── Tool 4: awos_budget_status ──────────────────────────────────────

@mcp.tool()
def awos_budget_status() -> str:
    """Return current month-to-date API budget status.

    Returns:
        JSON with spent, remaining, percent_used, monthly_budget
    """
    try:
        from agent.budget_ledger import get_ledger
        ledger = get_ledger()
        status = ledger.get_status()
        return _json(status)
    except Exception as exc:
        return _json({"error": str(exc)})


# ── Tool 5: awos_traces_list ────────────────────────────────────────

@mcp.tool()
def awos_traces_list() -> str:
    """List reasoning trace sessions recorded by AWOS.

    Returns:
        JSON list of sessions (id, goal, trace_count, timestamp)
    """
    try:
        from agent.core.reasoning import ReasoningTraceStore
        store = ReasoningTraceStore(persist_dir=".awos/traces")
        sessions = store.list_sessions()
        return _json({"sessions": sessions})
    except Exception as exc:
        return _json({"error": str(exc)})


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
