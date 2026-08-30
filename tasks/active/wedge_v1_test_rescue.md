# Task: Wedge v1 — Test Rescue Benchmark

**Status:** active
**Phase:** 1 (proper coding agent)
**Research:** [`docs/research/wedge_v1_test_rescue.md`](../docs/research/wedge_v1_test_rescue.md)  
**Spec:** [`docs/specs/wedge_v1_test_rescue_spec.md`](../docs/specs/wedge_v1_test_rescue_spec.md)  
**Product/Benchmark doc:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)
**Started:** 2026-06-25

---

## Context

Define and prove a **medium-scale CI repair benchmark** for the coding agent.

This task exists to validate:
- bounded autonomous work
- honest verification
- medium-scale planning and execution
- useful failure signals for future self-learning

`payment_utils` + `wedge_v1_assert.py` remain the assertion lab.
The larger CI repair corpus remains a benchmark workload, not the product identity.

**Exit:** Real repo or realistic fixture, 10+ scale, `SEMANTIC_PASS`, useful post-run failure analysis, and reviewable diff.

---

## Steps

### Phase 1 — Define benchmark (docs + corpus)
- [x] 1.1 Research doc → `docs/research/wedge_v1_test_rescue.md`
- [x] 1.2 Spec + `goal_assertions` schema → `docs/specs/wedge_v1_test_rescue_spec.md`
- [x] 1.3 Benchmark one-pager → `docs/product/wedge_v1_coffee_money.md`
- [x] 1.4 Corpus manifest → `tests/fixtures/wedge_v1/manifest.json`
- [x] 1.5 Primary demo fixture → `tests/fixtures/wedge_v1/payment_utils/`

### Phase 2 — Assertion gate
- [x] 2.1 `scripts/wedge_v1_assert.py` → verification: exit 0 on fixed fixture
- [x] 2.2 Unit test `tests/test_wedge_v1_assert.py` (fail on buggy, pass on golden)
- [ ] 2.3 Wire `goal_assertions` into mission JSON loader (optional field)

### Phase 3 — Live proof
- [x] 3.1 Demo script → `scripts/demo_wedge_v1_test_rescue.sh`
- [x] 3.2 Proof guide → `docs/wedge_v1_test_rescue_proof.md`
- [x] 3.3 Live proof: run `./scripts/demo_wedge_v1_test_rescue.sh` — watch for `WEDGE_ASSERT: SEMANTIC_PASS`
- [ ] 3.4 Live API proof: `awos worker start` on copied `payment_utils` + assert on worktree

### Phase 4 — Benchmark expansion
- [ ] 4.1 CI repair corpus (10+ failures, real repo or realistic fixture) — not `payment_utils` scale
- [ ] 4.2 PEI proof: compare benchmark runs vs raw API on same sprint goal
- [ ] 4.3 Failure taxonomy: record what kinds of failures this benchmark exposes in the coding agent

---

## Live proof checklist

- [x] Live proof: run `./scripts/demo_wedge_v1_test_rescue.sh` — watch for `WEDGE_ASSERT: SEMANTIC_PASS`

---

## Session log

| Date | Done | Next |
|------|------|------|
| 2026-06-25 | Research, spec, benchmark doc, payment_utils fixture, assert script, demo script, unit tests, live proof demo | API live proof on `awos worker`; expand corpus |

---

## Related

- [`docs/product/stage1_subscription_worker_guideline.md`](../docs/product/stage1_subscription_worker_guideline.md) — historical subscription framing
- [`scripts/benchmark_vs_raw_api.py`](../scripts/benchmark_vs_raw_api.py) — PEI cases overlap corpus
