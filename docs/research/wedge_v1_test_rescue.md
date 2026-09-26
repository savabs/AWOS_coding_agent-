# Research — Wedge v1: Test Rescue

**Date:** 2026-06-25  
**Product:** [`docs/product/wedge_v1_coffee_money.md`](../product/wedge_v1_coffee_money.md)  
**Guideline:** [`docs/product/stage1_subscription_worker_guideline.md`](../product/stage1_subscription_worker_guideline.md)

---

## Problem

AWOS has kernel plumbing, PEI wins on 3 toy bug cases, and clawcode docstring missions — but **no job a stranger would pay ₹2,000/month for today**.

**Competitive floor (2026-06-25):** Cursor ~₹2,000/mo includes ~400M auto-mode tokens. Fixing a few tests or adding docstrings is **not valuable enough to charge for** — one IDE prompt absorbs it. Earlier "coffee money" (₹500–₹2,000 for micro-tasks) framing is **withdrawn**.

Founders over-index on:

- Beating Claude Code / Cursor globally
- SWE-bench / gauntlet scores
- Small demos that look green but aren't substitution-worthy

Users pay when AWOS clears the **Cursor substitution test** — afternoon-scale verified work, better PEI, or compounding on their repo.

---

## Wedge choice (revised)

| Candidate job | vs Cursor ~400M tokens | Verifiable? | AWOS fit |
|---------------|------------------------|-------------|----------|
| Docstrings / README | Trivial — **reject** | Partial | Lab only |
| Fix 1–3 pytest tests | Trivial — **reject as product** | Yes | Assertion demo fixture only |
| **CI Rescue Sprint** (10–40 reds → green) | Non-trivial batch | Yes (pytest + suite) | Target wedge |
| Package hardening (5–15 files) | Multi-hour chat drain | Yes | Target wedge |
| Full feature from prose | Hard | Needs Outcome Judge | Later |

**Decision:** **Test Rescue** naming kept for **assertion infrastructure** (`wedge_v1_assert.py`, pytest oracle). **Product wedge** is **CI Rescue Sprint** — afternoon-scale, unattended, PEI-winning batch.

**One sentence (product):** *"My CI's been red for days; AWOS ran 90 minutes in a worktree and handed me a mergeable diff — cheaper in tokens than I burned in Cursor yesterday trying the same thing."*

---

## Who pays (v1)

- Solo Python dev or 2–5 person team with **pytest**
- Has **their own API key** (hybrid deployment)
- Pain: 1–3 hours debugging failures they keep postponing
- Willing to review worktree diff before merge

**Not v1:** enterprises, non-Python stacks, "build me a feature from a paragraph."

---

## Pricing hypothesis (revised)

| Tier | Price | Job |
|------|-------|-----|
| Solo | ₹2,000/mo | Cursor-comparable; **CI Rescue Sprints** + compounding `.awos/` |
| Team | ₹5,000+/mo | Multiple repos, shared learned state |

**No micro-task tier.** ₹500 for "a few test fixes" loses to Cursor's included token bucket.

**Break-even:** 5–10 hours/month on jobs chat didn't finish × opportunity cost >> ₹2,000.

---

## Why pytest is our Outcome Judge v0

Clawcode proved mechanical verifier ≠ goal met (README false-green).

For Test Rescue, **pytest is the semantic oracle**:

- `must_pass`: specific test node IDs that were red → must be green
- `must_still_pass`: full suite or regression subset
- `forbidden_paths`: agent must not edit tests to cheat

No LLM judge required for wedge v1. Outcome Judge (LLM assertions) is wedge v2 for prose goals.

---

## Existing assets

| Asset | Reuse |
|-------|-------|
| `tests/fixtures/benchmark/01–03` | Corpus cases (isolated bugs) |
| `scripts/benchmark_vs_raw_api.py` | AWOS vs raw on same cases |
| `awos worker start/status/resume/diff` | Customer surface |
| Worktree + RuntimeSession | Sandbox |
| `Verifier` + retry loop | Already wins `02_wrong_logic` |

---

## Risks

| Risk | Mitigation |
|------|------------|
| Agent edits tests to pass | `forbidden_paths: ["tests/"]` + held-out test list |
| Trivial bugs don't justify pay | v1 corpus includes `payment_utils` (3 reds, realistic) |
| False green on prose tasks | Wedge is **test-ID-bound**, not README-bound |
| "Beat Claude Code" scope creep | Wedge doc is the **revenue** critical path; SOTA is not |

---

## Next step

Spec: [`docs/specs/wedge_v1_test_rescue_spec.md`](../specs/wedge_v1_test_rescue_spec.md)  
Task: [`tasks/active/wedge_v1_test_rescue.md`](../../tasks/active/wedge_v1_test_rescue.md)
