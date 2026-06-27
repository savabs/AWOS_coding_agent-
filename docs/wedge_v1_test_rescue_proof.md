# Wedge v1 Test Rescue — Live Proof

> **Spec:** [`docs/specs/wedge_v1_test_rescue_spec.md`](specs/wedge_v1_test_rescue_spec.md)  
> **Product:** [`docs/product/wedge_v1_coffee_money.md`](product/wedge_v1_coffee_money.md)

---

## Scenario

Developer has `payment_utils` with **3 red pytest tests** and **2 green regressions**. AWOS (or golden fix in demo) repairs `payment.py` without touching `tests/`.

---

## Command

```bash
chmod +x scripts/demo_wedge_v1_test_rescue.sh
./scripts/demo_wedge_v1_test_rescue.sh
```

No API key required — demo applies the golden fix to prove the assertion gate.

---

## Success markers

| Marker | Meaning |
|--------|---------|
| `WEDGE_ASSERT: must_pass OK (...)` | Each listed red test is green |
| `WEDGE_ASSERT: must_pass OK (3/3)` | All reds fixed |
| `WEDGE_ASSERT: must_still_pass OK` | Full `tests/` suite passes |
| `WEDGE_ASSERT: forbidden_paths clean` | No edits under `tests/` |
| `WEDGE_ASSERT: SEMANTIC_PASS` | Wedge run counts as valuable |

---

## Manual assert (any worktree)

```bash
python3 scripts/wedge_v1_assert.py \
  --root /path/to/worktree \
  --assertions tests/fixtures/wedge_v1/payment_utils/wedge_goal.json
```

---

## Next: live API proof

After `awos worker start` on a copied fixture:

1. Copy `payment_utils` to a temp repo with `wedge_goal.json`
2. `awos worker start --root <copy> "Fix failing tests; do not edit tests/"`
3. Run `wedge_v1_assert.py` on the worktree path
4. Checkpoint with session id + assert output

---

## Failure modes

| Symptom | Likely cause |
|---------|----------------|
| `must_pass FAIL` | Bug not fixed or wrong file edited |
| `must_still_pass FAIL` | Fix broke regression tests |
| `forbidden_paths VIOLATION` | Agent edited tests (cheating) |
| `SEMANTIC_FAIL` | Not subscription-worthy; do not learn from this run |
