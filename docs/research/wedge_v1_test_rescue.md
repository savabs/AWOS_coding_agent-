# Research — Wedge v1: Test Rescue Benchmark

**Date:** 2026-06-25  
**Benchmark doc:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)

---

## Problem

AWOS needs a **medium-scale, verifiable coding benchmark** that is stronger than toy bugfixes but still bounded enough to analyze honestly.

The benchmark should help answer:
- can the coding agent finish a non-trivial repair task?
- does verification catch cheating or false greens?
- what failure modes appear before broad self-learning claims are made?

---

## Why this benchmark

CI repair remains useful because it is:
- concrete
- bounded
- honest to verify with pytest
- large enough to stress planning, execution, and recovery

This makes it a good proving workload for the coding agent.

---

## Benchmark shape

| Candidate job | Verifiable? | AWOS fit |
|---------------|-------------|----------|
| Docstrings / README | Partial | Too weak |
| Fix 1–3 tests | Yes | Assertion lab only |
| **CI repair sprint** (10–40 reds → green) | Yes | Strong benchmark |
| Package hardening (5–15 files) | Yes | Strong benchmark |
| Full feature from prose | Hard | Later |

**Decision:** **Test Rescue** stays tied to assertion infrastructure (`wedge_v1_assert.py`, pytest oracle). The larger CI repair sprint remains a benchmark workload for the coding agent.

---

## Why pytest is the right oracle here

For this benchmark, **pytest is the semantic oracle**:
- `must_pass`: originally red tests that must now be green
- `must_still_pass`: full suite or regression subset
- `forbidden_paths`: stop the agent from editing tests to cheat

This is stronger than a superficial “the file changed” success condition.

---

## Existing assets

| Asset | Reuse |
|-------|-------|
| `tests/fixtures/benchmark/01–03` | small benchmark cases |
| `scripts/benchmark_vs_raw_api.py` | compare harness vs raw API |
| `awos worker start/status/resume/diff` | operator surface |
| Worktree + RuntimeSession | sandbox |
| `Verifier` + retry loop | important existing mechanism |

---

## Risks

| Risk | Mitigation |
|------|------------|
| Agent edits tests to pass | `forbidden_paths` + held-out checks |
| Benchmark too small | expand beyond `payment_utils` |
| False green on prose-like tasks | keep benchmark test-bound |
| Benchmark mistaken for product identity | explicitly frame as coding-agent validation only |

---

## Next step

Spec: [`docs/specs/wedge_v1_test_rescue_spec.md`](../specs/wedge_v1_test_rescue_spec.md)  
Task: [`tasks/active/wedge_v1_test_rescue.md`](../../tasks/active/wedge_v1_test_rescue.md)
