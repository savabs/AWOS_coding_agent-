---
title: "E2E Pipeline Bug Fixes — Planner, Worktree Merge, Verify-Task Loop"
tags:
  - doc/spec
  - phase/1
  - topic/e2e-pipeline
  - topic/bugfix
---

# Spec: E2E Pipeline Bug Fixes

## Goal
Fix 3 bugs found in the live end-to-end trial so the full Qwen Planner → DeepSeek Worker → tests → merge-back pipeline works.

## Root causes (from live trial 2026-08-06)

### Bug 1 — Qwen Planner blocked by env config
- `.env` has `AWOS_CHEAP_ONLY=true` and `AWOS_PREMIUM_BUDGET=0`
- `is_cheap_only()` in `escalation_engine.py:109` returns True
- Orchestrator line 867: `if is_cheap_only():` → selects CheapPlanner, never Qwen
- **Fix:** env config change (not a code change)

### Bug 2 — Worktree fix not merged back
- `virtual_execution_runtime.py:192`: `if auto_merge_enabled() and commit_sha:`
- `auto_merge_enabled()` checks `AWOS_WORKTREE_AUTO_MERGE` env var (line 34)
- Not set in `.env` → auto-merge is off → fix stays in worktree, never reaches original repo
- **Fix:** env config change (not a code change)

### Bug 3 — Verify-only tasks loop forever (CODE FIX)
- `react_worker.py:471` `_task_needs_edits()` checks if action contains keywords: "fix", "edit", "add", "implement", "update", "change", "correct"
- Task action "Verify all tests pass after the fix" contains "fix" (in "after the fix")
- So `needs_edits` returns True even though the task is a verification task
- Worker correctly runs tests, calls `finish`, but `files_changed` is empty
- Line 450: `has_required_edits = bool(files_changed) or not needs_edits` → False
- Line 451: `success = bool(finished and test_pass and has_required_edits)` → False
- Orchestrator decomposes "failed" task → sub-tasks also "fail" → infinite loop until depth limit
- **Fix:** Add verification keywords as override in `_task_needs_edits()`

## Files affected
- `.env` — config changes (Bugs 1 + 2)
- `scaffold/agent/react_worker.py` — `_task_needs_edits()` method (Bug 3)
- `tests/test_react_worker.py` — new test for verify-task success (Bug 3)

## Implementation steps

### Step 1: Fix env config (Bugs 1 + 2)
- Set `AWOS_CHEAP_ONLY=false`
- Set `AWOS_PREMIUM_BUDGET=20`
- Add `AWOS_WORKTREE_AUTO_MERGE=true`
- Verification: `python3 -c "from scaffold.agent.escalation_engine import is_cheap_only; print(is_cheap_only())"` → False

### Step 2: Fix `_task_needs_edits()` in react_worker.py (Bug 3)
- Add verification keywords: "verify", "test", "check", "validate", "confirm", "ensure"
- If any verification keyword is in the action, return False (no edits required)
- Verification: unit test — "Verify all tests pass after the fix" → needs_edits=False

### Step 3: Add regression test for verify-task success
- Test that a task with action "Verify all tests pass after the fix" does not require edits
- Test that a task with action "Fix the bug in add function" still requires edits
- Verification: `pytest tests/test_react_worker.py -q --timeout=10`

### Step 4: Live proof — re-run end-to-end trial
- Reset test repo to buggy state
- Run: `AWOS_NATIVE_PLANNER_REQUIRED=1 python3 awos.py run "..." --root /tmp/opencode/e2e_test`
- Watch for: Qwen Planner used, fix applied, tests pass, fix merged back to original repo

## Edge cases
- Task action "Fix the bug and verify tests pass" — should require edits (primary verb is "fix")
- Task action "Verify tests pass" — should NOT require edits (primary verb is "verify")
- Task action "Run tests to check the fix worked" — should NOT require edits (primary verb is "run tests")

## Testing plan
- Unit test: `_task_needs_edits()` with verify keywords
- Live proof: full end-to-end trial with Qwen Planner

## Live proof
- Command: `AWOS_NATIVE_PLANNER_REQUIRED=1 python3 awos.py run "Fix the bug in calculator.py: the add function returns a-b instead of a+b. Make all tests in test_calculator.py pass." --root /tmp/opencode/e2e_test`
- Success markers:
  - `[PLANNER]` shows `Planner` (not `CheapPlanner`)
  - All tasks complete (no infinite decomposition)
  - `calculator.py` in original repo has `return a + b`
  - All 4 tests pass in original repo

## Related
- [[checkpoint_2026-08-04_planner_holdout_session]] — last checkpoint showing graph contract fix
- [[planner_graph_contract_compatibility]] — planner metadata envelope fix