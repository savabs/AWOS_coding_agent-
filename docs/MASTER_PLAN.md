# AWOS Master Plan — Execution Summary

> **Philosophy owner:** [`VISION.md`](../VISION.md) v2.7 (permanent, sole identity)
> **Facts owner:** [`memories/repo/project_structure.md`](../memories/repo/project_structure.md)
> **Session handoff:** [`docs/memory/checkpoint_2026-06-16_master_plan.md`](memory/checkpoint_2026-06-16_master_plan.md)
>
> This file is the **readable plan**. It does not replace `VISION.md`.

---

## What we are building

**AWOS** = Autonomous Work Operating System — a **capability amplifier** that turns commodity AI compute into **persistent, verified workers**.

```
Input:  base model (API or local) + goal + budget
Output: persistent employee (verified work done)
Delta:  AWOS kernel + compute engine
```

- **Not:** a coding chatbot, LLM wrapper, or Cursor clone
- **Coding:** App #1 benchmark only
- **Moat:** intelligence infrastructure + bare-metal efficiency on owned hardware
- **Long-term:** proprietary cognition (search + RL + symbolic + our modules) — not just next-token LLM

**2-year mission:** Given any model and any compute budget → **maximum reliable work**.

**Metric:** Useful Work Output ÷ (Dollar · Second · Watt)

---

## Compute & deployment

| Deployment | AWOS controls hardware? | Stack |
|------------|-------------------------|-------|
| Local laptop | Yes | Full kernel + `compute/` |
| On-prem server | Yes | Full kernel + `compute/` |
| Rented GPU (RunPod, etc.) | Yes | Full kernel + `compute/` |
| API-only (unified key) | No — optimize around provider | Kernel only |
| **Hybrid** | Partial | **Default** |

**Model choice:** user-configurable (providers, keys, allowlist) **but cannot bypass** plan → verify → reward → learn loop.

---

## Language stack

| Layer | Language | Path |
|-------|----------|------|
| GPU/CPU inference | C++ + CUDA | `compute/` |
| Kernel orchestration | Python (now) | `scaffold/agent/` |
| Small ML (PRM, bandits) | Python (PyTorch/NumPy) | `scaffold/agent/` |
| Symbolic math | Python (SymPy/Z3) → C++ later | `cognition/symbolic/` |
| CLI | Python | `awos.py` |
| Web console | Python + vanilla JS | `gui/` |
| FFI | pybind11 | `compute/bindings/` |

**Not AWOS core:** `src/` (vendored Claude Code)

---

## Hybrid cognition (not LLM-only)

```
Propose (LLM) → Search (MCTS) → Verify (oracle) → Learn (RL/PRM/bandit)
```

| Module | Status | File |
|--------|--------|------|
| MCTS search | Built, not default path | `mcts_search.py` |
| Process RM | Built, dormant <500 traces | `process_reward_model.py` |
| Bandit routing | Working | `ml_router.py` |
| Reward store | Working | `reward_store.py` |
| Symbolic | Planned | `cognition/symbolic/` |
| AWOS-owned foundation | Long-term | `cognition/` |

---

## User access model

**Goals in. Work out. Surfaces are views.**

| Priority | Surface |
|----------|---------|
| Primary | `awos run <goal>`, HTTP API (Stage 7), MCP `awos_run` |
| Observe | Web run console, `awos stats`, `awos report` |
| Secondary | `awos chat` / plan mode |

---

## 7-stage roadmap (sequential discipline)

| Stage | Focus | Timeline | Status |
|-------|-------|----------|--------|
| **1** | One excellent worker — persistent, hours-long, learns | **Now** | IN PROGRESS |
| **2** | Cheap execution — cache, routing, `compute/` C++/CUDA | 0–12 mo | PARTIAL |
| **3** | Memory — Experience → Episode → Skill → Policy → Habit | 6–12 mo | PARTIAL |
| **4** | Reflection — every failure → lesson → policy | 6–12 mo | PARTIAL |
| **5** | Planner — hierarchical replan on verify fail | 6–18 mo | PARTIAL |
| **6** | Tool evolution — synthesize, benchmark, keep | 12–18 mo | PARTIAL |
| **7** | Enterprise — Slack, Jira, GitHub, continuous goals | 18–24 mo | PLANNED |

---

## Stage 1 exit criteria (current focus)

- [ ] Multi-hour goal with pause/resume (`RuntimeSession` v1)
- [ ] Isolated execution (git worktree wired to orchestrator)
- [ ] Resume from checkpoint (plan + episodic + wave progress)
- [ ] Verify-fail → replan (minimal graph mutation)
- [ ] Every run logs reward; learning state compounds
- [ ] Coding benchmark: more verified work per dollar vs raw API

---

## Next build priorities (engineering)

### P0 — Kernel runtime (Stage 1)

1. **Spec:** `docs/specs/runtime_session_spec.md` + task file
2. **`runtime_session.py`** — unified `.awos/sessions/rs_*.json`
3. **`virtual_execution_runtime.py`** — wire `WorktreeManager`
4. **CLI:** `awos sessions list|resume|pause|fork`
5. **Real agent cancel** — GUI Stop kills orchestrator (from prior checkpoint)

### P1 — Cognition on hot path

6. Wire **MCTS** into default `awos run` path
7. Collect traces → activate **PRM**
8. Fix **error pattern typing** (~94% UNKNOWN today)

### P2 — Compute (Stage 2, parallel)

9. `compute/` CMake + `awos hardware` profiler
10. ggml bridge — single local inference from Python
11. Shared KV cache POC

### P3 — Access

12. Unified `GoalService` API (CLI + GUI + MCP call one kernel)
13. GUI: runs console primary, chat secondary

---

## Explicitly deferred

- Frontier LLM pretraining from scratch
- Large-scale self-play / world simulation
- AGI, robotics, agent swarms
- Multi-agent before Stage 1 done
- Docker/microVM sandbox (worktree-first per plan)

**Not deferred:** MCTS, PRM, symbolic oracles, small specialist nets, `compute/` inference runtime

---

## Key files (read order for new chat)

```
1. VISION.md v2.7
2. docs/MASTER_PLAN.md (this file)
3. memories/repo/project_structure.md
4. docs/memory/checkpoint_2026-06-16_master_plan.md
5. AGENTS.md
6. docs/research/hybrid_cognition.md
7. docs/research/bare_metal_compute_engine.md
```

---

## Business (summary)

| Horizon | Revenue logic |
|---------|---------------|
| Now | Build — prove verified work per dollar |
| Stage 7 | Org pays for **employee** (on-prem license, managed deploy, efficiency delta) |
| Long | Kernel + compute + `.awos/` compounding = switching cost |

---

*Last updated: 2026-06-16 — aligned with VISION v2.7*
