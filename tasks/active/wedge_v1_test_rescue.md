# Task: Wedge v1 — Test Rescue

**Status:** active  
**Phase:** 4 (revenue wedge)  
**Research:** [`docs/research/wedge_v1_test_rescue.md`](../docs/research/wedge_v1_test_rescue.md)  
**Spec:** [`docs/specs/wedge_v1_test_rescue_spec.md`](../docs/specs/wedge_v1_test_rescue_spec.md)  
**Product:** [`docs/product/wedge_v1_coffee_money.md`](../docs/product/wedge_v1_coffee_money.md)  
**Started:** 2026-06-25

---

## Context

Define and prove the **minimum job worth ₹2,000/month vs Cursor** (~400M auto tokens): afternoon-scale **CI Rescue Sprint**, not micro test fixes. `payment_utils` + `wedge_v1_assert.py` = assertion lab only.

**Exit:** Real repo, 30–120 min, 10+ scale, `SEMANTIC_PASS`, PEI ≤50% vs raw API; 1 payer who'd substitute Cursor for this job class.

---

## Steps

### Phase 1 — Define wedge (docs + corpus)

- [x] 1.1 Research doc → `docs/research/wedge_v1_test_rescue.md`
- [x] 1.2 Spec + `goal_assertions` schema → `docs/specs/wedge_v1_test_rescue_spec.md`
- [x] 1.3 Product one-pager → `docs/product/wedge_v1_coffee_money.md`
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

### Phase 4 — First payer (Cursor substitution)

- [ ] 4.1 CI Rescue Sprint corpus (10+ failures, real repo) — not `payment_utils` scale
- [ ] 4.2 PEI proof: AWOS ≤50% tokens vs raw API on same sprint goal
- [ ] 4.3 Find 1 beta user: would pay ₹2k **instead of** Cursor for this job class

---

## Live proof checklist

- [x] Live proof: run `./scripts/demo_wedge_v1_test_rescue.sh` — watch for `WEDGE_ASSERT: SEMANTIC_PASS`

---

## Session log

| Date | Done | Next |
|------|------|------|
| 2026-06-25 | Research, spec, product doc, payment_utils fixture, assert script, demo script, unit tests, live proof demo | API live proof on `awos worker`; first beta user |

---

## Related

- [`docs/product/stage1_subscription_worker_guideline.md`](../docs/product/stage1_subscription_worker_guideline.md) — full subscription bar (later)
- [`scripts/benchmark_vs_raw_api.py`](../scripts/benchmark_vs_raw_api.py) — PEI cases overlap corpus
