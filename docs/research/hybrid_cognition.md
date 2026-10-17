---
title: "Research: Hybrid Cognition Beyond LLM"
tags:
  - doc/research
  - topic/cognition
  - topic/rl
  - phase/1-2
---

# Research: Hybrid Cognition — Beyond Next-Token Prediction

> **Canonical identity:** [[VISION]] v2.6 — AWOS is not "just an LLM"

## Problem

LLMs predict likely text. They do **not** inherently know math, physics, or code correctness. Stacking API calls without search, symbolic proof, and RL-over-verified-actions plateaus quickly.

AWOS long-term goal: **proprietary cognition we own** — AlphaCode / AlphaEvolve style systems where **ground-truth oracles** drive learning.

## Architecture

```
Propose (LLM) → Search (MCTS/beam) → Verify (tests/symbolic/sim) → Learn (RL/PRM/bandit)
                     ↑_________________kernel owns this loop_________________↓
```

## Modules

| Module | Start when | Repo today |
|--------|------------|------------|
| Search / MCTS | **Now** | `mcts_search.py` |
| Process RM (small DL) | **Now** (needs traces) | `process_reward_model.py` |
| Bandit routing | **Now** | `ml_router.py`, `reward_store.py` |
| Symbolic math | Stage 2–3 | — (SymPy/Z3 integration TBD) |
| Specialist nets | Mid term | train on `.awos/` traces |
| AWOS foundation modules | Long term | `cognition/` |

## Alpha-style loop (coding app = perfect sandbox)

1. **State:** codebase + goal
2. **Actions:** patches, tool calls, plan branches
3. **Oracle:** pytest, compiler, AST, linter (deterministic)
4. **Search:** MCTS explores patch tree
5. **Learn:** PRM + routing weights from outcomes

Same pattern generalizes: physics sim, formal proof, optimization constraints.

## What "our own model" means

Not day one: train GPT-5. **Yes day one path:**

- Own **search policies** and **reward models**
- Own **symbolic + verifier** stack
- Own **specialist nets** (small, trained on AWOS episodes)
- Own **foundation modules** later — optimized for **verifiable work**, not chat

## Frontier models already have harness-level CoT

Opus-class models plan, critique, and tool-reason inside the trace. AWOS does **not** compete by stacking another planning harness on top.

| Layer | AWOS role |
|-------|-----------|
| Model CoT | Propose patches, plans, language |
| Kernel | **Verify** (oracle), **persist** (sessions, memory), **route** (economics), **learn** (policy updates) |

**Thin orchestration** on frontier tiers; **thick** verify + memory + learn always.

## Self-learning, self-modification, bounded recursion

In scope per [[VISION]] Layer 10–12. **Not** unbounded AGI recursion — everything test-gated with rollback.

| Mechanism | What evolves | Repo | Stage |
|-----------|----------------|------|-------|
| Bandit routing | Model/prompt selection | `ml_router.py` | Now |
| Prompt evolution (1A) | Worker instructions | `prompt_evolver.py` | On by default (`awos run`) |
| Tool synthesis (1B) | New Python tools | `live_tool_synth.py` | Opt-in |
| Scaffold patch (1C) | Kernel code | `scaffold_evolver.py` | After 1A proven |
| PRM / MCTS | Search policy | `process_reward_model.py`, `mcts_search.py` | MCTS on worker-fail path |
| RuntimeSession | Durable episodes for learning | `runtime_session.py` | Shipped |

Loop (non-negotiable): `plan → execute → verify → reward → learn`. Sessions (`rs_*.json`) ensure episodes survive pause/cancel so **no run is wasted** for the learn loop.

**Deferred:** frontier LLM pretraining, AGI, swarms, unchecked self-recursion.

## Next steps

1. Collect MCTS traces on real runs → train PRM at 500+ (`awos stats` shows count)
2. Prove M1 live: `awos validate run --count 10` then `awos stats` — check `learning_state.json`
3. Spec: `cognition/symbolic/` — SymPy step checker for math goals
4. All modules implement `CognitionProvider` — swappable under kernel

## Related

- [[VISION]] v2.6
- `docs/research/bare_metal_compute_engine.md`
- `scaffold/agent/mcts_search.py`
