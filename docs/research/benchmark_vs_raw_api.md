# Research — Benchmark vs Raw API

**Date:** 2026-06-21  
**Guideline:** [`docs/product/stage1_subscription_worker_guideline.md`](../product/stage1_subscription_worker_guideline.md)

---

## Question

Does AWOS harness deliver **more verified fixes per dollar** than calling the same worker model once (raw API)?

---

## Method

| Arm | What runs |
|-----|-----------|
| **raw** | `Worker.execute_task` × 1 attempt → `Verifier.verify_and_apply` → pytest |
| **awos** | `Orchestrator.execute_feature` with `pre_planned_tasks` (retries, replan, escalation) → pytest |

Both arms:
- Same env: `AWOS_CHEAP_ONLY=true`, `AWOS_LEARNING_DISABLE=true`, no worktree
- Isolated temp copy of fixture (`tests/fixtures/benchmark/<case>/`)
- Cost from dedicated `TokenTracker` per arm

---

## Fixtures

Three self-contained bug cases (copied from `scaffold/tests/bug_cases/`):

1. `01_off_by_one` — loop bounds  
2. `02_wrong_logic` — cache eviction + palindrome  
3. `03_simple_add` — `broken_add` returns `a - b`

Success = **pytest passes** on `test_case.py` after edit.

---

## Outputs

- `.awos/benchmarks/benchmark_vs_raw_<timestamp>.json`
- `docs/product/pei_proof.md` (human summary)

---

## Existing code

`scaffold/agent/benchmark_runner.py` compares single-shot vs MCTS on bug cases — different question. New script compares **full harness vs one-shot worker**.
