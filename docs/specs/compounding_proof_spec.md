---
title: "Spec: Prove Compounding (project notebook + job series)"
tags:
  - doc/spec
  - status/active
---

# Spec: Prove Compounding

> Identity: [`VISION.md`](../../VISION.md), "Models are replaceable. Intelligence
> compounds." Product shape: [`docs/product/agent_computer.md`](../product/agent_computer.md).
> Chosen 2026-09-26 by the owner: moat = **(2) private, on your box** as the
> wedge + **(4) it gets better at your work** as the lock. This spec proves (4).
> VISION stage: Stage 1 (one excellent worker that *learns*).

## Research findings (2026-09-26)

Nothing learned reaches the agent on the default `agent_loop` path:

- `_agent_loop_prompt` (`orchestrator.py`) uses only the task, its files, the
  previous task in the same run, and this goal's exploration. The system prompt
  is a fixed string; the tools are a fixed set.
- error_patterns are written (costing an LLM call) but only read by the old
  worker path. Skills, examples, evolved prompts, post-mortems: worker only.
- The one learned input, model routing, is broken: LinUCB trains arm
  `level.value` (1, 6) but selects by ladder index (0, 1). Flash outcomes
  credit Pro; Pro outcomes land on an arm never offered. Pro: 1 of 3084 episodes.
- All state lives in `./.awos` (cwd-relative), shared by every job and never
  reset, so experiments contaminate each other.

## What we build

### A. Project notebook (kernel): `scaffold/agent/project_notebook.py`

The per-project memory an employee builds up. Local only, never uploaded.

- **Where:** `.awos/projects/<project_id>/notebook.md` (cwd-relative, like all
  state). `project_id` = `AWOS_PROJECT_ID` if set, else the first 12 hex chars of
  sha256 of the resolved `codebase_root`.
- **Read:** `_agent_loop_prompt` appends, when the notebook exists and is
  non-empty, a section headed `What you learned about this project on earlier
  jobs (may be out of date; check before relying on it):` followed by the
  notebook, capped at 4000 chars.
- **Write:** once per goal, after the goal finishes (after the goal check, if
  any), one cheap LLM call (`goal_check_model()`-style resolution: env
  `AWOS_NOTEBOOK_MODEL`, default the worker's cheap model via OpenRouter)
  rewrites the notebook from: the old notebook, the goal text, the outcome
  (success, tests, goal-check verdict and its reason if any), and a compact
  action trace of every agent-loop task in the goal (tool name + short args +
  error/ok, test-command results; ≤ 6000 chars total).
- **Format** (the model must return the whole new notebook, ≤ 3500 chars, these
  sections only):
  - `## How to work here` — commands that worked (test command, entry points).
  - `## Layout and conventions` — where things live, project rules (error type,
    helpers to use, formats).
  - `## Pitfalls` — what went wrong on a past job and what fixed it.
  - `## Past jobs` — one line each: goal → outcome (keep the last 15).
- Only facts observed in the trace/outcome; no guesses. A failed or unparseable
  update keeps the old notebook. Update cost is recorded in the budget ledger
  (`request_type="notebook"`).
- **Switch:** `AWOS_NOTEBOOK` = `1` (default) | `0` (no read, no write).
- Stdout markers (live proof): `[notebook] read <n> chars from <path>` and
  `[notebook] updated <path> (<n> chars, $<cost>)`.

### B. Job series (corpus): `tests/job_series/ordertool/`

12 related-but-distinct user-shaped jobs on ONE project, in order.

```
tests/job_series/ordertool/
  base/                 # the project at the start (a git-able package + tests/)
  jobs/NN_<slug>/
    task.json           # {"id","goal","max_turns","max_cost_usd","timeout_min"}
    hidden_tests/       # test_*.py; copied into the project's tests/ to judge
    reference/          # files (paths relative to project root) that, copied
                        #   over the project, solve this job
  README.md
```

- `base/` = `tests/long_tasks/orders_export_filters` project with its reference
  solution applied (a working ordertool).
- Job N starts from base + references of jobs 1..N-1 (cumulative).
- Jobs deliberately re-use the project's conventions (its error type, money and
  date helpers, CSV writer, CLI registration, storage layout, test command) so
  that knowing the project saves work. Hidden tests check those conventions
  (e.g. raises the project's own error, uses its money format), not only the
  happy path.
- Mix: features, bug fixes, a refactor, a report, a CLI command; each solvable
  in ≤ 40 turns by a cheap model; goals written as a user would.
- Validation: for each job, on its start state the hidden tests FAIL (≥1) and
  the visible tests pass; with its reference applied, hidden + visible pass.

### C. Series runner: `scripts/job_series.py`

- `validate [--series ordertool]` — the check above, no LLM.
- `run [--series ordertool] [--arms off,on] [--jobs 1-12]`
  - Each arm gets its own empty state dir
    `.awos/job_series/<ts>/<arm>/state` used as the child's **cwd** (so every
    `.awos` store starts empty and is private to the arm); `.env` copied in.
  - Each arm gets one persistent project checkout (`.../<arm>/project`, git-inited)
    so `project_id` is stable across jobs.
  - Per job: set the project to the job's start state (base + references
    1..N-1, committed), run the orchestrator child exactly as
    `scripts/long_tasks.py` does (`AWOS_EXECUTOR=agent_loop`, `AWOS_SANDBOX=auto`,
    turn/cost caps from task.json), with `AWOS_NOTEBOOK=0|1` per arm,
    `AWOS_GOAL_CHECK=0` (hidden tests judge; saves ~$0.13/job), and the model
    pinned identically in both arms.
  - Judge: copy hidden tests in, run pytest `-rf`; record failing test names.
  - Metrics per job: solved, hidden pass/fail, failing test names, LLM calls
    (ledger entries), input/output tokens, cost_usd (from the arm's
    `.awos/budget.json`, notebook update included), minutes, notebook chars.
  - Backend preflight before each job (as in long_tasks.py).
  - Arms interleave job by job (off job1, on job1, off job2, …) so a key
    dying mid-run leaves a paired, comparable prefix.
  - Output `.awos/job_series_<ts>.json` + a printed paired table and summary:
    solve rate, mean cost, mean LLM calls per arm; the same for jobs 1–6 vs
    7–12 (the compounding curve).

### D. Routing fix (kernel, independent)

LinUCB must train and select on the same arm ids (ladder index), mask all arms,
and not re-replay reward_store episodes on every orchestrator start. Tests prove
a Flash success raises Flash's arm, not Pro's.

## Success criteria (live proof)

1. `validate` passes for all 12 jobs.
2. Series run, both arms, same model. **Compounding is shown** if the `on` arm,
   over jobs 7–12, has (a) solve rate ≥ `off`, and (b) mean cost or LLM calls
   ≥ 20% lower than `off`, while jobs 1–2 are about equal (nothing to learn yet).
3. If not shown, report that plainly with the per-job data — a negative result
   is a finding, not a failure to hide.

Single runs are noisy; a result is "suggestive" at 1 run and "shown" only when
repeated (≥ 2 runs agree).

## Not in scope

Cross-project/cross-box sharing (moat 5, later); skill library revival;
prompt evolution; the old worker path.
