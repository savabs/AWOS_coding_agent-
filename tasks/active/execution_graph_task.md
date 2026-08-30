---
title: "Task: Typed Execution Graph"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/execution-graph
  - layer/orchestration
---

# Task: Typed Execution Graph

**Status:** active
**Phase:** 1
**Research:** [[execution_graph]]
**Spec:** [[execution_graph_spec]]
**Started:** 2026-08-02

## Context

Create the canonical execution graph beneath the advanced Planner and above runtime execution. The first slice must be deterministic and provider-free: typed operation nodes, dependency validation, safe ready waves, test barriers, failure blocking, recovery insertion, and persistence. It is intentionally not wired into the production orchestrator until its contract is proven.

## Steps

### Phase 1.1: Graph contract
- [x] 1.1.1: Create `ExecutionNode` and node-type/status enums → import and serialization check
- [x] 1.1.2: Create `ExecutionGraph` storage and JSON round-trip → round-trip test passes
- [x] 1.1.3: Reject duplicate IDs, unknown dependencies, and invalid node types → validation tests pass
- [x] 1.1.4: Reject dependency cycles → cycle test passes

### Phase 1.2: Scheduling and recovery
- [x] 1.2.1: Calculate deterministic ready waves → independent-node wave test passes
- [x] 1.2.2: Serialize same-file writes → conflict test passes
- [x] 1.2.3: Enforce test/barrier dependencies and failed-prerequisite blocking → barrier tests pass
- [x] 1.2.4: Add bounded recovery nodes → recovery ordering and cycle tests pass

### Phase 1.3: Tests
- [x] 1.3.1: Write provider-free unit tests in `tests/test_execution_graph.py`
- [x] 1.3.2: Run focused graph suite with zero failures

### Phase 1.4: Cleanup
- [ ] 1.4.1: Run diagnostics and scoped docs lint — diagnostics clean; repository-wide Obsidian lint remains blocked by 371 pre-existing errors outside this slice
- [x] 1.4.2: Update `memories/repo/project_structure.md`
- [x] 1.4.3: Write checkpoint

## Blockers

| Blocker | Blocking step | Resolution needed |
|---|---|---|
| — | — | — |

## Decisions Made

| Decision | Rationale | Date |
|---|---|---|
| Keep execution graph separate from GoalGraph | GoalGraph tracks project intent/progress; ExecutionGraph tracks concrete operations and runtime dependencies | 2026-08-02 |
| Use stdlib-only deterministic scheduling | Avoid new dependency and keep graph validation cheap and reproducible | 2026-08-02 |

## Related

- [[execution_graph]]
- [[execution_graph_spec]]
- [[project_planner_task]]
- [[dag_executor_task]]
