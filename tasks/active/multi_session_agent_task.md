# Multi-Session Agent Task

## Status: PENDING

## Spec
`docs/specs/multi_session_agent_spec.md` — contains all code. Copy it.

## Steps
- [x] 1. Create `scaffold/agent/agent_state_manager.py`
- [x] 2. Add `AgentStateManager` import + `self.state_manager` to `Orchestrator.__init__`
- [x] 3. Add `resume: bool = False` param to `execute_feature()`
- [x] 4. Add resume skip logic in `execute_feature()` (before while loop)
- [x] 5. Update `execute_feature()` to track state via `self._current_state`
- [x] 6. Call `state_manager.finalize()` at end of `execute_feature()`
- [x] 7. Add `--resume` flag to `awos.py` `run` subcommand
- [x] 8. Add `goals` subcommand to `awos.py`
- [x] 9. Write `test_multi_session_agent.py` — 20 assertions
- [x] 10. Run test, mark COMPLETE

## Test Results
- `test_multi_session_agent.py`: 20/20 PASS

## Files to create
- `scaffold/agent/agent_state_manager.py`
- `test_multi_session_agent.py`

## Files to modify
- `scaffold/agent/orchestrator.py`
- `awos.py`

## Estimated effort
4–6 hours
