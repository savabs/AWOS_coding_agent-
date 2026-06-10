# AWOS — Project Structure

> **Canonical owner for all project metrics and identity facts.**
> All other files must [[link]] here; they must not copy these values.
> Product identity: see [[VISION]].

---

## Identity

**AWOS** is a learnable operating system for autonomous work — a greedy meta-AI that optimizes **quality × speed ÷ cost** on every project.

**Mission:** Build infrastructure for autonomous agents that compile experience into policy, get cheaper over time, and serve niche customers (~1,000 paying users) as an agent-making firm.

**Strategic edge:** Self-learning kernel (PromptEvolver, LiveToolSynthesizer, LinUCB, ScaffoldEvolver) with local `.awos/` state that compounds. Not a Cursor competitor — a different category (learnable OS vs IDE plugin).

**Coding agent:** App #1 on the kernel. Kernel is portable to research, support, ops, and custom domain agents.

---

## Build Sequence

- Phase 0: Workflow backbone — DONE (2026-04)
- Phase 1: Vector memory + ReAct traces — DONE (2026-05)
- Phase 2: ML meta-controller (LinUCB) + escalation ladder — DONE (2026-05)
- Phase 3: Self-learning loop (PromptEvolver, ToolSynth, ScaffoldEvolver) — DONE (2026-05)
- Phase 4: Model ladder optimization + PEI scorecard — IN PROGRESS
- Phase 5: AWOS API productization — PLANNED
- Phase 6: Multi-app kernel (research, support, ops) — PLANNED

**Current phase:** 4 (model ladder cleanup, PEI metrics, API layer)

---

## Architecture

```
Kernel (domain-agnostic)          App #1: Coding (domain-specific)
─────────────────────────         ────────────────────────────────
orchestrator.py                   planner.py, worker.py, verifier.py
reward_store.py (objective)       git_manager.py, cartographer.py
ml_router.py (LinUCB)             test_runner.py
escalation_engine.py              symbol_index.py
budget_ledger.py
prompt_evolver.py
live_tool_synth.py
scaffold_evolver.py
vector_memory.py, skill_library.py
agent_state_manager.py
dag_executor.py
```

**Runtime state:** `.awos/` — the OS filesystem (error patterns, skills, evolved prompts, tools, rewards, goals, memory).

---

## Key Modules

| Module | Path | Purpose |
|---|---|---|
| CLI entry | `awos.py` | User-facing commands |
| Kernel loop | `scaffold/agent/orchestrator.py` | Plan → execute → verify → learn |
| Objective function | `scaffold/agent/reward_store.py` | `compute_reward()` — quality × speed ÷ cost |
| Bandit router | `scaffold/agent/ml_router.py` | LinUCB model selection |
| Model scheduler | `scaffold/agent/escalation_engine.py` | 5-tier ladder, start cheap escalate on evidence |
| Prompt compiler | `scaffold/agent/prompt_evolver.py` | Experience → evolved prompts |
| Tool compiler | `scaffold/agent/live_tool_synth.py` | Failures → synthesized helpers |
| Self-patcher | `scaffold/agent/scaffold_evolver.py` | Scaffold mutations (test-gated) |
| Coding execute | `scaffold/agent/worker.py` | LLM edit application |
| Coding verify | `scaffold/agent/verifier.py` | Syntax + apply gate |
| Identity doc | `VISION.md` | Canonical product vision |

---

## Execution Flow

```
Goal
  ↓
Orchestrator (kernel)
  ├── EscalationEngine / LinUCB → pick model (hidden from user)
  ├── Planner → task DAG
  ├── Worker → execute (Coding App)
  ├── Verifier → quality gate (Coding App)
  ├── compute_reward() → score outcome
  └── Learn → .awos/ (error patterns, skills, prompts, tools, weights)
```

---

## Current Metrics

| Metric | Value | Last updated |
|---|---|---|
| Test count | 776 passing, 1 flaky | 2026-06-08 |
| Kernel components | 15+ (reward, bandit, evolver, synth, etc.) | 2026-06-08 |
| Self-learning features | 3 active (1A, 1B, 1C) + LinUCB | 2026-06-08 |
| Current phase | 4 — model ladder + PEI | 2026-06-08 |
| Git status | 15 commits ahead of origin | 2026-06-08 |

---

## North Star Metric

**Project Efficiency Index (PEI)** = (Quality × Speed) / Cost

- Quality: test pass rate, success rate, rework cycles
- Speed: time from goal to done
- Cost: total LLM spend per project

Tracked via: `performance.json`, `spans.jsonl`, `reward_store.jsonl`, `awos stats`.

---

## Active Work

| Task | Focus | Status |
|---|---|---|
| PEI scorecard (`awos report`) | Client-facing proof | ✅ DONE — `reports/pei_report.html` |
| Agency one-pager | Sales doc | ✅ DONE — `docs/AGENCY_ONE_PAGER.md` |
| Model ladder refactor | Top-60 tiers | IN PROGRESS |
| Outreach (30 agencies) | First revenue | PENDING |
| Client pilot | PEI improvement proof | PENDING |

---

## Key Decisions

| Decision | Rationale | ADR |
|---|---|---|
| AWOS = learnable OS, not coding tool | Kernel portable; coding is App #1 | [[VISION]] v1.0 |
| Niche 1,000 users, agent-making firm | Mass market incompatible with per-deployment learning | [[VISION]] v1.0 |
| Hide model selection from customers | Routing is margin moat | [[VISION]] v1.0 |
| Greedy objective: quality × speed ÷ cost | Encoded in `compute_reward()` | `reward_store.py` |
| `.awos/` local state ownership | Compounding switching cost | [[VISION]] v1.0 |

---

## Cold-Start Protocol

```bash
# 1. Identity
cat VISION.md

# 2. Project facts (this file)
cat memories/repo/project_structure.md

# 3. Last session
ls -t docs/memory/checkpoint_*.md | head -1 | xargs cat

# 4. Active tasks
cat tasks/active/*.md

# 5. Operational protocols (when implementing)
cat AWOS.md
```

Do not re-read the entire codebase. Follow the links.
