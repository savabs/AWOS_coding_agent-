# Wedge v1 — Competitive Bar (not coffee money)

> **Philosophy:** [`VISION.md`](../../VISION.md)  
> **Spec:** [`docs/specs/wedge_v1_test_rescue_spec.md`](../specs/wedge_v1_test_rescue_spec.md)  
> **Facts:** [`memories/repo/project_structure.md`](../../memories/repo/project_structure.md)

**Updated:** 2026-06-27 (compounding advantage positioning)

---

## Competitive reality

**Cursor (and similar IDE agents)** charge roughly **₹2,000/month** for plans that include on the order of **~400M tokens** in auto/agent mode.

That means:

| Task | Cursor | AWOS implication |
|------|--------|------------------|
| Fix 1–3 failing tests | One auto prompt, seconds | **Not a product** — not worth charging for |
| Docstrings / README | Trivial in chat | **Lab only** (clawcode proved plumbing, not value) |
| Small refactor in one file | Cheap in auto mode | **Not differentiated** |
| Afternoon of multi-step verified work | Burns tokens; needs babysitting; no compounding | **Where AWOS must win** |

**Rule:** Do not price or position micro-tasks. The minimum paid job must clear the **Cursor substitution test**:

> *"Would I cancel Cursor and pay AWOS instead for this — not in addition, for this class of work?"*

---

## What we sell (revised minimum)

**Not:** fix a few red tests for ₹500/month.  
**Not:** "cheaper than hiring a dev for one hour."

**Yes:** **verified afternoon-scale work** the harness finishes while you do something else — with proof you can merge.

Examples of **minimum valuable** jobs:

| Job | Why it's not "small stuff" |
|-----|---------------------------|
| **CI Rescue Sprint** — full red CI job (10–40 failures) to green, suite intact | Hours of work; chat loses context; AWOS verify + replan loop |
| **Package hardening** — tests + fixes across 5–15 files in one module | Multi-step, multi-file; needs worktree + pause/resume |
| **Tech-debt block** — mypy/ruff clean one package + tests green | Bounded but large; unattended batch |
| **Repeat repo maintenance** — same class of job monthly, harness gets cheaper via `.awos/` | Compounding moat Cursor doesn't have |

`payment_utils` (3 tests) is an **assertion demo fixture** — not the product promise.

---

## Minimum capability bar (₹2,000/mo class)

| Must prove | Threshold |
|------------|-----------|
| **Scale** | 10+ verified steps **or** 10+ test failures fixed **or** 5+ files touched |
| **Wall time** | 30–120+ minutes unattended (pause/resume OK) |
| **PEI** | Same verified outcome at **≤50% token cost** vs raw API on identical goal |
| **Trust** | `semantic_pass` — not mechanical false-greens |
| **UX** | `awos worker start` → walk away → `diff` → merge |

| Explicitly not enough | Why |
|-----------------------|-----|
| 1–3 test fixes | Cursor auto does this free in the subscription |
| 40-second missions | Proves plumbing only |
| PEI on 3 toy bugs | Necessary lab; insufficient for pricing |

---

## Why pay AWOS at Cursor price?

Cursor sells **tokens + chat UX**. AWOS sells **finished verified work that gets cheaper every run**:

### Immediate advantage (month 1):
1. **Autonomy** — multi-hour worker, not 20 back-and-forth prompts  
2. **Cost** — DeepSeek-first routing (20× cheaper than Claude) → 50% API savings day 1  
3. **Quality** — verify loop catches errors before you see them  
4. **Sandbox** — worktree, review diff, no dirty main  

### Compounding advantage (month 3+):
5. **Learning** — `.awos/` learns *your* codebase patterns, error fixes, coding style  
6. **Cheaper over time** — Month 1: $150 API spend → Month 6: $45 (70% less)  
7. **Switching cost moat** — Leave AWOS = lose all that learning, start over  

**Key difference:**

| | Cursor | AWOS |
|---|---|---|
| **Job 1 cost** | $3 | $1.50 (DeepSeek routing) |
| **Job 10 cost** | $3 (same) | $1 (learning kicks in) |
| **Job 100 cost** | $3 (same) | $0.30 (repo expert) |
| **State** | Stateless | Learns every run |

If we only match Cursor on a 5-minute fix, we lose. We must win on **batch reliability × cost × compounding memory**.

---

## Pricing (aligned to market)

| Plan | Price | Must deliver |
|------|-------|--------------|
| **Solo** | ₹2,000/mo | Comparable to Cursor Pro — but for **unattended verified batches**, not chat |
| **Team** | ₹5,000–20,000/mo | Multiple repos; CI Rescue + compounding state |

No ₹500 "coffee money" tier for micro-tasks — that undercuts positioning and doesn't beat Cursor's included token bucket.

**User math (revised):** 5–10 engineer-hours/month reclaimed on jobs chat started but didn't finish × ₹500–1,000/hr >> ₹2,000.

---

## Pitch (30 seconds, revised)

> "Cursor: Same cost every job. $3 to fix a bug today, $3 next month, $3 forever. Stateless.
> 
> AWOS: Learns your codebase. Job 1: $3. Job 10: $1. Job 100: $0.30. Gets cheaper every run.
> 
> Why? DeepSeek routing (instant 50% savings), then PromptEvolver learns YOUR patterns, SkillLibrary caches YOUR solutions, fewer retries = lower cost.
> 
> First month: Save $100 vs Cursor. Third month: Save $300 (learning kicks in).
> 
> The more you use it, the cheaper it gets. Switch back to Cursor? Lose all that learning."
>
> **See:** [`AWOS_POSITIONING_COMPOUNDING.md`](AWOS_POSITIONING_COMPOUNDING.md) for full cost curves + moat analysis.

---

## Assertion infra (still valid)

`scripts/wedge_v1_assert.py` + pytest oracles remain the **honesty layer** for any batch job — not the size of the job itself.

Demo: `./scripts/demo_ci_rescue_sprint.sh` (28 reds → green).  
Proof: [`docs/ci_rescue_sprint_proof.md`](../ci_rescue_sprint_proof.md)  
Spec: [`docs/specs/ci_rescue_sprint_corpus_spec.md`](../specs/ci_rescue_sprint_corpus_spec.md)

---

## Roadmap

```
Prove CI Rescue Sprint on real repo (30–120 min, semantic_pass, PEI win)
  → 1 payer at ₹2k who'd otherwise burn Cursor tokens on the same goal
  → expand corpus + Outcome Judge for prose goals
  → compounding proof (run 2 cheaper/faster than run 1)
```
