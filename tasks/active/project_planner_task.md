# Task: ProjectPlanner — Persistent Goal DAG (Phase 5C)

> Canonical HTML: tasks/active/project_planner_task.html
> This file is a navigation stub for Obsidian only.

## Goal
Give AWOS persistent, cross-session project memory via a Goal DAG at .awos/goals/, enabling the agent to resume work, skip completed tasks, and adapt to failures across sessions.

## Links
- Spec: [[project_planner_spec]]
- Research: [[project_planner_research]]
- Phase: 5C (follows 5A TestRunner, 5B ReflexionMemory)

## Pre-conditions (all met)
- TaskDecomposer exists and works
- DAGExecutor exists and works
- Orchestrator has task-level success/failure hooks
- ErrorPatternStore (Phase 5B) exists — feeds critique_ids into GoalNode

## Steps (11 atomic)
- [x] 1. Create scaffold/agent/project_planner.py — GoalNode, GoalGraph, ProjectPlanner skeleton
- [x] 2. Implement load_or_create_goal() with Jaccard matching (threshold 0.4) + INDEX.json
- [x] 3. Implement update_node_status() with propagation + atomic write (.tmp + rename)
- [x] 4. Implement append_nodes() for goal extension + GoalGraphFullError at 200 nodes
- [x] 5. Wire ProjectPlanner into Orchestrator.__init__ and execute_feature()
- [x] 6. Wire update_node_status() into Orchestrator._execute_single_task()
- [x] 7. Add existing_goal param to Planner.plan() to skip completed tasks
- [x] 8. Write tests/test_project_planner.py (53 tests, all use tmp_path)
- [x] 9. Write tests/test_project_planner_integration.py — provider-free interrupted/resumed multi-task proof (2 tests pass)
- [x] 10. Enforce model-role separation — advanced `Planner` decomposes projects; cheap models execute atomic tasks (56 focused tests pass)
- [ ] 11. pytest tests/ -v --tb=short — full-suite gate open; selected suite has 3 pre-existing RuntimeSession CLI failures

## Key Decisions
- 2026-05-19: Jaccard keyword overlap for goal matching, not LLM embeddings (zero extra cost)
- 2026-05-19: One JSON file per goal (crash isolation); atomic write via .tmp + os.replace()
- 2026-05-19: ProjectPlanner is an orchestration wrapper above Planner.plan(), does not replace it
