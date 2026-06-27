---
title: Stage 1 Prove-It — Research
tags:
  - research
  - stage/1
  - topic/validation
  - topic/learning
  - topic/worktree
---

# Stage 1 Prove-It — Research

**Date:** 2026-06-17  
**Stage:** 1 (one excellent worker)  
**Philosophy:** [`VISION.md`](../../VISION.md) — verify → reward → learn; metric = work ÷ dollar.

---

## 1. Problem

RuntimeSession, MCTS hot-path, and M1 PromptEvolver are **coded** but only **partially proved** on live runs:

| Capability | Code status | Live proof |
|------------|-------------|------------|
| RuntimeSession (`rs_*`) | Shipped | 3 sessions, all 1-task validation |
| M1 learning + candidate v1 | Shipped | Evolved at session 3; **0/5** probation sessions |
| MCTS traces | Wired | No traces (all tasks passed first try) |
| Worktree sandbox | Wired | **Not tested** on real git repo |
| Multi-task pause/resume | Partial | Not tested |
| Error pattern signal | Weak | ~94% UNKNOWN historically |
| Cost discipline | Documented | `.env` has `AWOS_CHEAP_ONLY=true` |

Stage 1 exit criteria ([`MASTER_PLAN.md`](../MASTER_PLAN.md)) remain unchecked for: multi-hour persistence, isolated execution, full checkpoint resume, verify-fail replan, and benchmark vs raw API.

---

## 2. What we need to prove next (priority order)

### 2.1 M1 candidate probation (P0 — cheap, high signal)

**Mechanism** (`learning_state.py`, `prompt_evolver.py`):

- After evolution, `evolved_prompt.json` holds a **candidate** (v1 exists).
- Each completed run calls `record_session(..., used_candidate_prompt=has_active_candidate())`.
- After `AWOS_PROMPT_ACCEPT_WINDOW` (default 5) candidate sessions, `maybe_finalize_candidate()` compares KPI vs baseline:
  - Success rate must not drop > `AWOS_PROMPT_MIN_SUCCESS_DROP` (5%)
  - Cost per task must not rise > `AWOS_PROMPT_MAX_COST_INCREASE` (25%)
- Verdict → `commit_candidate()` or `rollback_candidate()`.

**Current state** (`.awos/learning_state.json`):

- `sessions_completed: 3`
- `active_version: 0` (baseline)
- `candidate.version: 1`, `sessions_evaluated: 0`, `accept_window: 5`
- All 3 recorded sessions have `used_candidate_prompt: false` (candidate started *at* session 3, so sessions 4–8 should use candidate)

**Test:** Run **5 more** orchestrator sessions. Expect `used_candidate_prompt: true` and eventual accept/reject.

**Cost:** ~$0.001–0.002/task × 5 ≈ **<$0.01** with cheap-only.

### 2.2 V1 validation queue exhausted

All 15 tasks in `docs/v1_validation_tasks.json` are `completed`. Cannot run `awos validate run` without new pending tasks.

**Options considered:**

| Option | Pros | Cons |
|--------|------|------|
| Re-queue v1-13..15 | No file change | Goals already satisfied; verifier may fail on duplicate docstrings |
| Add v1-16..20 on untouched modules | Safe, measurable, same harness | Need 5 new task definitions |
| Ad-hoc `awos run` loop | Flexible | No progress JSON tracking |

**Decision:** Add **v1-16..v1-20** targeting kernel modules not touched in v1 batch (`runtime_session`, `learning_policy`, `mcts_policy`, `worktree`, `token_tracker`).

### 2.3 Worktree sandbox (P0 — safety)

**Mechanism** (`virtual_execution_runtime.py`, `worktree.py`):

- Gate: `AWOS_USE_WORKTREE=true`
- On session enter: `WorktreeManager.create_worktree()` → `.awos/worktrees/<feature_id>/`
- Orchestrator prints `[WORKTREE] Isolated execution: <path>`
- Session `sandbox` fields persisted in `rs_*.json`
- On success without auto-merge: worktree preserved for review

**Unit tests:** `tests/test_virtual_execution_runtime.py` (mock/tmp git only).

**Gap:** No live run on AWOS repo itself.

**Test:** One `awos run` with `AWOS_USE_WORKTREE=true`, tiny docstring goal on `virtual_execution_runtime.py`. Assert:

1. `[WORKTREE]` log line
2. `.awos/worktrees/<id>/` exists
3. Session JSON has `sandbox.enabled: true`
4. Main repo file unchanged until manual merge (optional diff check)

### 2.4 Deferred in this batch (document, don't block)

| Item | Why defer |
|------|-----------|
| Multi-hour pause/resume | Needs multi-task goal + manual interrupt; separate session |
| MCTS trace collection | Requires intentional worker failure; burns tokens |
| Error pattern UNKNOWN fix | Engineering task, not prove-it run |
| AWOS vs raw API benchmark | Needs controlled experiment design |

---

## 3. Cost constraints

Per [`VISION.md`](../../VISION.md) and `.env.example`:

```bash
AWOS_CHEAP_ONLY=true
AWOS_PREMIUM_BUDGET=0
AWOS_MONTHLY_BUDGET=20
```

Escalation ladder (`escalation_engine.py`): Gemini / DeepSeek / GPT-4o-mini only. Opus banned. Sonnet blocked in cheap-only.

Validation runs call `apply_kernel_defaults()` — sessions + learning + MCTS on, but cost stays low if tasks succeed first try.

---

## 4. Success criteria (this batch)

| # | Criterion | Pass condition |
|---|-----------|----------------|
| A | M1 probation | 5 sessions with `used_candidate_prompt: true`; `maybe_finalize_candidate` returns accept or reject |
| B | Learning compounds | `sessions_completed >= 8`; `awos stats` shows candidate verdict or probation complete |
| C | Validation batch | 5/5 v1-16..20 succeed |
| D | Worktree live | Worktree path used; sandbox persisted; no silent fallback |
| E | Cost | Batch total <$0.05; per-task ~$0.002 |

---

## 5. Risks

| Risk | Mitigation |
|------|------------|
| Duplicate docstring goals fail verifier | New symbols on untouched files only |
| Worktree fails on dirty repo | Use tiny goal; check git status first |
| API outage | Validation records failure; retry |
| Candidate rejected | Still valid proof — gate works |

---

## 6. References

- `docs/MASTER_PLAN.md` — Stage 1 exit criteria
- `docs/specs/runtime_session_spec.md` — Phase 3 worktree
- `scaffold/agent/validation_runner.py` — harness
- `scaffold/agent/learning_state.py` — probation gate
- `.awos/learning_state.json` — live state
