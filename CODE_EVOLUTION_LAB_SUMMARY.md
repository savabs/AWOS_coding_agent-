# Code Evolution Lab — Complete Summary

Built: 2026-06-27

## What We Built

A **lightweight SWE-CI-style benchmark** to test whether agents can maintain code across **5 sequential commits** without breaking earlier tests.

Based on 2026 research: **75% of AI agents break working code during multi-commit evolution.**

---

## The Fixture

`tests/fixtures/wedge_v1/code_evolution/` — realistic e-commerce order system

### Evolution Path (12 → 23 tests)

| Commit | Feature | Tests | Baseline Green | New Failures | Key Bugs |
|--------|---------|-------|----------------|--------------|----------|
| **0** | Baseline | 12 | 12 | 0 | None (all correct) |
| **1** | Tiered discounts | 15 | 12 | 3 | Bronze threshold wrong, tier discount returns 0 |
| **2** | Refactor validation | 17 | 15 | 2 | Refactor dropped price < 0 guard |
| **3** | Bulk orders | 20 | 17 | 3 (+1 carry) | is_bulk per-item not total qty, no discount applied |
| **4** | Integration (tier+bulk) | 23 | 17 | 6 (+2 carry) | Doesn't combine discounts, accumulated bugs |

### Regression Traps

**Commit 2:** If agent doesn't restore price guard → old test breaks  
**Commit 3:** If bulk check is brittle → integration breaks  
**Commit 4:** If earlier fixes were hacky → 6 failures (accumulated + integration)

This mimics **real evolution** where shortcuts show up later.

---

## Research Foundation

### Bulk Order Edge Cases (2026 best practices)
- Per-item vs total quantity thresholds
- Margin-safe testing with realistic carts
- No price floor = unprofitable orders
- Discount stacking without priority = double-discounting

### Integration Testing Patterns
- Test pyramid: 70% unit, 20% contract/integration, 10% E2E
- Big bang integration as release-readiness gate
- Contract tests catch breaking changes pre-merge

### SWE-CI Principles
- **Zero-Regression Rate:** Did agent break ANY previously-passing test?
- **EvoScore (γ≥1):** Future-weighted metric rewards stability
- **Technical Debt:** Short-term fixes that accumulate over time
- Most agents: <25% zero-regression rate across 20 iterations

---

## Deliverables

### ✅ Completed

1. **5 commit stages** with intentional bugs and regression traps
2. **Golden fixes** for each commit (demonstrates correct path)
3. **Corpus tests** (`tests/test_code_evolution_corpus.py`) — 5 passing
4. **Smoke demo** (`scripts/demo_code_evolution_lab.sh`) — fixture verification
5. **Benchmark script** (`scripts/benchmark_code_evolution_lab.py`) — raw vs AWOS comparison
6. **Documentation:**
   - Design: `docs/research/code_evolution_lab_design.md`
   - Spec: `docs/specs/code_evolution_lab_spec.md`
   - Proof guide: `docs/code_evolution_lab_proof.md`
   - Task: `tasks/active/code_evolution_lab.md`

---

## How to Run

### Fixture smoke test (no API, <10 seconds)

```bash
./scripts/demo_code_evolution_lab.sh
pytest tests/test_code_evolution_corpus.py -q
```

**Expected:** All 5 commit stages verified (12 pass → 3 fail → 2 fail → 4 fail → 6 fail)

### Head-to-head benchmark (API calls, ~$0.10, 5-10 min)

```bash
python3 scripts/benchmark_code_evolution_lab.py
```

**Outputs:**
- Report: `docs/product/code_evolution_head_to_head.md`
- JSON: `.awos/benchmarks/code_evolution_head_to_head_*.json`

### Metrics

| Metric | Definition |
|--------|------------|
| **Zero-Regression Rate** | 1 if no previously-passing test ever fails; else 0 |
| **EvoScore** | Future-weighted normalized change (γ=1.5) |
| **Final Pass** | All 23 tests green at commit_4 |

---

## Why This Proves AWOS Value

### The Test

- **Raw API:** One-shot fix per commit, no verify loop
- **AWOS:** Verify loop catches regressions before moving forward

### The Bar

**SWE-CI finding:** 75% of agents break working code over time  
**Cursor bar:** ~₹2,000/month includes ~400M auto-mode tokens

### The Proof

If AWOS achieves **Zero-Regression Rate = 100%** and raw API introduces regressions → harness prevents production breakage raw doesn't catch.

**This is the Cursor substitution test** — would you pay for AWOS **instead of** burning Cursor tokens on this job class?

---

## Current Status

**Fixture:** Complete and verified  
**Benchmark script:** Built (currently uses golden-copy simulation)  
**Live API proof:** Not yet run with actual worker/verifier calls

### To Complete Full Proof

Option A: Wire actual agent calls in benchmark  
Option B: Run on real repo evolution (3-5 commits from clawcode/knowledge_hub history)  
Option C: Sufficient as synthetic proof of concept

---

## File Map

```
tests/fixtures/wedge_v1/code_evolution/
├── commit_0_baseline/       # 12/12 green
├── commit_1_tiers/          # 12 green, 3 red + golden/
├── commit_2_refactor/       # 15 green, 2 red + golden/
├── commit_3_bulk/           # 16 green, 4 red + golden/
├── commit_4_integration/    # 17 green, 6 red + golden/
└── README.md

scripts/
├── demo_code_evolution_lab.sh
└── benchmark_code_evolution_lab.py

docs/
├── research/code_evolution_lab_design.md
├── specs/code_evolution_lab_spec.md
└── code_evolution_lab_proof.md

tests/
└── test_code_evolution_corpus.py
```

---

## Key Decisions

1. **Synthetic over real:** Faster iteration, controlled regression traps
2. **5 commits not 71:** Laptop-friendly, <10 min run vs 48 hours
3. **Golden fixes:** Demonstrates correct path without needing perfect agent
4. **Accumulated bugs:** Commit 4 includes carry-forward from earlier — realistic

---

## Next Steps (Optional)

1. Run with actual agent calls (not golden-copy)
2. Compare multiple models (DeepSeek, Claude, GPT) on same evolution
3. Extend to 10 commits for harder test
4. Port to real repo: pick 3-5 commits from organic history
5. Publish as "AWOS vs Raw API on Multi-Commit Maintenance"
