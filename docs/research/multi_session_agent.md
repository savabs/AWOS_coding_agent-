# Multi-Session Agent — Research

## Problem
Every `awos run` starts from zero. No memory of what was done last time.
If a goal takes 3 runs to complete, AWOS re-plans from scratch each time.

## What we already have
- `VectorMemory` — semantic search over past interactions (cross-session)
- `ReasoningTraceStore` — all sessions saved to `.awos/traces/*.json`
- `.awos/sessions/` — directory already exists (used by AWOS session manager)

## Solution: `AgentStateManager`

A lightweight JSON file per goal run at `.awos/state/<goal_hash>.json`:

```json
{
  "goal": "add user authentication",
  "goal_hash": "abc123",
  "started_at": "2026-05-17T01:00:00",
  "last_updated": "2026-05-17T01:30:00",
  "completed_task_ids": [1, 2, 3],
  "failed_task_ids": [4],
  "total_tasks": 5,
  "session_ids": ["orch_a1b2c3", "orch_d4e5f6"],
  "status": "in_progress"  # or "complete" | "failed"
}
```

On next `awos run --goal "add user authentication"`:
1. Compute `goal_hash = sha256(goal)[:8]`
2. Load state file if exists
3. Skip `completed_task_ids` from the plan
4. Resume from where it left off

## What this enables
- **Resume interrupted runs** — power cut, API error, etc.
- **Incremental delivery** — implement 2 tasks today, 3 tomorrow
- **Audit trail** — full history of what was done for each goal

## Scope (keep minimal)
- `AgentStateManager` class: `load(goal)`, `save(goal, state)`, `mark_complete(task_id)`, `mark_failed(task_id)`
- Wire into `Orchestrator.execute_feature()`: skip already-completed tasks before running
- Wire into `awos.py run` subcommand: pass `--resume` flag
- No UI changes, no new APIs

## State dir: `.awos/state/`
Already exists — used by AWOS for other state. New state files go here.
