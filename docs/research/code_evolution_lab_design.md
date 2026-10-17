# Code Evolution Lab — Design Spec

**Based on:** SWE-CI principles, TEBench patterns, regression testing best practices (2026 research)

## Core Insight (from SWE-CI)

**Good maintenance = passing today's tests + not breaking tomorrow's tests**

75% of agents break their own fixes over time. SWE-CI proves this with EvoScore (future-weighted metric) and Zero-Regression Rate.

---

## What Makes a Valid Multi-Commit Evolution Test

### From SWE-CI Research

1. **Test Gap as Driver**
   - Start from base commit with N passing tests
   - Each evolution step adds new tests (features) or changes behavior (refactors)
   - Agent must close test gap while maintaining existing green tests

2. **Regression Measurement**
   - **Zero-Regression Rate:** Did agent break ANY previously-passing test?
   - Most agents fail this — <25% achieve zero regressions across 20 iterations

3. **EvoScore (Future-Weighted)**
   - Later iterations weighted more heavily (γ ≥ 1)
   - Rewards clean design that makes future changes easier
   - Penalizes tech debt that accumulates

4. **Technical Debt Signals**
   - Brittle fixes that work now but break later
   - Hardcoded values instead of proper abstractions
   - Skipping proper validation/error handling

### From TEBench Research

Three test evolution patterns to include:

1. **Test-Breaking:** Production code changes → existing test fails to compile/run
2. **Test-Stale:** Production code changes → test passes but no longer validates correct behavior
3. **Test-Missing:** New feature added → need new test coverage

---

## Design: Code Evolution Lab

### Structure

```
tests/fixtures/wedge_v1/code_evolution/
├── commit_0_baseline/     # Starting point (all green)
├── commit_1_feature/      # Add feature → new tests fail
├── commit_2_refactor/     # Refactor → old tests break (regression trap)
├── commit_3_edge_case/    # Edge case → new test + potential to break old
├── commit_4_integration/  # Integration → combines earlier features
└── golden/                # Reference fixes for each commit
```

### Scenario: E-commerce Order Processing

**Domain:** Realistic enough to have dependencies, simple enough to debug.

#### Commit 0: Baseline (all green)
- `order_processor.py` — basic order validation
- `discount_calculator.py` — simple percentage discounts
- `inventory.py` — stock checking
- **Tests:** 12 passing (basic happy paths)

#### Commit 1: Add tiered discounts (Test-Missing)
- `discount_calculator.py` changes: add tier logic (bronze/silver/gold)
- **Test gap:** 3 new tests for tiered discounts **fail**
- **Regression trap:** If agent hardcodes tiers, breaks when commit_4 adds platinum tier

#### Commit 2: Refactor order validation (Test-Breaking)
- `order_processor.py` refactor: extract validation to separate functions
- **Test gap:** 2 old tests **break** (import paths changed)
- **Regression trap:** If agent doesn't update all call sites, commit_3 breaks

#### Commit 3: Add bulk order edge case (Test-Stale + Test-Missing)
- `order_processor.py`: bulk orders need different validation
- **Test gap:** 1 old test **passes but is stale** (doesn't check bulk logic), 2 new tests **fail**
- **Regression trap:** If agent doesn't validate inventory properly, breaks commit_4 integration

#### Commit 4: Integration — combine discounts + bulk (Compound Test)
- `order_processor.py` + `discount_calculator.py`: tiered discounts apply to bulk orders
- **Test gap:** 3 new integration tests **fail**
- **Regression trap:** If earlier fixes were brittle, **many old tests break here**

---

## Success Criteria

### Per-Commit Metrics
| Commit | Baseline Pass | New Tests | Regression Allowed? |
|--------|---------------|-----------|---------------------|
| 0      | 12/12         | 0         | N/A (baseline)      |
| 1      | 12/12         | 3 new     | **NO** — must keep 12 green |
| 2      | 12/12         | 2 fixed   | **NO** — must keep 12 + 3 green |
| 3      | 15/15         | 3 new     | **NO** — must keep 15 green |
| 4      | 18/18         | 3 new     | **NO** — must keep 18 green |
| **Final** | **21/21** | **-**     | **Zero regressions = success** |

### Head-to-Head Comparison

| Metric | Raw API | AWOS Harness |
|--------|---------|--------------|
| **Final tests passing** | ? / 21 | ? / 21 |
| **Zero-Regression Rate** | 0% or 100% (binary) | 0% or 100% (binary) |
| **Regressions introduced** | Count per commit | Count per commit |
| **Cost** | $ | $ |
| **Time** | seconds | seconds |

**Hypothesis:** Raw API introduces 1-3 regressions by commit_4. AWOS verify loop catches them.

---

## EvoScore Calculation (Simplified)

```python
def normalized_change(current_pass, baseline_pass, target_pass):
    if current_pass >= baseline_pass:
        return (current_pass - baseline_pass) / (target_pass - baseline_pass)
    else:
        # Regression penalty
        return (current_pass - baseline_pass) / baseline_pass

def evoscore(normalized_changes, gamma=1.5):
    """
    gamma > 1 weights later commits more heavily.
    Rewards sustained stability over quick-but-brittle fixes.
    """
    n = len(normalized_changes)
    weights = [(i+1)**gamma for i in range(n)]
    total_weight = sum(weights)
    return sum(nc * w for nc, w in zip(normalized_changes, weights)) / total_weight
```

**Example:**
- Commit 1: 15/15 pass → NC = 1.0
- Commit 2: 14/17 pass (1 regression) → NC = -0.07 (penalty)
- Commit 3: 17/20 pass (caught up) → NC = 0.56
- Commit 4: 21/21 pass → NC = 1.0

Raw API with regressions → EvoScore ~0.65
AWOS with no regressions → EvoScore ~0.92

---

## Implementation Plan

### Phase 1: Build Fixture (~1-2 hours)
1. Write 5 source modules (order_processor, discount_calculator, inventory, utils, config)
2. Write 21 tests total (progressive across commits)
3. Create 5 commit states with intentional evolution patterns
4. Write golden/ reference fixes

### Phase 2: Oracle & Metrics (~30 min)
1. Extend `wedge_v1_assert.py` to support multi-commit tracking
2. Add regression counter
3. Add EvoScore calculator

### Phase 3: Mission Config (~30 min)
1. Write `code_evolution.json` mission config
2. Write sequential plan (5 commit stages)
3. Wire into `awos.py` CLI

### Phase 4: Benchmark Script (~1 hour)
1. Extend `benchmark_ci_rescue_sprint.py` pattern
2. Add per-commit test running
3. Add regression tracking
4. Compare raw vs AWOS

### Phase 5: Run & Document (~1 hour)
1. Run full benchmark
2. Write proof doc
3. Update project_structure.md

---

## Why This Proves AWOS Value

1. **Harder than current tests:** Multi-commit dependencies, regression traps
2. **Measures right thing:** Zero-Regression Rate (what production cares about)
3. **Fast proof:** ~3-4 hours total, runs on laptop, costs <$0.10
4. **Based on 2026 research:** SWE-CI + TEBench principles, not made-up metrics
5. **Cursor substitution test:** If AWOS has zero regressions and raw has 2-3, that's the proof

---

## Next Steps

1. Build commit_0 baseline (12 tests, all green)
2. Build commit_1 feature layer (3 new tests)
3. Build commit_2 refactor layer (2 breaking tests)
4. Build commit_3 edge case layer (3 new tests)
5. Build commit_4 integration layer (3 new tests)
6. Write golden/ fixes
7. Wire mission config
8. Run benchmark
