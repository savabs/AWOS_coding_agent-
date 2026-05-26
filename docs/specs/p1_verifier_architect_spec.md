# P1 Spec — Test Execution, Architect+Editor Split, Context Compaction

**Full spec**: [docs/specs/p1_verifier_architect_spec.html](p1_verifier_architect_spec.html)  
**Priority**: P1 High | **Effort**: 9h | **Prerequisite**: P0 complete

## What
Add pytest execution as objective verifier. Split worker into architect (reasoning) + editor (JSON edits) calls. Add SessionState for task history compaction.

## Steps (8)
1. Add TestResult dataclass + _discover_test_file() to Verifier (1h)
2. Add run_tests() to Verifier (1h)
3. Wire test execution into orchestrator retry loop (0.5h)
4. Add architect prompt builder to Worker (1h)
5. Add editor prompt + _execute_with_architect_split() (1.5h)
6. Add TaskSummary + SessionState dataclasses (0.5h)
7. Wire SessionState into orchestrator execute_feature() (1h)
8. Write tests/test_p1_improvements.py (13 tests) (1.5h)

## Files
- scaffold/agent/verifier.py — TestResult, run_tests
- scaffold/agent/worker.py — architect/editor split
- scaffold/agent/orchestrator.py — test wiring, SessionState
- scaffold/agent/session_state.py (new) — dataclasses
- tests/test_p1_improvements.py — new test file
