# Research — CI Rescue Sprint Corpus

**Date:** 2026-06-25  
**Product:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)  
**Spec:** [`docs/specs/ci_rescue_sprint_corpus_spec.md`](../specs/ci_rescue_sprint_corpus_spec.md)

---

## Problem

Cursor ~₹2,000/mo ≈ 400M auto tokens absorbs micro-tasks. AWOS needs a **proved corpus** at afternoon scale:

- 15+ failures (we ship **28**)
- 5+ source files
- Regression greens mixed in
- pytest oracle + forbidden test edits
- Pre-planned mission (18 tasks) for deterministic harness runs

---

## Fixture design: `shopkit` mini commerce kernel

Synthetic Python package modeling auth, billing, inventory, shipping, promo — realistic multi-module CI failure pattern without external repo dependency.

| Module | Bugs | Failing tests |
|--------|------|---------------|
| `auth.py` | 4 | 4 |
| `billing.py` | 5 | 5 |
| `inventory.py` | 5 | 5 |
| `shipping.py` | 4 | 4 |
| `promo.py` | 4 | 4 |
| integration | cross-module | 6 |
| **Total** | **22 bug sites** | **28 red / 7 green / 35 total** |

`golden/` holds reference fixes for demo and regression without API calls.

---

## Why not a real repo first

| Approach | Pros | Cons |
|----------|------|------|
| Real repo reds | Maximum realism | Non-reproducible; hard to reset |
| **Synthetic corpus** | Deterministic; versioned; golden path | Must validate on real repo next |
| PEI benchmark cases | Already exists | 3 cases — too small |

**Decision:** Ship synthetic corpus v1; Phase 2 = same mission shape on external repo with organic CI reds.

---

## PEI pairing (Phase 2)

Run identical 18-task plan:

- **AWOS harness** (worktree, verify, replan)
- **Raw API** script with same plan as prompts

Target: AWOS ≤50% token cost at equal `SEMANTIC_PASS`.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Agent edits tests | `forbidden_paths` + assert gate |
| Task 18 too large | Split in v2 if worker struggles |
| Golden demo ≠ API proof | Separate live proof checklist item |
