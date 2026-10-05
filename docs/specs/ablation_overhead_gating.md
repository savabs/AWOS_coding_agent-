# Ablation 2: skip the planner and reviewer on small jobs

Pre-registered on 2026-10-05, before the run. The protocol is
`docs/research/evaluation_first_principles_2026-10.md`.

## Why

Timing analysis over 48 pinned backupd jobs:

- **Planner:** one reasoning call, about 53 s per job. In 71 of 72 planned
  jobs, the plan was collapsed into one task anyway.
- **Integration reviewer:** one reasoning call, about 47 s per job. Its
  output is only advisory and nothing acts on it. In the unpinned baseline it
  hung two jobs for 9–13 minutes each.
- **Combined:** these two calls are about 31% of a job's wall time, and most
  of the roughly $0.006 fixed cost per job.

## The change (one ablation: fixed-overhead gating)

- **`AWOS_PLANNER=auto` (default).** Skip the planner and run the goal as a
  single task when pre-plan exploration hits at most `AWOS_PLANNER_MAX_FILES`
  (default 6) files.
- **`AWOS_INTEGRATION_REVIEW=auto` (default).** Review only when more than one
  task ran. Every review call is capped at `AWOS_REVIEW_TIMEOUT_S` (default
  60).

Everything else stays as in the adopted pinned configuration:
`AWOS_OPENROUTER_PROVIDER=deepinfra,gmicloud,novita,siliconflow` and
`AWOS_OPENROUTER_ALLOW_FALLBACKS=0`.

## Design

- **Series:** backupd (held-out), jobs 1–12, arms `off` and `on`, `--repeat 2`.
- **Comparison:** the pooled pinned passes without gating:
  - ablation 1, run 20261003T114614;
  - the repeat 1 confirmation pass, run 20261003T162359 (r1).

  Jobs are paired per (job, pass).
- **Extra check:** the three real-repo issues (cachetools #423, sqlparse
  #332, more-itertools #1284), AWOS off. This shows that the gating does not
  break real-code runs.

## Metrics and decision rule

1. **Primary:** mean wall time per job (`minutes`) falls by at least 20% in
   both arms. The test is a paired sign-flip permutation test with a
   bootstrap CI.
2. **Co-primary:** billed $ per job falls in both arms. A CI excluding 0 is
   not required, because the expected saving (about $0.002–0.004) is small
   next to the per-job noise.
3. **Mechanism:**
   - the planner is skipped on at least 10 of 12 jobs;
   - the reviewer is skipped on every single-task job;
   - no review call runs longer than 60 s.
4. **Guardrail:** the pooled solve rate is within 2 jobs per 12 of the pinned
   pool:
   - pinned pool: off 12/24, on 13/24;
   - minimum allowed: off ≥ 8/24, on ≥ 9/24.
5. **Health:** both runs are VALID.

**Adopt** if 1, 3, 4 and 5 hold.
