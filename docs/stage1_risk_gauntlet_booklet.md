---
title: Stage 1 Risk Gauntlet Booklet
tags:
  - stage/1
  - topic/validation
  - topic/stress-test
  - extreme
---

# Stage 1 Risk Gauntlet Booklet

> **Permanent stress-test catalog.** Run these regularly — not just safe v1 docstring validation.
> Philosophy owner: [`VISION.md`](../VISION.md) · Facts: [`memories/repo/project_structure.md`](../memories/repo/project_structure.md)

**Runner:** `python3 scripts/stage1_gauntlet_runner.py list|run <id>|run-all`  
**CLI:** `python3 awos.py gauntlet list|run <id>`

---

## Why this exists

Safe validation proves plumbing. **Gauntlets prove the worker under stress:**

- Wrong planner paths
- Decomposition spirals
- Worktree isolation + sync
- Pause/resume mid-plan
- Worker failure → MCTS → traces
- Learning gate firing on messy runs

**Rule:** If a gauntlet hasn't been run in 7 days, Stage 1 confidence is stale.

---

## Cost discipline

All gauntlets default to:

```bash
AWOS_CHEAP_ONLY=true
AWOS_PREMIUM_BUDGET=0
AWOS_MONTHLY_BUDGET=20
```

Estimated batch (G1–G4): **<$0.05** unless noted.

---

## Scenarios (extreme → extreme)

| ID | Name | What it breaks | Pass criteria |
|----|------|----------------|---------------|
| **G1** | Worktree vision | SymbolIndex blind; untracked files missing | `[WORKTREE]` log; SymbolIndex >0 files; main repo untouched |
| **G2** | Pause mid-plan | Resume restarts from task 1 | Session `PAUSED` → `COMPLETED`; completed_ids skip task 1 on resume |
| **G3** | Failure hunt | Worker never fails → no MCTS | `.awos/mcts_traces.jsonl` gains ≥1 line (automated G3) |
| **G3L** | Failure hunt live | Worker fixes bug first try | `VERIFIED` + optional MCTS if worker exhausts |
| **G4** | Multi-file chaos | Planner wrong paths, decompose spiral | Session completes; integration reviewer run; sandbox exists |
| **G5** | Learning probation | Candidate accept/reject under load | `learning_state.json` verdict or probation tick |
| **G6** | SIGINT live | Real Ctrl+C during `awos run` | `rs_*` status `paused`; `awos sessions resume` works |

---

## G1 — Worktree vision

```bash
AWOS_USE_WORKTREE=true python3 awos.py run \
  "In scaffold/agent/virtual_execution_runtime.py add a one-line docstring to worktree_enabled"
```

**Check:** stdout has `SymbolIndex: N files` where N>0; main-file unchanged.

---

## G2 — Pause mid-plan (automated)

```bash
python3 scripts/stage1_gauntlet_runner.py run G2
```

3-task plan; pause after task 1; resume completes 2–3.

---

## G3 — Failure hunt (MCTS)

**Automated (proves trace wiring):**

```bash
python3 scripts/stage1_gauntlet_runner.py run G3
# or: awos gauntlet run G3
```

Forces worker failure → MCTS fallback → `.awos/mcts_traces.jsonl` gains ≥1 line.

**Live hunt (best-effort — worker may succeed first try):**

```bash
python3 scripts/stage1_gauntlet_runner.py run G3L
```

Fixes intentional bug in `tests/fixtures/gauntlet_broken_module.py`. MCTS only fires if worker exhausts attempts on medium+ complexity.

---

## G4 — Multi-file chaos (manual)

```bash
AWOS_USE_WORKTREE=true AWOS_E2E=1 python3 awos.py run \
  "Risky gauntlet: 3 tasks across awos.py and tests/ — implement _sandbox_summary and tests"
```

Expect planner path errors. Review worktree diff before any merge.

---

## G5 — Learning load

```bash
python3 awos.py validate run --count 5
python3 awos.py stats   # candidate probation / verdict
```

---

## G7 — Verify-fail → replan

**Automated:**

```bash
awos gauntlet run G7
# or: python3 scripts/stage1_gauntlet_runner.py run G7
```

**Check:** stdout contains `[REPLAN] Verify-fail`; revised task completes after fidelity rejection.

---

## G6 — SIGINT live

**Automated:**

```bash
awos gauntlet run G6
# or: python3 scripts/stage1_gauntlet_runner.py run G6
```

**Manual:**

1. `AWOS_RUNTIME_SESSION=true awos run "<3+ task goal>"`
2. `Ctrl+C` after task 1 completes
3. `awos sessions list` → `paused`
4. `awos sessions resume <rs_id>`

---

## Extreme limits checklist

Before claiming Stage 1 "excellent worker", all must be true at least once:

- [x] G1 worktree + SymbolIndex >0 (fix landed 2026-06-19; live: 80 files on `181d75a7fd91`)
- [x] G2 pause/resume 3-task (automated or G6 manual)
- [x] G3 MCTS trace (`run G3`) or live worker exhaust (`run G3L`)
- [x] G4 survived without main-repo corruption (mission tranches 1–2)
- [x] Error pattern saved with typed class (not UNKNOWN) — G3 `WORKER_FAIL` 2026-06-20
- [x] `awos sessions show` prints Sandbox line for worktree session

---

## History

| Date | Run | Notes |
|------|-----|-------|
| 2026-06-21 | G7 auto | Verify-fail (fidelity) → `[REPLAN]` → success |
| 2026-06-20 | G6 live | `rs_9b12efa06152` SIGINT pause → resume 3/3 |
| 2026-06-20 | G1 live | `rs_634ad875f7db` worktree + SymbolIndex 80 files |
| 2026-06-19 | Mission tranche 1 | `rs_77134f119597` 4/4 worktree-only edits |
| 2026-06-19 | G1 fix (code) | `should_index_py_file()` — worktree SymbolIndex 80 files |
| 2026-06-19 | G4 sign-off | Main vs worktree diff confirms isolation |
| 2026-06-18 | G6 live | SIGINT after task 1 → `rs_21209030e699` PAUSED → resume COMPLETED |
| 2026-06-18 | G2 auto | Pause mid 3-task plan → resume; `rs_cabb2e51ebee` COMPLETED |
| 2026-06-18 | G3 auto | Forced worker fail → MCTS trace logged |
| 2026-06-18 | G3L live | Worker fixed `broken_add` first try; $0.0026; no MCTS (expected) |
| 2026-06-18 | G4 live | Planner → `src/cli/main.py`; decompose spiral; v2 candidate |
| 2026-06-18 | G1 fix | SymbolIndex relative filter + worktree sync |

_Update this table after every gauntlet run._
