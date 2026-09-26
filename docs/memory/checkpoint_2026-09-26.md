---
title: "Checkpoint 2026-09-26 — compounding proof, run 1 (partial); project on hiatus"
tags:
  - doc/checkpoint
  - status/paused
---

# Checkpoint 2026-09-26 — compounding proof (paused)

Branch `claude/bench-run` (worktree `.claude/worktrees/bench-run`), draft PR #1.
Owner put the project on **hiatus** on 2026-09-26 during run 1 of the series.

## Direction decided this session

- Stepped back from tuning 5 practice long tasks (benchmark-chasing).
- Moat, chosen by the owner: **(2) private, on your box** as the wedge +
  **(4) it gets better at your work** as the lock. Everything else (loop,
  sandbox, checker, queue) is copyable plumbing.
- Research found nothing learned reached the agent on the default path, and
  LinUCB routing trained/selected on mismatched arm ids.
- Spec: [`docs/specs/compounding_proof_spec.md`](../specs/compounding_proof_spec.md).

## Built (commit 4647572, pushed)

- Project notebook `scaffold/agent/project_notebook.py` (read into the
  agent-loop prompt once per goal; rewritten once per goal by one cheap call;
  `.awos/projects/<id>/notebook.md`; `AWOS_NOTEBOOK=0` disables).
- Routing fix (ladder-index arms, `ml_router.py` / `escalation_engine.py`).
- 12-job series `tests/job_series/ordertool` (12/12 validate).
- Runner `scripts/job_series.py` (off vs on, pinned Flash, private state per
  arm, interleaved, turns from spans, elapsed time per log line).
- Suite: no new failures vs baseline.

## Run 1 — partial (started 12:37 IST; jobs 1–6 both arms, job 7 off)

Raw lines: [`runs/job_series_20260926T123741_partial.txt`](runs/job_series_20260926T123741_partial.txt);
the learned notebook: [`runs/job_series_20260926T123741_notebook_on.md`](runs/job_series_20260926T123741_notebook_on.md).

| Job | off | on |
|---|---|---|
| 1 | ✅ 17 turns $0.014 | ✅ 13 turns $0.012 |
| 2 | ✅ 34 turns $0.028 | ✅ 32 turns $0.034 |
| 3 | ❌ 2/9, 49 turns $0.037 | ❌ 8/9, 40 turns $0.028 |
| 4 | ❌ 8/13, 56 turns $0.053 | ❌ 9/13, 78 turns $0.068 |
| 5 | ✅ 19 turns $0.014 | ✅ 7 turns $0.006 |
| 6 | ✅ 58 turns $0.062 | ✅ 32 turns $0.028 |
| 1–6 | 4/6, 233 turns, $0.207 | 4/6, 202 turns (−13%), $0.176 (−15%) |

**Suggestive, not shown** (one run; job 4 went the other way). Jobs 5–6 used
about half the turns and cost with the notebook. Jobs 7–12 are the real test.
If the background run finished, its full results are in
`.awos/job_series_20260926T123741.json` (not yet committed).

## Findings to act on

1. **The notebook records false success.** It only sees the visible tests, so
   job 3 ("success: 25 tests passed") went in as a win while hidden tests
   failed. Wrong beliefs can compound too. Fix: the notebook should record
   the goal-check verdict when there is one, or say "unverified".
2. **Mac sleep stalls runs.** On battery with the lid closed, macOS sleeps
   despite `caffeinate` (smoke jobs took ~32 min wall, ~2 min of work). Long
   runs need AC power + lid open, or an always-on box.
3. Pinning uses `escalation_engine.MODELS_UNAVAILABLE` in the runner's child;
   there is no first-class "pin the worker model" setting.

## Next single action (on return)

Finish run 1 (or rerun: `PYTHONUNBUFFERED=1 caffeinate -i -s .venv/bin/python
scripts/job_series.py run`, ~1.5 h, ~$0.5, on AC), then a second run, and judge
against the spec's criteria (jobs 7–12: on ≥ off solve rate and ≥20% lower
cost/turns, two runs agreeing). Then fix finding 1.

Key: OpenRouter, $5 limit, about $3.3 left at pause.
