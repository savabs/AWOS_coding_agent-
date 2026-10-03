---
title: "Checkpoint: Vision v2.7 + Master Plan — 2026-06-16"
tags:
  - doc/checkpoint
  - phase/identity
  - phase/stage-1
date: 2026-06-16
immutable: true
---

# Session Checkpoint — 2026-06-16 (Vision & Master Plan)

> **Immutable record.** Identity lives in `VISION.md` v2.7. Facts in `memories/repo/project_structure.md`.
> **Prior checkpoint (GUI/M1):** `docs/memory/checkpoint_2026-06-16.md` — still valid for GUI/learning work.

---

## One-line status

**AWOS identity, business thesis, 7-stage roadmap, compute/access/cognition/language stack fully stamped in VISION v2.7 + `docs/MASTER_PLAN.md`. No new kernel code this session — strategic consolidation for handoff to new chat.**

---

## What happened this session

### Vision & identity (stamped permanent)

- Replaced v1 GTM/positioning with **Autonomous Work OS** / capability amplifier thesis
- Deleted conflicting docs: `NICHE_GTM.md`, `AGENCY_ONE_PAGER.md`, `V1_VALIDATION.md`, old blueprints, etc.
- Created `AGENTS.md` + `.cursor/rules/awos-vision.mdc` (always-on agent rules)

### Major doctrine additions (VISION v2.4 → v2.7)

| Topic | Decision |
|-------|----------|
| **Business** | Intelligence infrastructure; orgs pay for employees not prompts (Stage 7) |
| **Compute** | API + local both first-class; hybrid default |
| **Deployment** | Laptop, on-prem, rented GPU, API-only — all valid |
| **Model choice** | User-configurable within verify→reward→learn loop |
| **Access** | Goals-in/work-out; CLI primary; chat secondary |
| **Bare-metal** | `compute/` C++/CUDA; worktree sandbox (no Docker in 6mo) |
| **Cognition** | Not LLM-only; MCTS+PRM+symbolic; own modules long-term |
| **Languages** | Python kernel now; C++/CUDA compute; JS GUI observer only |
| **Deferred** | Frontier LLM pretraining, AGI, swarms — **not** MCTS/PRM/compute |

### Planning artifacts created

| File | Purpose |
|------|---------|
| `docs/MASTER_PLAN.md` | Consolidated execution plan |
| `docs/research/bare_metal_compute_engine.md` | Compute layer research |
| `docs/research/hybrid_cognition.md` | Beyond-LLM cognition research |
| `compute/README.md` | C++/CUDA scaffold |
| Kernel plan (Cursor plan) | RuntimeSession + VirtualExecutionRuntime design |

### Files updated

- `VISION.md` → v2.7
- `README.md`, `AWOS.md`, `AGENT_INDEX.md`
- `memories/repo/project_structure.md`
- `.github/copilot-instructions.md`, `.awos/README.md`
- `awos.py`, `gui_chat.py` (identity strings)

---

## Canonical read order (new chat — start here)

```
1. VISION.md v2.7                    ← sole philosophy
2. docs/MASTER_PLAN.md               ← execution plan + priorities
3. memories/repo/project_structure.md ← facts, phase, metrics
4. This checkpoint                   ← what we decided today
5. AGENTS.md                         ← agent rules
6. docs/memory/checkpoint_2026-06-16.md  ← GUI/M1 technical state (if doing GUI)
```

Estimated context time: ~10 minutes.

---

## State at session end

### Strategic

| Item | Status |
|------|--------|
| Product identity | **LOCKED** in VISION v2.7 |
| 2-year mission | Maximum reliable work per model/budget |
| Current stage | **Stage 1** — one excellent worker |
| Business model | Stage 7 enterprise; build first |

### Engineering (unchanged from prior session)

| Item | Status |
|------|--------|
| Agent kernel | 15/15 validation passed (prior session) |
| M1 PromptEvolver | Coded, not proven live |
| GUI | Operable; Stop doesn't kill orchestrator |
| RuntimeSession | **Not built** — top P0 |
| Worktree runtime | **Not wired** — top P0 |
| MCTS default path | **Not wired** — P1 |
| `compute/` | Scaffold only |

---

## Next session — recommended priorities

### If continuing **strategy/docs only**
- Spec: `docs/specs/runtime_session_spec.md` per MASTER_PLAN P0

### If continuing **engineering** (recommended)

**P0 — Stage 1 kernel runtime**
1. `runtime_session.py` + orchestrator checkpoints
2. `virtual_execution_runtime.py` + wire `WorktreeManager`
3. `awos sessions` CLI
4. Real agent cancel (from GUI checkpoint)

**P1 — Cognition on hot path**
5. MCTS default in `awos run`
6. Prove M1 PromptEvolver live (`AWOS_PROMPT_EVOLUTION=true`)
7. Fix error_patterns UNKNOWN typing

**P2 — Compute (Stage 2)**
8. `compute/` CMake + `awos hardware`
9. ggml bridge POC

---

## Decisions made (write to canonical files)

| Decision | Owner file |
|----------|------------|
| VISION v2.7 permanent identity | `VISION.md` |
| MASTER_PLAN as readable execution doc | `docs/MASTER_PLAN.md` |
| API + local hybrid; model choice within loop | `VISION.md` |
| C++/CUDA for compute; Python kernel for now | `VISION.md` Language Stack |
| Worktree not Docker for Stage 1 sandbox | MASTER_PLAN + kernel plan |
| Chat secondary, goals primary | `VISION.md` User Access Model |
| Proprietary cognition long-term; MCTS/PRM now | `VISION.md` + `hybrid_cognition.md` |

---

## What's NOT in scope yet

- HTTP enterprise API (Stage 7)
- `kernel/` directory migration from `scaffold/agent/`
- Foundation model training
- Multi-agent orchestration

---

## Quick commands

```bash
# Read plan
cat docs/MASTER_PLAN.md

# Run agent
python3 awos.py run --goal "your goal"

# Validation
python3 awos.py validate run --count 3

# GUI
python3 gui/server.py --port 8765

# Tests
python3 -m pytest tests/ -q --tb=no -x
```

---

## Related checkpoints

| File | Covers |
|------|--------|
| `checkpoint_2026-06-16_master_plan.md` | **This session** — vision, plan, handoff |
| `checkpoint_2026-06-16.md` | GUI hardening, M1 PromptEvolver, cancel gap |
| `checkpoint_gui_layer_2026-06-12.md` | GUI v0 origin |

---

*Session ended 2026-06-16. New chat: read VISION → MASTER_PLAN → project_structure → this file.*
