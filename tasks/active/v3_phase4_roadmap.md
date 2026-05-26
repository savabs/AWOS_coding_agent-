# AWOS v3 Phase 4 — Feature Roadmap

**Date:** 2026-05-17  
**Status:** Planning — ready to execute in order  
**Prerequisite:** Phase 1–3 COMPLETE (105/105 tests PASS)

---

## Feature Queue (priority order)

| # | Feature | Effort | ROI | Spec | Task |
|---|---------|--------|-----|------|------|
| 1 | **Budget Hard Stop** ✅ | 1–2h | High | `docs/specs/budget_hard_stop_spec.md` | `tasks/active/budget_hard_stop_task.md` |
| 2 | **AWOS MCP Server** ✅ | 4–6h | Very High | `docs/specs/mcp_server_spec.md` | `tasks/active/mcp_server_task.md` |
| 3 | **Multi-Session Agent** ✅ | 4–6h | High | `docs/specs/multi_session_agent_spec.md` | `tasks/active/multi_session_agent_task.md` |
| 4 | **E2E Integration Test** ✅ | 2–3h | Medium | `docs/specs/e2e_integration_test_spec.md` | `tasks/active/e2e_integration_test_task.md` |
| — | Tool Macros | 6h | Low (DEFERRED) | — | revisit after 100+ tool usage logs |

---

## Rules for implementing from this plan

1. **Read the spec first.** Each spec is self-contained. Do not invent beyond it.
2. **Follow existing patterns.** Check `scaffold/agent/` for conventions before adding new files.
3. **Write the test before committing.** Every feature ships with a smoke test.
4. **Preflight is already done.** All research/spec/task files exist — go straight to coding.
5. **One feature at a time.** Mark task COMPLETE before starting the next.

---

## Context (for cheap models reading this)

```
Project root: /home/becmachlean/2024/projects/AWOS_coding_agent/
Agent code:   scaffold/agent/
Key files:
  scaffold/agent/orchestrator.py       — main execution engine
  scaffold/agent/unified_agent.py      — single entry point for requests
  scaffold/agent/budget_ledger.py      — BudgetLedger + check_budget()
  scaffold/agent/dag_executor.py       — parallel task execution
  scaffold/agent/task_decomposer.py    — retry with simplification
  scaffold/agent/core/reasoning.py     — ReasoningTrace, ReasoningSession
  scaffold/agent/memory/vector_memory.py — VectorMemory (ChromaDB)
  awos.py                              — CLI entry point (argparse)
  .awos/                               — runtime state (budget.json, traces/, sessions/)
  .vscode/mcp.json.template            — MCP server definitions for IDE
```

Import pattern (always use try/except relative+absolute):
```python
try:
    from .module import Class
except ImportError:
    from module import Class
```
