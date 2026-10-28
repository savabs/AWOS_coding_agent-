# Spec: verification-gate accuracy (false accept / false reject) on existing runs

Pre-registered 2026-10-11 (Track G of the parallel wave), before the analysis
script was written or run. Spend: $0. Analysis only, read-only on run data.

**Disclosure.** Before writing this spec I printed one joint count table from
`.awos/job_series_E1_combined.json` while checking the field layout
(`orchestrator_success` × `visible_tests_ok` × hidden pass). The definitions
below were not tuned to that table; they are the obvious ones for the fields
the harness writes (`scripts/job_series.py` `run_job`).

## Why

`docs/expedition/ATLAS.md` finding 1: the gate's false-accept rate is the most
important number AWOS has not measured. Hidden tests are the ground truth the
agent never sees, so every existing job_series run is a labelled gate example.

## Data (read-only)

- **Flash (E1):** `.awos/job_series_E1_combined.json` in the `bench-run`
  worktree: 137 runs, DeepSeek V4 Flash, arms `ctrl` and `treat` (E1 package),
  2026-10-09/10. These are the runs E1 was scored on.
- **Haiku 5.5:** `.awos/job_series_20261011T12*.json` and `…T13*.json`: 73
  runs, one per issue, 2026-10-11.
- Run logs: `<run_dir>/<arm>/run.log`, located from each row's
  `inputs.cmd[-1]` (the project path).
- Excluded: invalid rows, dry runs, and rows with no hidden test result.

## Definitions

- **Truth (hidden pass):** `hidden_failed == 0 and hidden_passed > 0`.
  (`solved` additionally requires visible green; it is not the truth label.)
- **Gates scored**, each a binary "accept":
  1. `declared`: `orchestrator_success is True` (what AWOS reported).
  2. `visible`: `visible_tests_ok` (the harness's post-run run of the repo's
     own visible tests).
  3. `own_tests`: visible green AND the acceptance gate did not end with a
     failing kept test it then kept (i.e. what AWOS's own visible +
     acceptance-generated tests said, after arbitration). An inactive
     acceptance gate counts as passing, which is how the code treats it.
  4. `strict_acceptance` (counterfactual): visible green AND the last logged
     `[ACCEPTANCE] check: K kept, P passed` has P == K (no arbitration
     override, no restore of a failing green edit). Only defined where the
     log shows an active gate.
- **False accept (FA):** gate accepts, hidden fails. **False reject (FR):**
  gate rejects, hidden passes.
- **Rates, each with a Wilson 95% interval:**
  - FA rate = FA / hidden-fail runs (the classical false-positive rate; what
    ImpossibleBench-style canaries would measure).
  - FDR = FA / accepted runs (share of "done" reports that are wrong; what the
    owner experiences).
  - FR rate = FR / hidden-pass runs.
- Reported per model, per arm (Flash), and overall. Runs of one issue are
  correlated (E1 has two arms per issue); a task-clustered count (issues with
  ≥ 1 FA) is reported next to the run counts.

## Failure-mode labels (from run.log, rule-based, first match wins)

For every FA and FR run: `acceptance_inactive`, `arbiter_overrode`
(arbitration labelled every failing kept test WRONG_TEST and the green result
stood), `repair_failed_restored`, `acceptance_passed_weak` (kept tests all
passed but hidden failed), `acceptance_none_kept`, `post_green_stop` (the
agent was stopped by the post-green idle rule), `no_log`, `other`. Secondary
flags (not exclusive): post-green stop, one-shot solved, no_progress stop,
rollback, loop-guard trip.

## Decision rules for recommendations (written before results)

- A gate change is **recommended now** if, on this data, it would remove ≥ 2
  false accepts while adding ≤ 1 false reject (counterfactuals computed from
  logs where possible).
- A change is **recommended for a pre-registered A/B** if it addresses ≥ 2
  false accepts but its false-reject cost cannot be estimated from logs.
- Ranking = (FAs addressed − FRs added) on the pooled data, ties broken by
  implementation cost.
- If FA rate (classical) has a Wilson upper bound > 10%, the ATLAS H1 premise
  ("current false-accept rate above 10%") is called **supported on organic
  failures**; if the upper bound is < 10%, **not supported**; otherwise
  **inconclusive**. (Canaries are still needed for an adversarial estimate.)
