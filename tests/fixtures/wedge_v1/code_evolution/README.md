# Code Evolution Lab — Fixture

Multi-commit maintenance test for AWOS. Based on SWE-CI research (2026).

**Design:** `docs/research/code_evolution_lab_design.md`  
**Proof:** `docs/code_evolution_lab_proof.md`  
**Benchmark:** `scripts/benchmark_code_evolution_lab.py`

---

## What This Tests

Agent maintains code across **5 sequential commits** (12 → 23 tests) without breaking earlier tests.

**Key metric:** Zero-Regression Rate (SWE-CI finding: 75% of agents break working code)

---

## Stages Built

| Commit | Pass | Fail | Baseline Green | Description | Bugs |
|--------|------|------|----------------|-------------|------|
| **0_baseline** | 12 | 0 | 12 | All correct | None |
| **1_tiers** | 12 | 3 | 12 | Customer tiers | Bronze threshold wrong, tier discount returns 0 |
| **2_refactor** | 15 | 2 | 15 | Validation split | Refactor dropped price < 0 guard → **regression trap** |
| **3_bulk** | 16 | 4 | 17 (2 carry) | Bulk orders | is_bulk per-item not total qty, no discount applied |
| **4_integration** | 17 | 6 | 17 (4 carry) | Tier + bulk combined | Doesn't combine discounts + accumulated bugs |

**Commit 4 is the trap:** If earlier fixes were brittle, integration breaks.

---

## Smoke Check

```bash
cd tests/fixtures/wedge_v1/code_evolution/commit_0_baseline
pytest tests/ -q --tb=no  # 12 passed

cd ../commit_1_tiers
pytest tests/ -q --tb=no  # 3 failed, 12 passed

cd ../commit_2_refactor
pytest tests/ -q --tb=no  # 2 failed, 15 passed

cd ../commit_3_bulk
pytest tests/ -q --tb=no  # 4 failed, 16 passed

cd ../commit_4_integration
pytest tests/ -q --tb=no  # 6 failed, 17 passed
```

Or run the full smoke test from root:

```bash
./scripts/demo_code_evolution_lab.sh
pytest tests/test_code_evolution_corpus.py -q
```

---

## Golden Fixes

Each commit (except baseline) has a `golden/` directory with corrected versions:

- **commit_1_tiers/golden/discount_calculator.py** — Fix tier logic
- **commit_2_refactor/golden/validation.py** — Restore price guard
- **commit_3_bulk/golden/validation.py, order_processor.py** — Fix bulk + price guard
- **commit_4_integration/golden/order_processor.py** — Combine discounts correctly

---

## File Structure

```
commit_N_name/
├── config.py              # Business rules
├── inventory.py           # Stock checks
├── discount_calculator.py # Discount logic
├── order_processor.py     # Order flow
├── validation.py          # (from commit_2)
├── conftest.py            # Test setup
├── wedge_goal.json        # Expected state
├── tests/
│   ├── test_*.py          # Tests for this stage
│   └── ...
└── golden/                # Correct versions
    └── *.py
```

---

## What Makes This Valid

Based on 2026 research:

1. **Test Gap as Driver:** Each commit adds features → new failures
2. **Regression Traps:** Integration breaks if earlier fixes were brittle
3. **Accumulated Bugs:** Commit 4 has 6 failures (not just 3 new)
4. **Zero-Regression Rate:** Did agent break ANY previously-passing test?
5. **EvoScore:** Future-weighted metric (γ=1.5) rewards stability

**This is the SWE-CI principle:** Coding is not a one-shot game, it's a maintenance loop.

---

## Status

**Built:** 2026-06-27  
**Verified:** All 5 stages smoke-tested + corpus tests pass  
**Next:** Benchmark with actual agent calls (raw vs AWOS)
