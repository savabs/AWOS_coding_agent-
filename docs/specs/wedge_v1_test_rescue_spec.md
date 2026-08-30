# Spec — Wedge v1: Test Rescue Benchmark

**Research:** [`docs/research/wedge_v1_test_rescue.md`](../research/wedge_v1_test_rescue.md)  
**Benchmark doc:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)
**Task:** [`tasks/active/wedge_v1_test_rescue.md`](../../tasks/active/wedge_v1_test_rescue.md)

---

## Goal

Define a medium-scale CI repair benchmark that can honestly validate the coding agent’s planning, execution, verification, and failure-analysis ability.

---

## Benchmark definition

| Field | Value |
|-------|-------|
| **Name** | CI repair sprint benchmark |
| **Purpose** | Validate the coding agent on bounded, verifiable repair work |
| **Scale** | 10+ failures or equivalent medium-scale task load |
| **Oracle** | `pytest` + `goal_assertions` |
| **Identity** | Benchmark only — not the project identity |

---

## `goal_assertions` schema

Post-run checker: `scripts/wedge_v1_assert.py`

```json
{
  "goal_assertions": {
    "must_pass": [
      "tests/test_payment.py::test_discount_ten_percent",
      "tests/test_payment.py::test_tax_rounds_half_up"
    ],
    "must_still_pass": "python3 -m pytest tests/ -q --tb=no",
    "forbidden_paths": ["tests/", "conftest.py"],
    "max_wall_minutes": 120
  }
}
```

**Pass rule:** `semantic_pass = all(must_pass) ∧ must_still_pass ∧ ¬forbidden_edits`

---

## Corpus v1

Manifest: `tests/fixtures/wedge_v1/manifest.json`

| Case ID | Repo path | Reds | Purpose |
|---------|-----------|------|---------|
| `01_off_by_one` | `tests/fixtures/benchmark/01_off_by_one` | 1 | Easy baseline |
| `02_wrong_logic` | `tests/fixtures/benchmark/02_wrong_logic` | 2 | Medium |
| `03_simple_add` | `tests/fixtures/benchmark/03_simple_add` | 1 | Easy |
| `payment_utils` | `tests/fixtures/wedge_v1/payment_utils` | 3 | Assertion lab only |

---

## User flow

```bash
# 1. Run coding agent on benchmark repo/fixture
awos worker start --root /path/to/repo \
  "Fix failing tests listed in .awos/wedge_goal.json — do not edit tests/"

# 2. Assert benchmark criteria
python3 scripts/wedge_v1_assert.py \
  --root /path/to/repo/.awos/worktrees/rs_<id> \
  --assertions /path/to/repo/.awos/wedge_goal.json

# 3. Review and merge
awos worker diff
```

---

## Live proof

| Artifact | Path |
|----------|------|
| Demo script | `scripts/demo_wedge_v1_test_rescue.sh` |
| Proof guide | `docs/wedge_v1_test_rescue_proof.md` |

**Success markers:**
1. `WEDGE_ASSERT: must_pass OK (N/N)`
2. `WEDGE_ASSERT: must_still_pass OK`
3. `WEDGE_ASSERT: forbidden_paths clean`
4. `WEDGE_ASSERT: SEMANTIC_PASS`

---

## Exit criteria

- [ ] Corpus manifest + `payment_utils` fixture committed
- [ ] `wedge_v1_assert.py` passes on fixed golden copy
- [ ] Live proof demo run on buggy fixture with observed markers
- [ ] Expanded benchmark case beyond the tiny assertion lab
- [ ] Failure analysis from the run is useful for future self-learning work

---

## Out of scope

- billing
- pricing
- customer conversion claims
- non-Python repos
- claiming AWOS is a CI product
- claiming general self-learning based on this benchmark alone
