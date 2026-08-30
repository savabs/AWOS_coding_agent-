---
title: "Test Outcome Gate Proof"
tags:
  - doc/proof
  - phase/1
  - topic/coding-agent-reliability
---

# Test Outcome Gate Proof

## Purpose

Prove that AWOS does not report a coding task as successful when the edited repository tests fail, and that failed edits are restored. Also prove that a passing edit remains successful.

## Command

```bash
python3 -m pytest -q tests/test_test_outcome_gate.py tests/test_coding_agent_minimal_e2e.py tests/test_orchestrator_e2e.py
```

## Executed proof (2026-07-29)

`python3 -m pytest -q tests/test_test_outcome_gate.py tests/test_coding_agent_minimal_e2e.py tests/test_orchestrator_e2e.py` → **5 passed** (one pre-existing `RequestsDependencyWarning`). `python3 -m py_compile scaffold/agent/orchestrator.py tests/test_test_outcome_gate.py` also passed.

## Success markers

- All focused outcome-gate tests pass.
- Failure-case assertions observe `result["success"] is False`.
- Failure-case execution logs contain `status == "failed"`.
- Failure-case source files equal their pre-task contents after rollback.
- Passing-case source remains changed and the execution log contains `status == "completed"`.
- Existing minimal and orchestrator E2E tests remain green.

## Scope

This proof uses deterministic planner/worker/ReAct boundaries and real orchestrator bookkeeping, `GitManager` rollback, file I/O, test-result handling, and debug evidence. It makes no external model calls.

## Related

- [[test_outcome_gate]]
- [[test_outcome_gate_spec]]
- [[coding_agent_minimal_e2e]]
