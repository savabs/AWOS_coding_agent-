# Budget Hard Stop — Spec

## What to build
Wire `BudgetLedger.check_budget()` into the Orchestrator and UnifiedAgent so no
API call fires when the monthly budget is exhausted.

---

## Change 1 — `scaffold/agent/orchestrator.py`

### In `execute_feature()` — before the while loop (after planning)

```python
# Guard: check budget before starting execution
from scaffold.agent.budget_ledger import get_ledger
_ledger = get_ledger()
_allowed, _reason = _ledger.check_budget(estimated_cost=0.01)
if not _allowed:
    print(f"[BUDGET] ⛔ Cannot start execution — {_reason}")
    return {
        "success": False,
        "goal": goal,
        "tasks_completed": 0,
        "tasks_failed": len(tasks),
        "total_tasks": len(tasks),
        "execution_log": [{"task_id": "all", "status": "failed", "reason": _reason}],
        "errors": [_reason],
        "time_elapsed": time.time() - start_time,
    }
```

### In `_execute_single_task()` — before the Worker attempts loop

```python
# Budget guard per task
from scaffold.agent.budget_ledger import get_ledger
_ledger = get_ledger()
_allowed, _reason = _ledger.check_budget(estimated_cost=0.05)
if not _allowed:
    print(f"[BUDGET] ⛔ Skipping task {task_id} — {_reason}")
    self.execution_log.append({
        "task_id": task_id,
        "status": "failed",
        "reason": f"Budget block: {_reason}",
    })
    return {"task_id": task_id, "success": False, "task": task}
elif _reason:  # warning
    print(f"[BUDGET] {_reason}")
```

---

## Change 2 — `scaffold/agent/unified_agent.py`

### In `_call_deepseek()` — before `client.chat.completions.create(...)`

```python
if self.budget_ledger:
    allowed, reason = self.budget_ledger.check_budget(estimated_cost=0.01)
    if not allowed:
        raise RuntimeError(f"Budget blocked: {reason}")
    if reason:
        logger.warning(reason)
```

### In `_call_anthropic()` — same pattern

```python
if self.budget_ledger:
    allowed, reason = self.budget_ledger.check_budget(estimated_cost=0.05)
    if not allowed:
        raise RuntimeError(f"Budget blocked: {reason}")
    if reason:
        logger.warning(reason)
```

---

## Notes
- Import `get_ledger` at the top of each file (already imported in orchestrator via existing pattern)
- `check_budget` is pure read — no side effects, safe to call any time
- `estimated_cost` is a **pre-call estimate** — not the actual cost (which is recorded after)
- Warning (90%) should print but NOT block
- Hard stop (100%) returns `False` → skip the call

---

## Test file: `test_budget_hard_stop.py`

```python
# Test 1: check_budget blocks when over budget
ledger = BudgetLedger(".awos/test_budget.json")
ledger._monthly_total = 21.0  # force over budget
allowed, reason = ledger.check_budget(0.01, monthly_budget=20.0)
assert not allowed
assert "BUDGET" in reason

# Test 2: check_budget warns at 90%
ledger._monthly_total = 18.5
allowed, reason = ledger.check_budget(0.01, monthly_budget=20.0)
assert allowed
assert "WARNING" in reason

# Test 3: check_budget passes under 90%
ledger._monthly_total = 5.0
allowed, reason = ledger.check_budget(0.01, monthly_budget=20.0)
assert allowed
assert reason == ""
```
