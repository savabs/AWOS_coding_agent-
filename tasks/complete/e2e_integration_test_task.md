# E2E Integration Test Task

## Status: PENDING

## Spec
`docs/specs/e2e_integration_test_spec.md` — full test file content is in the spec. Just copy it.

## Steps
- [x] 1. Create `test_e2e_pipeline.py` at project root
- [x] 2. Run `python3 test_e2e_pipeline.py`
- [x] 3. Fix import path issues (added ANTHROPIC_API_KEY dummy)
- [x] 4. Fix mock path (agent.worker.Worker.execute_task) + performance tracker _load patch
- [x] 5. All assertions green → mark COMPLETE

## Test Results
- `test_e2e_pipeline.py`: 26/26 PASS

## Notes
- Mock path: `agent.worker.Worker.execute_task` (not scaffold.agent.worker)
- Also patched `ToolPerformanceTracker._load` to prevent loading corrupted real data
- Zero API calls — fully isolated test

## Files to create
- `test_e2e_pipeline.py`

## Files to modify
- None (this is a pure test file)

## Common pitfalls
- Mock path must match the import in orchestrator.py — use `"agent.worker.Worker.execute_task"` not `"scaffold.agent.worker.Worker.execute_task"`
- `side_effect` callable receives the same keyword args as the real function — signature must match
- `pre_planned_tasks` skips Planner; `use_parallel=True` uses DAGExecutor — both already supported

## Estimated effort
2–3 hours
