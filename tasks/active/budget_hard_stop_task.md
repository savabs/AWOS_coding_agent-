# Budget Hard Stop Task

## Status: PENDING

## Spec
`docs/specs/budget_hard_stop_spec.md` — read this first

## Steps
- [x] 1. Add budget guard to `Orchestrator.execute_feature()` (before while loop)
- [x] 2. Add budget guard to `Orchestrator._execute_single_task()` (before Worker loop)
- [x] 3. Add budget guard to `UnifiedAgent._call_deepseek()`
- [x] 4. Add budget guard to `UnifiedAgent._call_anthropic()`
- [x] 5. Write `test_budget_hard_stop.py` — 12 assertions
- [x] 6. Run test, mark COMPLETE

## Test Results
- `test_budget_hard_stop.py`: 12/12 PASS

## Files to modify
- `scaffold/agent/orchestrator.py`
- `scaffold/agent/unified_agent.py`

## New files
- `test_budget_hard_stop.py`

## Estimated effort
1–2 hours. All logic already exists in `BudgetLedger.check_budget()`.
This task is purely wiring — no new logic required.
