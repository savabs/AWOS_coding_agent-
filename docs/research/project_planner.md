# Research: ProjectPlanner — Persistent Goal DAG (Phase 5C)

> Canonical HTML: docs/research/project_planner.html
> This file is a navigation stub for Obsidian only.

## Summary
ProjectPlanner closes the "goal amnesia" gap in AWOS. It wraps the existing Planner + TaskDecomposer + DAGExecutor with a persistent Goal DAG stored at .awos/goals/, enabling multi-session project memory, dependency-aware execution, and plan adaptation when tasks fail.

## Problem
Current AWOS discards its plan at session end. Every new request starts from zero — no awareness of prior work, completed tasks, or partial failures across sessions.

## Solution
- GoalNode dataclass: (goal_id, description, parent_ids, child_ids, tasks, status, ...)
- GoalGraph: directed acyclic graph of GoalNodes, persisted as JSON
- ProjectPlanner: load_or_create_goal(), update_node_status(), get_pending_nodes()
- INDEX.json: fast lookup for goal matching

## Key Design Decisions
- Keyword overlap (Jaccard, threshold 0.4) for goal matching — no LLM needed
- Atomic write pattern (.tmp + rename) for crash safety
- Max 50 open goals; archive completed goals after 30 days
- Feeds into ReflexionMemory (critique_ids per node) and RewardStore

## Research Sources (verified)
- AFlow (ICLR 2025 Oral, arXiv:2410.10762) — workflow as search over task graphs
- HiPlan (Aug 2025) — hierarchical planning: milestones + local steps, +12-18% coding success
- TMS (Manufacturing Letters 2025) — persistent goal state is #1 predictor of long-horizon success
- GoCodeo (2025) — persistent breadcrumb state reduces context-switching errors by 20-30%

## Related
- [[project_planner_spec]] — implementation spec
- [[project_planner_task]] — execution checklist
- [[reflexion_memory]] — Phase 5B (critique_ids in GoalNode)
- [[test_runner_reward]] — Phase 5A (reward signal per task)
