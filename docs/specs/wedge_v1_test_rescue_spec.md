# Spec — Wedge v1: Test Rescue

**Research:** [`docs/research/wedge_v1_test_rescue.md`](../research/wedge_v1_test_rescue.md)  
**Product:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)  
**Task:** [`tasks/active/wedge_v1_test_rescue.md`](../../tasks/active/wedge_v1_test_rescue.md)

**Updated:** 2026-06-25 (competitive floor: Cursor ~₹2k / ~400M tokens)

---

## 0. Competitive floor

Cursor-class IDE agents: **~₹2,000/month** for **~400M auto-mode tokens**. Micro-tasks (1–3 test fixes, docstrings) are **not a paid product** — absorbed in one prompt.

`payment_utils` (3 reds) = **assertion lab fixture** only. Product wedge = **CI Rescue Sprint** (see §1).

---

## 1. Wedge definition

| Field | Value |
|-------|-------|
| **Name** | CI Rescue Sprint (assertion layer: Test Rescue / `wedge_v1_assert.py`) |
| **Job** | Afternoon-scale: 10+ failures or 10+ steps → verified green CI, mergeable diff |
| **Buyer** | Solo dev / small team, pytest, own API key |
| **Price band** | **₹2,000/mo** (Cursor-comparable — must pass substitution test) |
| **Not required** | SWE-bench SOTA; beat Cursor on 5-minute fixes |

---

## 2. Calendar test (developer feels value)

A wedge run **passes** when the developer says yes to:

> "I'd pay ₹2,000/month for this instead of burning Cursor tokens on the same goal."

**Quantified bar (product — not lab fixture):**

| Criterion | Threshold |
|-----------|-----------|
| Scale | **10+** failing tests fixed **or** **10+** verified steps **or** **5+** files |
| Wall time | **30–120+** minutes unattended |
| PEI | ≤**50%** token cost vs raw API on same goal |
| Red tests / regression | All `must_pass` green; `must_still_pass` exit 0 |
| Cheating | No edits under `forbidden_paths` |
| Review | `awos worker diff` mergeable without rewrite |

---

## 3. `goal_assertions` schema (Outcome Judge v0)

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

| Field | Required | Meaning |
|-------|----------|---------|
| `must_pass` | yes | Test node IDs that must exit 0 after run |
| `must_still_pass` | yes | Shell command; full regression gate |
| `forbidden_paths` | no | Prefixes; any diff under these → fail |
| `max_wall_minutes` | no | Soft SLA for subscription bar |

**Pass rule:** `semantic_pass = all(must_pass) ∧ must_still_pass ∧ ¬forbidden_edits`

Learn/reward only when `semantic_pass ∧ mechanical_pass`.

---

## 4. Corpus v1

Manifest: `tests/fixtures/wedge_v1/manifest.json`

| Case ID | Repo path | Reds | Purpose |
|---------|-----------|------|---------|
| `01_off_by_one` | `tests/fixtures/benchmark/01_off_by_one` | 1 | Easy — PEI baseline |
| `02_wrong_logic` | `tests/fixtures/benchmark/02_wrong_logic` | 2 | Medium — harness wins vs raw |
| `03_simple_add` | `tests/fixtures/benchmark/03_simple_add` | 1 | Easy |
| `payment_utils` | `tests/fixtures/wedge_v1/payment_utils` | 3 | **Assertion demo only** — not product scale |

---

## 5. User flow (v1 beta)

```bash
# 1. Developer points at repo with known reds (or our demo fixture)
awos worker start --root /path/to/repo \
  "Fix failing tests listed in .awos/wedge_goal.json — do not edit tests/"

# 2. Walk away (optional pause on Ctrl+C)
awos worker status

# 3. After complete — assert wedge criteria
python3 scripts/wedge_v1_assert.py \
  --root /path/to/repo/.awos/worktrees/rs_<id> \
  --assertions /path/to/repo/.awos/wedge_goal.json

# 4. Review and merge
awos worker diff
```

Beta shortcut (demo fixture):

```bash
./scripts/demo_wedge_v1_test_rescue.sh
```

---

## 6. Mission JSON extension

Add optional `goal_assertions` to mission config (same schema as §3).

Example: `docs/missions/wedge_v1_payment_utils.json`

---

## 7. Live proof

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

## 8. Exit criteria (wedge v1 done)

- [ ] Corpus manifest + `payment_utils` fixture committed
- [ ] `wedge_v1_assert.py` passes on **fixed** golden copy of `payment_utils`
- [ ] Live proof demo run on **buggy** `payment_utils` via `awos worker` OR documented manual harness run with observed markers
- [ ] One-page product doc linked from `project_structure.md`
- [ ] **Stretch:** 1 beta user runs Test Rescue on their repo (even free)

---

## 9. Explicitly out of scope (wedge v1)

- Stripe / billing
- LLM Outcome Judge for prose goals
- Non-Python repos
- Editing tests to make them pass
- Claiming subscription-ready for all coding tasks

---

## 10. Stage 1 relationship

**CI Rescue Sprint** is the **revenue critical path** at Cursor price — afternoon batches with PEI + compounding.

`wedge_v1_assert.py` + `payment_utils` = honesty infrastructure for any batch size.

Full subscription bar (`stage1_subscription_worker_guideline.md`) aligns with this — not micro-tasks.
