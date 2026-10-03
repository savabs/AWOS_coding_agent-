# Code Evolution Lab — Quick Start

**Built:** 2026-06-27  
**Status:** Ready to run

---

## What Is This?

A **multi-commit maintenance test** that proves AWOS can maintain code across evolution without breaking earlier tests.

Based on **SWE-CI research**: 75% of agents break working code over time.

---

## Run It (30 seconds)

### 1. Verify fixture is built

```bash
./scripts/demo_code_evolution_lab.sh
```

**Expected output:**
```
[commit_0_baseline]  12 passed
[commit_1_tiers]     3 failed, 12 passed
[commit_2_refactor]  2 failed, 15 passed
[commit_3_bulk]      4 failed, 16 passed
[commit_4_integration] 6 failed, 17 passed

OK — all 5 commit stages built.
```

### 2. Run corpus tests

```bash
pytest tests/test_code_evolution_corpus.py -q
```

**Expected:** `5 passed in ~2s`

### 3. Run benchmark (optional, API calls)

```bash
python3 scripts/benchmark_code_evolution_lab.py
```

**Outputs:**
- `docs/product/code_evolution_head_to_head.md`
- `.awos/benchmarks/code_evolution_head_to_head_*.json`

---

## What It Tests

Agent fixes bugs across **5 sequential commits** (12 → 23 tests):

| Commit | Feature | New Tests | Trap |
|--------|---------|-----------|------|
| 0 | Baseline | 12 | None (all correct) |
| 1 | Customer tiers | +3 | Tier logic bugs |
| 2 | Refactor validation | +2 | **Regression:** dropped price guard |
| 3 | Bulk orders | +3 | Bulk logic bugs + carry-forward |
| 4 | Integration | +3 | **Accumulated bugs** + integration |

**Key:** Commit 4 has **6 failures** — if earlier fixes were brittle, they break here.

---

## Metrics

| Metric | Definition |
|--------|------------|
| **Zero-Regression Rate** | Did agent break ANY previously-passing test? |
| **EvoScore** | Future-weighted metric (γ=1.5) — rewards stability |
| **Final Pass** | All 23 tests green at commit 4 |

---

## Files You Need to Know

| File | What It Is |
|------|------------|
| `CODE_EVOLUTION_LAB_SUMMARY.md` | High-level overview |
| `WORK_SESSION_SUMMARY_2026-06-27.md` | What we built today |
| `docs/code_evolution_lab_proof.md` | How to run + interpret |
| `tests/fixtures/wedge_v1/code_evolution/` | The 5-commit fixture |
| `scripts/benchmark_code_evolution_lab.py` | Raw vs AWOS comparison |

---

## Why This Matters

**The Question:**  
Would you pay for AWOS to run multi-commit maintenance overnight instead of burning Cursor tokens?

**The Test:**  
If AWOS achieves **Zero-Regression Rate = 100%** and raw API introduces regressions → harness prevents production breakage raw doesn't catch.

**The Bar:**  
SWE-CI finding: **75% of agents break working code** during long-term evolution.

---

## Next Steps (Your Choice)

1. **Run smoke tests** (done above) ✅
2. **Read the summary** (`cat CODE_EVOLUTION_LAB_SUMMARY.md`)
3. **Run benchmark with API** (`python3 scripts/benchmark_code_evolution_lab.py`)
4. **Extend to 10 commits** (harder test)
5. **Port to real repo** (clawcode history)
6. **Use as wedge proof** (show to potential users)

---

## Status

- ✅ Fixture: 5 commits, 23 tests, regression traps
- ✅ Docs: Design, spec, proof guide, summary
- ✅ Scripts: Smoke test, benchmark, corpus tests
- ✅ Verified: All tests passing

**Ready for you to review and decide next steps.**
