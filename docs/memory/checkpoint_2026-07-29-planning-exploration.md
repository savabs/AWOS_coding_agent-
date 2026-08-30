---
title: "Checkpoint: Planning & Exploration v1 — 2026-07-29"
tags:
  - doc/checkpoint
  - phase/1
  - topic/planning-exploration
---

# Session Checkpoint — Planning & Exploration v1 — 2026-07-29

> **This is an immutable historical record.**

## Session state

Implemented the first planning/exploration upgrade under [[planning_exploration_v1_spec]]. The agent now has a bounded deterministic exploration snapshot and pre-execution plan contract validation. Offline fixture assertions measure grounding quality rather than trusting planner prose.

## Implemented

- `scaffold/agent/exploration_phase.py`
  - `normalize_goal_references()` preserves explicit paths and symbols separately from fuzzy keywords.
  - `build_exploration_snapshot()` produces stable IDs, candidate/test/config files, grep evidence, negative evidence, bounds, and cost counters.
  - Existing `run_exploration()` remains backward-compatible while exposing snapshot fields.
  - Planner context now receives structured exploration metadata.
- `scaffold/agent/cheap_planner.py`
  - Planning prompt receives snapshot identity and grounded evidence requirements.
  - New task fields: `acceptance_check` and `evidence_ref`.
- `scaffold/agent/orchestrator.py`
  - Added `validate_plan_contract()`.
  - Model-generated plans are normalized and validated before execution.
  - Invalid paths and multi-file task shapes fail before Worker/ReAct execution.
  - Legacy pre-planned callers remain compatible.
- `tests/test_planning_exploration_v1.py`
  - 7 focused assertions cover normalization, deterministic replay, bounds, negative evidence, context propagation, plan validation, and offline fixture metrics.
- `tests/fixtures/planning_exploration_v1/manifest.json`
  - Reviewed single-file and test-repair ground truth.

## Validation

```text
python3 -m pytest -q tests/test_planning_exploration_v1.py tests/test_test_outcome_gate.py tests/test_coding_agent_minimal_e2e.py tests/test_orchestrator_e2e.py
11 passed, 1 warning

python3 -m py_compile scaffold/agent/exploration_phase.py scaffold/agent/cheap_planner.py scaffold/agent/orchestrator.py
passed
```

The warning is the existing `RequestsDependencyWarning`. No live model/API calls were used.

## Blocker

The broader command including `tests/test_grep_repo_map.py` cannot collect because that existing test imports `_infer_literal` from `scaffold.agent.tools.filesystem`, where the symbol is absent. This was not changed because it is outside the current spec. It should be resolved or explicitly retired before claiming a fully green repository-wide planning regression.

## Next action

Before implementation of sandboxing, create the separate research/spec/task triad for execution sandbox and approval safety. The sandbox slice must evaluate isolation, rollback, command policy, approvals, and cleanup with hostile/error-path tests. After that, create the self-learning/reward/evaluation slice with multi-trial, per-task reporting and no metric that can compensate for failed repository correctness.

## Related

- [[planning_exploration_v1]]
- [[planning_exploration_v1_spec]]
- [[test_outcome_gate]]
- [[coding_agent_minimal_e2e]]
