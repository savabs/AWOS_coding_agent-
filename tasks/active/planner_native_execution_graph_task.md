---
title: "Task: Planner-Native Execution Graph"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/execution-graph
  - layer/planning
---

> **Content:** [planner_native_execution_graph_task.html](planner_native_execution_graph_task.html) — open in browser.

## Related
- [[planner_native_execution_graph]]
- [[planner_native_execution_graph_spec]]
- [[execution_graph_integration_task]]

## Status
Active. Implementation is gated by [[planner_native_execution_graph_spec]].

## Steps
- [x] 1.1 Define and validate graph version 1 contract → provider-free contract tests pass (11 tests)
- [x] 1.2 Add deterministic legacy plan projection → projection tests pass
- [x] 1.3 Request structured graph JSON from advanced Planner → mocked request shape passes
- [x] 1.4 Integrate native and legacy parsing → mocked Planner responses pass
- [x] 1.5 Run focused regression validation → 124 focused tests passed

## Blockers
- Repository-wide Obsidian lint reports 371 pre-existing errors; use focused validation and report this limitation.

## Decisions
- Advanced Planner owns project-level decomposition and graph creation.
- CheapPlanner remains a fallback/task-list planner and never becomes the project-graph authority.
- Native graph execution is a separate follow-up after this output contract is proven.
