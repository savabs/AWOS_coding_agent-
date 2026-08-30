---
title: "E2E Pipeline Bug Fixes"
tags:
  - doc/task
  - phase/1
  - topic/e2e-pipeline
  - topic/bugfix
  - status/active
---

# Task: E2E Pipeline Bug Fixes

**Goal:** Fix 3 bugs found in live end-to-end trial so Qwen Planner → DeepSeek Worker → tests → merge-back works.
**Spec:** [[e2e_pipeline_bugfixes_spec]]

## Checklist

- [x] **1.1** Fix env config: set `AWOS_CHEAP_ONLY=false`, `AWOS_PREMIUM_BUDGET=20`, add `AWOS_WORKTREE_AUTO_MERGE=true`
  - Verification: `python3 -c "from scaffold.agent.escalation_engine import is_cheap_only; print(is_cheap_only())"` → False ✓

- [x] **1.2** Fix `_task_needs_edits()` in `react_worker.py`: add verification keywords override
  - Verification: unit test passes — "Verify all tests pass after the fix" → needs_edits=False ✓

- [x] **1.3** Add regression test for verify-task success in `tests/test_react_worker.py`
  - Verification: `pytest tests/test_react_worker.py -q --timeout=10` — 30 passed ✓

- [x] **1.4** Run `ruff check scaffold/agent/react_worker.py tests/test_react_worker.py`
  - Verification: 1 pre-existing error (line 579, unrelated to change) ✓

- [x] **1.5** Run `pytest tests/test_react_worker.py tests/test_orchestrator_e2e.py -q --timeout=10`
  - Verification: react_worker 30 passed; orchestrator 4 passed + 1 pre-existing error (unrelated) ✓

- [ ] **1.6** Live proof: re-run end-to-end trial with Qwen Planner
  - Verification: Qwen Planner used, fix applied, tests pass, fix merged back to original repo

## Related
- [[e2e_pipeline_bugfixes_spec]] — spec for this task
- [[checkpoint_2026-08-04_planner_holdout_session]] — prior context