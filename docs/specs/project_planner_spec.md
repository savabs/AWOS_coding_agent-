# Spec: ProjectPlanner — Persistent Goal DAG (Phase 5C)

> Canonical HTML: docs/specs/project_planner_spec.html
> This file is a navigation stub for Obsidian only.

## Goal
Give AWOS persistent memory of multi-session projects by maintaining a Goal DAG at .awos/goals/, so the agent knows which tasks are complete, which failed, and which are pending — without re-running completed work or losing failure history.

## Model-role contract
The advanced `Planner` owns project-level reasoning and decomposition. It must produce sufficiently atomic, grounded tasks for cheap execution models. Cheap models are task solvers, not the default authority for breaking down large project goals. `CheapPlanner` is permitted only for explicit cheap-only mode or as a bounded fallback when the advanced planner is unavailable.

## Files Affected
| File | Change |
|---|---|
| scaffold/agent/project_planner.py | CREATE — GoalNode, GoalGraph, ProjectPlanner |
| scaffold/agent/orchestrator.py | MODIFY — advanced planner selection + ProjectPlanner wiring |
| scaffold/agent/planner.py | MODIFY — add existing_goal param to plan() |
| scaffold/agent/cheap_planner.py | MODIFY — document cheap-only/fallback role |
| tests/test_project_planner.py | CREATE — 25+ unit tests |
| tests/test_coding_agent_minimal_e2e.py | MODIFY — update planner-role mock boundary |
| tests/test_project_planner_integration.py | MODIFY — assert advanced-planner/cheap-solver separation |

| tests/test_project_planner_integration.py | CREATE — provider-free multi-session orchestration proof |

## Steps (10 atomic steps)
1. Create project_planner.py with GoalNode, GoalGraph, ProjectPlanner
2. Implement load_or_create_goal() with Jaccard matching + INDEX.json
3. Implement update_node_status() with propagation + atomic write
4. Implement append_nodes() for goal extension and decomposition expansion
5. Wire ProjectPlanner into Orchestrator.__init__ and execute_feature()
6. Wire update_node_status() into Orchestrator._execute_single_task()
7. Add existing_goal param to Planner.plan() to skip completed tasks
8. Write tests/test_project_planner.py (25+ tests)
9. Write tests/test_project_planner_integration.py — provider-free interrupted/resumed multi-task proof
10. Run full test suite — zero regressions

## Key Constants
- MATCH_THRESHOLD = 0.4 (Jaccard keyword overlap for goal matching)
- MAX_REPLAN = 2 (max decomposition cycles per node)
- MAX_NODES_PER_GRAPH = 200
- MAX_OPEN_GOALS = 50
- GOALS_DIR = ".awos/goals/"
- GoalNode statuses: pending | in_progress | completed | failed | stalled | blocked

## Related
- [[project_planner_research]] — research document
- [[project_planner_task]] — execution checklist
- [[reflexion_memory_spec]] — Phase 5B (critique_ids in GoalNode)
