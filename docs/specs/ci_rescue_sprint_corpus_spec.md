# Spec — CI Rescue Sprint Corpus v1

**Research:** [`docs/research/ci_rescue_sprint_corpus.md`](../research/ci_rescue_sprint_corpus.md)  
**Task:** [`tasks/active/ci_rescue_sprint_corpus.md`](../../tasks/active/ci_rescue_sprint_corpus.md)  
**Product bar:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)

**Updated:** 2026-06-25

---

## 1. Corpus identity

| Field | Value |
|-------|-------|
| **ID** | `ci_rescue_sprint_v1` |
| **Path** | `tests/fixtures/wedge_v1/ci_rescue_sprint/` |
| **Mission** | `docs/missions/ci_rescue_sprint.json` |
| **Plan** | 18 pre-planned tasks → `ci_rescue_sprint.plan.json` |
| **Red / green / total** | 28 / 7 / 35 |
| **Modules** | 5 (`auth`, `billing`, `inventory`, `shipping`, `promo`) |

---

## 2. Cursor-class bar

| Criterion | Threshold |
|-----------|-----------|
| Failures before | **≥15** (actual: 28) |
| Source files | **≥5** |
| Steps | **18** pre-planned tasks |
| Wall time (live API) | **30–120 min** target |
| PEI (later) | ≤50% tokens vs raw API at same semantic pass |
| Price class | ₹2,000/mo substitution test |

---

## 3. Assertion gate

```bash
python3 scripts/wedge_v1_assert.py \
  --root <worktree> \
  --assertions tests/fixtures/wedge_v1/ci_rescue_sprint/wedge_goal.json
```

**Pass:** `WEDGE_ASSERT: SEMANTIC_PASS` with full suite green and no forbidden path edits.

---

## 4. Live proof

| Artifact | Path |
|----------|------|
| Corpus demo (no API) | `scripts/demo_ci_rescue_sprint.sh` |
| Proof guide | `docs/ci_rescue_sprint_proof.md` |
| Corpus unit test | `tests/test_ci_rescue_sprint_corpus.py` |

**Markers:**

1. `CI_RESCUE: red_count=28`
2. `CI_RESCUE: golden 35/35 pass`
3. `WEDGE_ASSERT: SEMANTIC_PASS`

---

## 5. Harness run (API)

```bash
# From AWOS repo root
python3 awos.py mission start --config docs/missions/ci_rescue_sprint.json
# After complete:
python3 scripts/wedge_v1_assert.py \
  --root <worktree from session> \
  --assertions tests/fixtures/wedge_v1/ci_rescue_sprint/wedge_goal.json
```

---

## 6. Exit criteria

- [x] Fixture + golden reference committed
- [x] 28 reds verified on buggy baseline
- [x] 35/35 pass on golden
- [x] Demo script run with observed markers
- [x] Live API mission complete with SEMANTIC_PASS
- [ ] PEI pair vs raw API (stretch)

---

## 7. File map

```
tests/fixtures/wedge_v1/ci_rescue_sprint/
  auth.py billing.py inventory.py shipping.py promo.py   # buggy
  golden/*.py                                             # reference fixes
  tests/test_*.py                                         # 35 tests (immutable)
  wedge_goal.json
  conftest.py
docs/missions/ci_rescue_sprint.json
docs/missions/ci_rescue_sprint.plan.json
```
