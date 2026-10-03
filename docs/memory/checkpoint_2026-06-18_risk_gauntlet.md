# Checkpoint — Stage 1 Risk Gauntlet

**Date:** 2026-06-18

---

## Summary

Permanent **Risk Gauntlet** booklet + runner + `awos gauntlet` CLI. Automated G2 (pause/resume) and G3 (MCTS trace) passed. MCTS hot path wired to `mcts_policy.py` (default-on after worker failure).

---

## Deliverables

| Asset | Path |
|-------|------|
| Booklet (human) | `docs/stage1_risk_gauntlet_booklet.md` |
| Scenarios (machine) | `docs/gauntlet_scenarios.json` |
| Runner | `scripts/stage1_gauntlet_runner.py` |
| CLI | `awos gauntlet list` · `awos gauntlet run G2` |
| G3 fixtures | `tests/fixtures/gauntlet_broken_module.py` |

---

## Gauntlet results (2026-06-18)

| ID | Result | Notes |
|----|--------|-------|
| G2 | **PASS** | 3-task pause after task 1; resume completes 2–3 |
| G3 | **PASS** | Forced worker fail → MCTS trace in `.awos/mcts_traces.jsonl` |
| G6 | **PASS** | Live SIGINT → paused → `awos sessions resume` → completed |
| G3L | Ran | Live fix first try; no MCTS (expected when worker succeeds) |

---

## Kernel fixes (enabling gauntlets)

1. **MCTS policy** — `orchestrator.py` uses `should_run_mcts_fallback()` + `_log_mcts_trace()` (was gated on `AWOS_USE_MCTS` + high only).
2. **MCTSTraceStore** — alias added in `process_reward_model.py` with `count()` / `get_recent()`.
3. **RuntimeSession** — pause between tasks in `_run_task_batch`; checkpoint + `runtime_session_id` return.
4. **SIGINT → pause** — `awos run` / `awos sessions resume` install SIGINT handler when `AWOS_RUNTIME_SESSION=true`.
5. **E2E** — skip VectorMemory init when `AWOS_E2E=1` (fast gauntlet/tests).

---

## Run gauntlets

```bash
awos gauntlet list
awos gauntlet run G2    # pause/resume
awos gauntlet run G3    # MCTS trace wiring
python3 scripts/stage1_gauntlet_runner.py run-all   # automated batch (skips manual G4/G6)
```

**Rule:** If no gauntlet run in 7 days, Stage 1 confidence is stale.
