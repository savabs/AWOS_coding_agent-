---
title: Stage 1 Real Workload Mission — Spec
tags:
  - spec
  - stage/1
  - mission
---

# Stage 1 Real Workload Mission

**Focus:** Prove one excellent worker on a **real multi-step goal** in an isolated worktree, with optional pause/resume across wall-clock time.

**Config:** `docs/missions/stage1_real_workload.json`  
**Task tracker:** `tasks/active/stage1_real_workload_mission.md`

---

## Success definition

| Criterion | How to verify |
|-----------|----------------|
| Worktree isolation | `[WORKTREE]` in logs; `git status` on main clean |
| Multi-step completion | Session `completed`; 3+ tasks in progress |
| Pause/resume (optional) | `paused` → `awos sessions resume` → `completed` |
| SymbolIndex | Log line `SymbolIndex: N files` with N > 0 |
| Sandbox visible | `awos sessions show rs_*` prints worktree path |

---

## Execution

```bash
awos mission guide      # playbook
awos mission start      # launch (worktree + cheap-only)
# … Ctrl+C after task 2 when ready …
awos sessions resume rs_<id>
awos mission status     # active / paused sessions
```

---

## Out of scope

- Auto-merge to main (`AWOS_WORKTREE_AUTO_MERGE` stays off)
- Multi-agent / RL

## Done (2026-06-21)

- **Verify-fail → replan** — `[REPLAN]` after verifier fidelity fail; G7 gauntlet PASS

---

## Kernel dependency

`VirtualExecutionRuntime.enter()` must run **before** SymbolIndex + planner (wired in `orchestrator.py`).
