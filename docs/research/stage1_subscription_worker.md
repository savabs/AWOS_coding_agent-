# Research — Stage 1 Subscription Worker

**Date:** 2026-06-21  
**Guideline:** [`docs/product/stage1_subscription_worker_guideline.md`](../product/stage1_subscription_worker_guideline.md)

---

## Problem

We want a **subscription-worthy one worker**: long-running, problem-solving, efficient, simple harness. Lab proofs (gauntlet G1–G7, short missions) exist, but:

1. **Long mission `rs_951ea5932eb0`** — 9 min wall time, **failed**: CheapPlanner collapsed 12 prose steps → 3 tasks; task 1 worker parse fail → decompose spiral.  
2. **No benchmark** — cannot claim “worth paying” vs raw API without paired runs.  
3. **UX split** — builders use Cursor + `awos mission`/`gauntlet`; no single `awos worker` path for customers.

---

## Root cause: planner collapse

| Path | Behavior |
|------|----------|
| `awos mission start --long` with prose `goal` only | `CheapPlanner.plan()` returns 2–4 merged tasks |
| `orchestrator.execute_feature(pre_planned_tasks=...)` | Skips planner; runs exact task list |

**Existing API:** `pre_planned_tasks` is used in tests, gauntlet G2/G3/G7, e2e mocks — **not wired to mission JSON**.

---

## Options considered

| Option | Pros | Cons |
|--------|------|------|
| **A. `plan_file` in mission JSON** | Deterministic steps; no planner change | Manual plan maintenance |
| **B. Fix CheapPlanner prompt** | Automatic | Still non-deterministic; failed on long goal |
| **C. Force N tasks in planner** | Flexible | Unreliable task boundaries |

**Decision:** **A first** (Phase A), keep planner for ad-hoc `awos run`.

---

## Pre-planned task schema

Reuse orchestrator task dict + [`plan_actions.normalize_plan`](../../scaffold/agent/plan_actions.py):

```json
{
  "task_id": 1,
  "task_type": "edit_file",
  "path": "scaffold/agent/runtime_session.py",
  "file": "scaffold/agent/runtime_session.py",
  "action": "Add one-line docstring to checkpoint() explaining pause/resume",
  "complexity": "low"
}
```

---

## Benchmark design (Phase B)

- **Fixture:** 3–5 small coding goals on this repo (or copy in `tests/fixtures/benchmark_goals.json`)  
- **Arms:** AWOS harness (worktree, verify loop) vs script calling same model API with single-shot edit prompt  
- **Metrics:** verified success, wall time, cost USD, retries  
- **Output:** `docs/product/pei_proof.md` + JSON in `.awos/benchmarks/`

Deferred to Phase B after one full pre-planned long mission completes.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Worker SEARCH/REPLACE fails on large files | Prefer small files in v1 plan; one file per task |
| Decompose spiral | Cap decomposition; prefer replan for verify-fail |
| False “long” claim with fast failures | Success criteria: **completed** 8+ tasks, not wall time alone |

---

## Next implementation step

Wire `plan_file` → `pre_planned_tasks` in `awos mission start` + ship `docs/missions/stage1_long_workload.plan.json` (8 tasks).
