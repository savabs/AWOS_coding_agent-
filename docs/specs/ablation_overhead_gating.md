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

## Result (scored 2026-10-05)

Runs compared:

- **Gated:** backupd run 20261005T100157, 2 passes.
- **Pinned pool, no gating:** run 20261003T114614 plus the r1 pass of run
  20261003T162359.

Jobs are paired per job, averaged over passes. The real-issue runs were
20261005T173405, 20261005T173622 and 20261005T174313. All runs were
**VALID**.

| Criterion | off | on |
|---|---|---|
| 1. Minutes per job fall ≥20% | 5.22 → 3.74, **−28%**, CI [+0.30, +2.85], p = 0.04 ✅ | 5.53 → 4.59, −17%, CI [−0.29, +2.48], p = 0.29 ❌ |
| 2. Billed $ per job falls | $0.0135 → $0.0109, −19%, p = 0.49 (ns) | $0.0152 → $0.0189, **+25%**, p = 0.06 ✗ |
| 3. Mechanism | **The planner was skipped on only 16/48 jobs (33%)** ❌. The reviewer was skipped on 47/48 single-task jobs ✅. No review call ran, so no timeouts. | (same) |
| 4. Guardrail: solved within 2 per 12 | 12/24 → **14/24** ✅ | 13/24 → 13/24 ✅ |
| 5. VALID | ✅ | ✅ |

Real issues (AWOS off) were all solved again:

- cachetools: 1.44 min (was 1.53).
- sqlparse: 6.15 min (was 6.98); the planner ran.
- more-itertools: 5.44 min (was 6.6).

**Verdict: not adopted as configured.** Criteria 1 (on arm) and 3 fail.

- **The reviewer gate works.** Keep it: it saves about 47 s per single-task
  job, and the deadline removes the hang risk.
- **The planner gate is mis-set.** Exploration hit counts include test files
  and keyword noise. On backupd they exceed 6 on about two-thirds of jobs, so
  the planner still ran, and that capped the time saved.
- **The on-arm cost rise is unexplained.** Turns rose 11%, and per-job billed
  figures carry billing-lag noise. Treat it as unresolved, not as a finding.
- **Next.** Gate the planner on a better signal, either non-test source files
  hit or `AWOS_PLANNER=never` for agent_loop. The evidence for the second
  option: 71 of 72 past plans collapsed to one task. Then re-score.
