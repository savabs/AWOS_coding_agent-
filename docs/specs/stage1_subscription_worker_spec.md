# Spec — Stage 1 Subscription Worker (Phase A)

**Research:** [`docs/research/stage1_subscription_worker.md`](../research/stage1_subscription_worker.md)  
**Task:** [`tasks/active/stage1_subscription_worker.md`](../../tasks/active/stage1_subscription_worker.md)

---

## Scope (Phase A only)

1. Mission config supports **`plan_file`** (JSON array of tasks).  
2. `awos mission start --long` loads plan → `pre_planned_tasks` (skip CheapPlanner).  
3. Eight-task plan for long mission (docstring/comment edits, one file each).  
4. Unit test for plan loader.  
5. One live long mission run recorded in task tracker.

**Out of scope (Phase B+):** benchmark script, `awos worker` CLI, Stripe.

---

## Mission config shape

```json
{
  "id": "stage1_long_workload_v1",
  "goal": "Human-readable summary for session record",
  "plan_file": "stage1_long_workload.plan.json",
  ...
}
```

`plan_file` is relative to `docs/missions/`.

Alternative: inline `"plan": [ ... ]` (same schema).

---

## Loader behavior

1. If `plan` or `plan_file` present → load tasks, `normalize_plan()`, pass to orchestrator.  
2. Print: `[MISSION] Pre-planned task list: N tasks (planner skipped)`.  
3. If absent → current behavior (planner breaks down `goal`).

---

## Long mission plan (v1)

8 tasks, low complexity, existing files only — see `docs/missions/stage1_long_workload.plan.json`.

---

## Success criteria (Phase A)

- [ ] `awos mission start --long` logs `Pre-planned task list: 8 tasks`  
- [ ] No `[PLANNER] Generated 3 tasks` on long start when plan_file set  
- [ ] Unit test passes  
- [ ] One live run: session `completed` with ≥6/8 tasks OR documented failure mode for next fix  

---

## Files to touch

| File | Change |
|------|--------|
| `awos.py` | `_load_mission_plan()`, wire `cmd_mission_start` / `cmd_run` |
| `docs/missions/stage1_long_workload.json` | Add `plan_file`, shorten `goal` |
| `docs/missions/stage1_long_workload.plan.json` | New — 8 tasks |
| `tests/test_mission_plan_loader.py` | New |
| `docs/product/...` | Guideline (done) |
