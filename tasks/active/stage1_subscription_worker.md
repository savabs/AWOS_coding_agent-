# Stage 1 Subscription Worker

**Guideline:** `docs/product/stage1_subscription_worker_guideline.md`  
**Spec:** `docs/specs/stage1_subscription_worker_spec.md`  
**Research:** `docs/research/stage1_subscription_worker.md`

**Status:** IN PROGRESS — Phase A

---

## Phase A — Pre-planned long mission

- [x] Product guideline written
- [x] Research + spec + task file
- [x] Wire `plan_file` in `awos mission start`
- [x] `stage1_long_workload.plan.json` (8 tasks)
- [x] Unit test `tests/test_mission_plan_loader.py`
- [x] Live run: `awos mission start --long` → rs_b2667c2e8eb7 completed (8 plan + 4_1/4_2 decompose)
- [x] Update `memories/repo/project_structure.md` active work

## Phase B — Benchmark

- [x] `docs/research/benchmark_vs_raw_api.md`
- [x] `scripts/benchmark_vs_raw_api.py`
- [x] `docs/product/pei_proof.md` — AWOS 100% vs raw 67%

## Phase C — `awos worker` CLI (next)

- [ ] `awos worker start|status|resume` thin wrapper

---

## Live run log

| Date | Session | Result | Notes |
|------|---------|--------|-------|
| 2026-06-23 | rs_b2667c2e8eb7 | **completed** | 8-task plan; task 4 decomposed; $0.016, 165s |
| 2026-06-23 | benchmark | **AWOS 3/3** | raw 2/3; see `docs/product/pei_proof.md` |
