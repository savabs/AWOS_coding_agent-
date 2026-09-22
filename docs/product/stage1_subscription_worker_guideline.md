# Stage 1 Subscription Worker — Product Guideline

> **Identity:** [`VISION.md`](../../VISION.md) (sole philosophy)  
> **Execution plan:** [`docs/MASTER_PLAN.md`](../MASTER_PLAN.md) → Stage 1 exit criteria  
> **Facts:** [`memories/repo/project_structure.md`](../../memories/repo/project_structure.md)  
> **Research:** [`docs/research/stage1_subscription_worker.md`](../research/stage1_subscription_worker.md)  
> **Spec:** [`docs/specs/stage1_subscription_worker_spec.md`](../specs/stage1_subscription_worker_spec.md)  
> **Task tracker:** [`tasks/active/stage1_subscription_worker.md`](../../tasks/active/stage1_subscription_worker.md)

**Updated:** 2026-06-21

---

## What we are building (v1 product)

A **subscription-worthy single worker** for coding repos:

| Pillar | User promise | Harness responsibility (hidden) |
|--------|--------------|----------------------------------|
| **Long** | Runs hours, many steps, pause/resume | `RuntimeSession`, checkpoints, worktree |
| **Solves problems** | Goal in → verified edits out | Planner/worker/verifier, replan, MCTS fallback |
| **Efficient** | More finished work per dollar vs raw API | Cheap-first routing, budget ledger, learning loop |
| **Simple** | One goal in, one status out | CLI/GUI; no model names, no gauntlet jargon |

**We sell:** reliable finished work in a sandbox.  
**We do not sell:** tokens, chat, or “smarter GPT.”

Models are fuel. The **harness** is the product ([`VISION.md`](../../VISION.md)).

---

## Who it is for (v1)

- Developers with **their own API key** (hybrid deployment per [`MASTER_PLAN.md`](../MASTER_PLAN.md))
- Real repos, real multi-step goals (afternoon-scale, not seconds)
- Willing to review a **worktree diff** before merge (auto-merge off by default)

**Not v1:** enterprises, no-code users, multi-agent swarms, custom LLM training.

---

## Subscription-worthy bar (measurable)

A stranger pays when we can show **on real repos**:

1. **Completion rate** — multi-step goals finish without decompose spirals  
2. **Wall time** — 30–120+ minutes with pause/resume  
3. **PEI** — verified tasks per dollar **beats** raw API on the same goals ([`reward_store.py`](../../scaffold/agent/reward_store.py))  
4. **Simple UX** — `awos worker start "<goal>"` + `awos worker status` (mission/gauntlet hidden from payers)

Until (1)–(3) are boringly repeatable, **do not sell**.

---

## What is proven vs open (2026-06-24)

| Proven (lab + sessions) | Open (blocks subscription) |
|-------------------------|----------------------------|
| ✅ Worktree + SymbolIndex (G1) | Multi-hour **completed** mission (only 165s proven) |
| ✅ Pause/resume (G6, mission `rs_181d75a7fd91`) | Real repo proof (only toy fixtures tested) |
| ✅ Verify-fail → replan (G7) | Planner collapses long prose goals (workaround: `plan_file`) |
| ✅ Typed worker errors (G3) | Billing / onboarding path |
| ✅ Stagnation breaker (safety for unattended) | |
| ✅ `awos worker` CLI (Phase C) | |
| ✅ PEI benchmark (100% vs 67% raw API) | |

---

## Build sequence (engineering)

```
Phase A — Reliable long runs     ✅ DONE (2026-06-23)
  Pre-planned mission tasks (no planner collapse)
  Stuck/decompose guardrails
  One long mission completes 8+ steps

Phase B — Proof pack             ✅ DONE (2026-06-23)
  scripts/benchmark_vs_raw_api.py (same goals, AWOS vs direct API)
  docs/product/pei_proof.md (numbers for sales)

Phase C — Product surface        ✅ DONE (2026-06-24)
  awos worker start|status|resume|diff|cancel
  Hide gauntlet from default UX

Phase D — Subscription           ← NEXT
  Multi-hour proof on real repos
  Monthly budget cap, Stripe — only after A+B+C repeat on 3+ repos
```

---

## What we stop doing

- Re-running gauntlet G1–G7 for morale (green enough)  
- Long missions without `plan_file` (planner will merge steps)  
- Framing as “Cursor killer” — frame as **worker that finishes jobs in a sandbox**  
- GUI / multi-app before Phase A completes  

---

## User-facing v1 flow (current)

```bash
# Start work
awos worker start "Fix auth module, add tests, keep main clean"

# Hours later, optional Ctrl+C → lunch
# (session auto-pauses on interrupt)

# Check status
awos worker status

# Resume
awos worker resume

# Review sandbox before merge
awos worker diff

# Merge manually (auto-merge off by default)
cd .awos/worktrees/rs_<id>
git diff main
# manual merge if satisfied
```

---

## Related commands

| Command | Purpose | Audience |
|---------|---------|----------|
| `awos worker start/status/resume/diff/cancel` | Primary interface | **Customers** |
| `awos mission start --long` | Lab testing with `plan_file` | Internal only |
| `awos sessions list` | Session internals | Power users |
| `awos gauntlet run G*` | Stress tests | Lab only |
