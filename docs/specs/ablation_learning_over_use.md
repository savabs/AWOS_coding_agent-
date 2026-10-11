# Ablation: does AWOS get better with use? (Track C, learning over use)

Pre-registered 2026-10-11, before any model call of this experiment. Model:
direct Anthropic `claude-haiku-5-5` (no OpenRouter). Spend cap for the track: **$3**.

## Question

The moat claim is "gets better at your work". On a sequence of *related routine
tasks in one project*, do later tasks get cheaper, shorter or more reliable when
AWOS's persistent cross-job learning is ON versus OFF?

## What persists across jobs in AWOS today (audited 2026-10-11)

On the path job_series uses (`AWOS_EXECUTOR=agent_loop`):

| Store | Switch | Read on agent_loop path? | In this test |
|---|---|---|---|
| Project notebook (`.awos/projects/<id>/notebook.md`, one LLM rewrite per goal) | `AWOS_NOTEBOOK` | yes, every agent-loop prompt | ON in `learn`, OFF in `off` |
| Experience store (top-3 past verified diffs, BM25, `.awos/experience/<series>.jsonl`) | `AWOS_EXPERIENCE` | yes, one-shot context + first agent message | ON in `learn`, OFF in `off` |
| Compiled Tools / Routines (`scaffold/agent/compiled/`) | none | **no — not wired into the orchestrator** (its own docstring) | cannot be tested; reported as not wired |
| Skill library, example store, prompt evolver, strategy weights, LinUCB | always | legacy worker path only (not agent_loop); routing is pinned to one model | same in both arms (not a variable) |

So the only per-arm variable is `AWOS_NOTEBOOK=1 + AWOS_EXPERIENCE=1` (arm
`learn`, added to `scripts/job_series.py`) versus both 0 (arm `off`).

## Task set (synthetic routine workload; report as such)

Built by `scripts/routines/build_learning_series.py` from the R40 routine set
(`~/.awos-harness/routine_series`, read-only). The six R40 families whose four
instances share one base snapshot are each chained into one 4-job series in one
project (job N = instance N; job N starts from base + references 1..N-1):

- `learn_f01` run a pytest target + JSON report · `learn_f03` add a CLI flag ·
  `learn_f04` add a dataclass field · `learn_f07` rename a function ·
  `learn_f08` add a config env var · `learn_f09` write a unit test for a pure fn.
- Near-miss job 5 (look-alike task needing a different action) appended where
  the near-miss shares the base and stays valid when chained: `learn_f08`,
  `learn_f09`. (`r40_f07_nm_alias_bucket_of` is excluded: job 2's reference
  already renamed the function it asks to alias, so validate marks it INVALID.)
- `job_series validate` passes all 26 jobs (hidden tests fail on start, pass
  with reference, visible tests green both ways).

## Design

- Arms `off,learn`, interleaved job by job (job_series default), each arm with
  its own fresh state dir per repeat, so memory accumulates only within one
  pass over one family's series. Position 1 has empty memory in both arms
  (placebo position: any difference there is noise).
- `--repeat 3` planned: repeat 1 over all six series first, then repeats 2 and
  3. **Budget stop:** before each new (series, repeat) launch, if cumulative
  ledger spend of this track ≥ $2.50, stop; analyse what is complete (whole
  repeats of a series only).
- Per job: `max_turns` 40, `max_cost_usd` 0.25, 15 min (R40 task.json values).
- `AWOS_GOAL_CHECK=0` (job_series pin); hidden tests judge.
- A wiring pilot (arm `learn`, `learn_f04` jobs 1–2, one pass) runs first only
  to confirm the notebook update and experience record work on Haiku 5.5. It is
  **excluded** from the analysis. If the learning features are broken on Haiku,
  fix minimally, record the fix here, and re-pilot.

## Metrics

Unit = task = (series, position). Per task and arm, pool the valid runs (K ≤ 3).

- **Primary (efficiency):** billed-or-ledger `cost_usd` per job and `turns` per
  job, on the *learnable positions* 2–4 (18 tasks). `learn` cost includes its
  notebook update call (the cost of learning is charged to learning).
- **Secondary:** solve rate (hidden + visible tests); cost per solved task per
  position (curve, descriptive); minutes per job; near-miss jobs (position 5)
  reported separately; position-1 placebo difference.
- **Curves:** per position 1..4 (and 5 for near-miss), per arm: solve rate,
  mean cost, mean turns, cost per solved task, notebook chars.

## Decision rules (written before running)

1. **Solve rate** — repo stats rule (`docs/specs/stats_decision_rule.md`) via
   `scripts/eval_report.decision` on positions 2–4, base `off`, candidate
   `learn`, cost budget +50% → KEEP / REJECT / INCONCLUSIVE.
2. **Efficiency (primary)** — for each of cost and turns, per task
   d_t = log(mean_learn,t / mean_off,t) (tasks where both arms have ≥ 1 valid
   run and both means > 0). Report:
   - exact two-sided sign test on the d_t (b = tasks cheaper with learn,
     c = dearer);
   - Bayesian bootstrap (Dirichlet(1..1) over tasks, 20 000 draws, seed
     20261009) P(mean d < 0);
   - geometric-mean ratio exp(mean d) with 95% bootstrap interval.
   Label **LEARNING HELPS** iff P(mean d < 0) ≥ 0.95 **and** sign-test p ≤ 0.05
   **and** the solve-rate label is not REJECT. **LEARNING HURTS** iff
   P(mean d < 0) ≤ 0.05 and sign p ≤ 0.05. Otherwise **INCONCLUSIVE**.
3. **Learning-curve check** (descriptive, not a gate): the learn/off cost ratio
   at positions 2–4 versus at position 1 (difference-in-differences). A real
   learning effect should show a ratio near 1 at position 1 and below 1 later.
4. **Harm check**: any task where `off` solves all its runs and `learn` solves
   none is listed with its log (misleading memory), as in ablation B.

The overall claim "AWOS gets better with use (on routine work, Haiku 5.5)" is
supported only if rule 2 says LEARNING HELPS for cost or turns. Anything else is
reported as not shown, with the curves.

Analysis script: `scripts/routines/analyze_learning.py` (committed with this
spec, before running).

## Result

(filled in after the run)
