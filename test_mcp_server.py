#!/usr/bin/env python3
"""
Smoke test for AWOS MCP Server.
Tests tool registration + direct function calls. No server startup needed.
"""
import sys
import os
import json

sys.path.insert(0, "scaffold")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

import mcp_server

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    mark = PASS if cond else FAIL
    extra = f" — {detail}" if not cond and detail else ""
    print(f"  {mark} {name}{extra}")


print("=" * 60)
print("AWOS MCP Server Smoke Test")
print("=" * 60)

# ── 1. FastMCP app exists ─────────────────────────────────────────────
print("\n1. App initialization")
check("app_exists", mcp_server.mcp is not None)
check("app_name_awos", mcp_server.mcp.name == "awos", mcp_server.mcp.name)

# ── 2. All 5 tools registered ────────────────────────────────────────
print("\n2. Tool registration")
tool_objs = mcp_server.mcp._tool_manager.list_tools()
tools = [t.name for t in tool_objs]
check("five_tools", len(tools) == 5, f"got {len(tools)}: {tools}")
expected = {"awos_run", "awos_chat", "awos_memory_search", "awos_budget_status", "awos_traces_list"}
check("expected_names", set(tools) == expected, f"got {set(tools)}")

# ── 3. awos_budget_status returns valid JSON ────────────────────────
print("\n3. awos_budget_status() — no API keys needed")
try:
    raw = mcp_server.awos_budget_status()
    check("budget_is_str", isinstance(raw, str))
    data = json.loads(raw)
    check("budget_has_spent", "spent" in data or "error" in data, f"keys={list(data.keys())}")
    check("budget_has_percent", "percent_used" in data or "error" in data)
except Exception as exc:
    check("budget_no_exception", False, str(exc))

# ── 4. awos_traces_list returns valid JSON ──────────────────────────
print("\n4. awos_traces_list() — no API keys needed")
try:
    raw = mcp_server.awos_traces_list()
    check("traces_is_str", isinstance(raw, str))
    data = json.loads(raw)
    check("traces_has_sessions", "sessions" in data or "error" in data)
except Exception as exc:
    check("traces_no_exception", False, str(exc))

# ── 5. awos_memory_search handles missing index gracefully ──────────
print("\n5. awos_memory_search() — graceful on missing index")
try:
    raw = mcp_server.awos_memory_search("test query", n=2)
    check("memory_is_str", isinstance(raw, str))
    # Missing index is OK — error field should be present
    data = json.loads(raw)
    check("memory_has_results_or_error", "results" in data or "error" in data,
          f"keys={list(data.keys())}")
except Exception as exc:
    check("memory_no_exception", False, str(exc))

# ── 6. awos_chat returns string ─────────────────────────────────────
print("\n6. awos_chat() — returns string (no API keys = may return error)")
try:
    raw = mcp_server.awos_chat("hello")
    check("chat_is_str", isinstance(raw, str), f"type={type(raw)}")
    check("chat_not_empty", len(raw) > 0, f"got {raw!r}")
except Exception as exc:
    check("chat_no_exception", False, str(exc))

# ── 7. awos_run returns JSON ────────────────────────────────────────
print("\n7. awos_run() — returns JSON (no API keys = early failure OK)")
try:
    raw = mcp_server.awos_run("add hello function", codebase_root=".")
    check("run_is_str", isinstance(raw, str))
    data = json.loads(raw)
    check("run_has_success_key", "success" in data or "error" in data,
          f"keys={list(data.keys())}")
except Exception as exc:
    check("run_no_exception", False, str(exc))

# ── 8. Each tool has a docstring / description ──────────────────────
print("\n8. Tool descriptions")
for tool_name in expected:
    tool_info = mcp_server.mcp._tool_manager.get_tool(tool_name)
    has_desc = tool_info is not None and tool_info.description is not None and len(tool_info.description.strip()) > 10
    check(f"{tool_name}_has_desc", has_desc,
          f"desc={tool_info.description[:40]!r}" if tool_info else "missing")

# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed_count = sum(1 for _, ok, _ in results if not ok)
print(f"Total: {passed}/{len(results)} passed  |  {failed_count} failed")
if failed_count:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL {name}: {detail}")
    sys.exit(1)
else:
    print("\nAll MCP Server tests PASSED.")
    sys.exit(0)
