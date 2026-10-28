# Research — CI Rescue Sprint head-to-head

**Date:** 2026-06-25  
**Fixture:** `tests/fixtures/wedge_v1/ci_rescue_sprint`

---

## Question

On the **same 18 fix steps**, does AWOS finish with **all tests passing** and **lower API cost** than calling the model once per step with no harness?

This is the first fair comparison for the Cursor-class wedge (28 broken tests, 5 files).

---

## Two arms

| Arm | What it does |
|-----|----------------|
| **Raw API** | For each of 18 steps: one `Worker` call, apply patch, next step. No orchestrator retries. |
| **AWOS** | Full `Orchestrator` with the same 18-step plan, verify loop, retries on failure. |

Both start from a **fresh copy** of the buggy practice repo. No worktree (temp dirs only). Same cheap model env as missions.

---

## Success

1. All 35 pytest tests pass  
2. `wedge_v1_assert.py` says honest pass (tests were not edited to cheat)  
3. Compare total API cost and wall time  

Target from product bar: AWOS cost ≤ 50% of raw when both pass (stretch goal when raw also passes).

---

## Script

`scripts/benchmark_ci_rescue_sprint.py`

Outputs:

- `.awos/benchmarks/ci_rescue_head_to_head_<timestamp>.json`
- `docs/product/ci_rescue_head_to_head.md`
