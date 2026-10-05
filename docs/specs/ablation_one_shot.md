# Ablation 3: one shot first

Pre-registered on 2026-10-05, before the run. The protocol is
`docs/research/evaluation_first_principles_2026-10.md`.

## Why

On the same jobs and model, Aider solves about as many backupd jobs as AWOS
(5/12 vs 6–9/12, not significant). It costs about 1/5–1/10 as much and runs
in about 1/5 of the time.

**How Aider does it.** It preloads the relevant files, sends one or two calls
that return SEARCH/REPLACE edits, runs the tests and stops.

**How AWOS does it.** About 24 turns, each re-sending 7–10k tokens, to
explore, edit one hunk at a time and check.

Agentless and Aider both show that a fixed localize → repair → validate pass
solves many tasks cheaply.

## The change

`AWOS_ONE_SHOT=1` adds one call before the agent loop. That call gets:

- a repo map;
- whole relevant source files, plus the visible tests as read-only, up to
  `AWOS_ONE_SHOT_BUDGET_TOKENS` (24k);
- an instruction to return all edits as SEARCH/REPLACE blocks.

The edits are applied, then the tests run:

- **All tests pass** → the job is done in one call.
- **Otherwise** → the normal agent loop continues. It keeps the one-shot edits
  and is given the failing tests' output.

Everything else matches ablation 2 run 20261005T100157:

- pinned fp8 provider list;
- `AWOS_PLANNER=auto`;
- `AWOS_INTEGRATION_REVIEW=auto`.

## Design

- **Series:** backupd (held-out), jobs 1–12, arms `off` and `on`,
  `--repeat 2`.
- **Comparison:** ablation 2's gated passes, run 20261005T100157, paired per
  job and averaged over passes.
- **Also:** the three real-repo issues, AWOS off.
- **Reference:** Aider's backupd baseline in run 20261002T223614: 5/12
  solved, $0.0012 per job.

## Metrics and decision rule

1. **Primary:** billed $ per job falls by at least 30% in both arms, by a
   paired sign-flip test with a bootstrap CI.
2. **Co-primary:** minutes per job fall by at least 30% in both arms.
3. **Mechanism:**
   - One-shot solves at least 25% of jobs on its own, with no agent loop.
   - When it falls back, the loop starts from the kept edits. That is
     checked in the logs.
4. **Guardrail:** pooled solved within 2 per 12 of ablation 2's gated pool.
   - Ablation 2 pool: off 14/24, on 13/24.
   - Minimum: off ≥ 10/24, on ≥ 9/24.
   - The real issues must still be solved: at least 2 of 3.
5. **Health:** VALID.

**Adopt** (make `AWOS_ONE_SHOT` default on) if 1, 3, 4 and 5 hold.
