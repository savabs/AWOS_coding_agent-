# Code Evolution Lab — Task (completed baseline, pending full proof)

**Spec:** `docs/specs/code_evolution_lab_spec.md`  
**Design:** `docs/research/code_evolution_lab_design.md`  
**Proof:** `docs/code_evolution_lab_proof.md`

## Goal

SWE-CI-style lightweight benchmark: agent maintains code across commits without regressions.

## Status: READY FOR PROOF

### Completed ✅

- [x] Research SWE-CI / TEBench design requirements
- [x] **All 5 commit stages** built and verified:
  - `commit_0_baseline` — 12/12 green
  - `commit_1_tiers` — 12 green + 3 red (tier bugs)
  - `commit_2_refactor` — 15 green + 2 red (validation refactor dropped price guard)
  - `commit_3_bulk` — 16 green + 4 red (bulk logic bugs + carry-forward)
  - `commit_4_integration` — 17 green + 6 red (tier + bulk integration + accumulated bugs)
- [x] Golden fixes for each commit
- [x] Corpus tests (`tests/test_code_evolution_corpus.py`) — 5 passing
- [x] Smoke demo (`scripts/demo_code_evolution_lab.sh`)
- [x] Benchmark script (`scripts/benchmark_code_evolution_lab.py`)
- [x] Spec + proof guide docs

### To Complete Full Proof

- [ ] Wire actual agent calls in benchmark (currently uses golden-copy simulation)
- [ ] Or: run on real repo evolution (pick 3-5 commits from clawcode/knowledge_hub history)
- [ ] Live proof run with API → measure zero-regression rate difference
- [ ] Update `memories/repo/project_structure.md`

## Quick Verify

```bash
./scripts/demo_code_evolution_lab.sh
pytest tests/test_code_evolution_corpus.py -q
python3 scripts/benchmark_code_evolution_lab.py --dry-run
```

## Fixture Map

| Stage | Path | Pass / Fail | Baseline Green |
|-------|------|-------------|----------------|
| 0 baseline | `commit_0_baseline/` | 12 / 0 | 12 |
| 1 tiers | `commit_1_tiers/` | 12 / 3 | 12 |
| 2 refactor | `commit_2_refactor/` | 15 / 2 | 15 |
| 3 bulk | `commit_3_bulk/` | 16 / 4 | 17 (2 carry-forward) |
| 4 integration | `commit_4_integration/` | 17 / 6 | 17 (accumulated) |

**Key insight:** Commit 4 has 6 failures including accumulated bugs from earlier commits — realistic evolution where brittle fixes show up later.

## What Makes This a Valid Test

Based on 2026 research (SWE-CI, TEBench):

1. **Test Gap as Driver:** Each commit adds features/refactors → new test failures
2. **Regression Traps:** Integration (commit 4) breaks if earlier fixes were brittle
3. **Zero-Regression Rate:** Did agent break ANY previously-passing test? (SWE-CI metric — 75% of agents fail)
4. **EvoScore:** Future-weighted metric rewards clean design that eases later evolution

## Golden Fixes Summary

| Commit | Files | Bugs Fixed |
|--------|-------|------------|
| 1 | `discount_calculator.py` | Bronze threshold (0 not 100), return actual tier discount (not 0) |
| 2 | `validation.py` | Restore price < 0 guard |
| 3 | `validation.py`, `order_processor.py` | Restore price guard, fix is_bulk_order (total qty not per-item), apply bulk discount |
| 4 | `validation.py`, `order_processor.py` | All above + combine tier + bulk discounts correctly |
