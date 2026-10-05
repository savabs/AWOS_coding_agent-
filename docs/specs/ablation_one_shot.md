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

## Result (scored 2026-10-05)

- **One-shot run:** backupd run 20261005T183332, 2 passes.
- **Comparison:** ablation 2 run 20261005T100157, paired per job.
- **Real issues:** runs 20261005T220016, 20261005T220135 and 20261005T223535.
- **Health:** all four runs were VALID.

| Criterion | off | on |
|---|---|---|
| 1. Billed $ per job falls ≥30% | $0.0109 → $0.0106, −3%, p = 0.90 ❌ | $0.0189 → $0.0084, **−56%**, CI [+0.0054, +0.0157], p = 0.003 ✅ |
| 2. Minutes per job fall ≥30% | 3.74 → 3.83, +2% ❌ | 4.59 → 3.35, −27%, p = 0.10 ❌ |
| Turns per job | 24 → 13, **−46%**, p = 0.007 | 25 → 8, **−66%**, p = 0.001 |
| 3. One-shot solves alone ≥25% | **10/24 (42%)** ✅ | **9/24 (38%)** ✅ |
| 4. Guardrail: solved | 14 → **17/24** ✅ | 13 → 13/24 ✅ |
| 4. Guardrail: real issues ≥2/3 | **3/3** ✅ (see below) | — |
| 5. VALID | ✅ | ✅ |

Real issues, in detail:

- **cachetools:** solved in one call, $0.0023, 15 s.
- **sqlparse:** solved through the fallback, 17 turns. The first attempt hung to the 30-minute timeout and was marked INVALID, then retried.
- **more-itertools:** solved through the fallback, 24 turns. None of the 11 one-shot blocks applied.

**Verdict: not adopted as configured.** Criterion 1 fails in the off arm, and
criterion 2 fails in both arms.

The mechanism works. About 40% of jobs finish in one call at Aider-level
cost, the turn count drops sharply, and correctness holds or improves. The
fallbacks are what erase the savings:

- **B: truncated replies.** The one-shot reply was cut off at the 16k output
  cap 5 times. Each time it retried once, then fell back. That wasted about
  3 minutes and two large calls per job.
- **Failed blocks.** On the larger jobs, one bad SEARCH block (or all of them
  on more-itertools, where `more.py` exceeds the 24k context budget) sends the
  job to the full agent loop. The cost of one-shot is then added to the cost
  of the loop.
- **C: stopping at visible tests.** One-shot ships as soon as the visible
  tests pass. It then missed hidden requirements: on j05/j06/j07/j11/j12, off
  j06/j12. These misses were more frequent in the on arm. Check whether
  notebook text reaches the one-shot prompt.
- **G: new.** sqlparse's first attempt hung until the job timeout. Find the
  cause in its log.

## Next

1. **Fix B.** Turn reasoning off for the one-shot call, and fall back after
   the first cut-off.
2. **Add a repair call.** Re-send only the failed blocks, with the exact
   current file text, before falling back.
3. **Give large files their relevant sections.** Use the grep-hit regions
   instead of skipping the whole file.
4. **Fix C.** Write acceptance tests from the task's stated requirements and
   gate "done" on them.

Then re-score against this run.
