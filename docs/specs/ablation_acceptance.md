# Ablation C: acceptance tests from the issue gate "done"

Pre-registered 2026-10-07, before any run. Stage 1 (one excellent worker).

## Why

In reliability run 2 and ablation 4, the largest group of AWOS failures with a
real edit was (C).

- **The symptom:** AWOS edited, its visible tests were green, and it stopped.
  Often this was the one-shot "solved in 1 call". But the hidden tests found a
  requirement that the issue states and the visible suite never checks.
- **Examples:**
  - more-itertools_1304: `ichunked(rows, 0)` must yield nothing on a nonempty
    input.
  - parse_249, pyparsing_647, tabulate_176.
  - toolz_634, toolz_635: Aider also misses toolz_634.
- **Count:** 9 of the 20 AWOS failures in run 2.

The issue text holds the evidence: a reproduction snippet and the expected
behaviour. AWOS never turns that text into a check.

## Change (one change, behind `AWOS_ACCEPTANCE`, default 0 until adopted)

1. **Generate.** Before any edit, one utility call reads the goal text, plus
   the relevant source signatures, and writes a small pytest file of
   acceptance tests. These come from the issue's stated behaviour and repro
   examples only: no hidden tests, no reference fix.
2. **Filter: must fail first.** Run the tests on the start state and keep only
   the tests that **fail** there. Tests that already pass prove nothing; tests
   that error at collection are dropped. If none survive, the gate is off for
   this task and behaviour is unchanged. This is the same rule the benchmark
   applies to hidden tests.
3. **Gate.** A one-shot "solved", or an agent-loop "done", counts only if the
   kept acceptance tests pass as well as the visible suite. On failure, the
   failing acceptance output goes to the agent loop as feedback, the same way
   visible test failures do.
4. **Clean up.** The tests live outside the agent's diff, so they are never
   part of the patch, and they are removed at the end. The model never sees
   hidden tests.

Cost: one generator call of about $0.002, plus test runs. Risk: a wrong
acceptance test, one that fails on start and also on a correct fix, could
push a correct solution into the agent loop, adding cost, or mislead the
agent. The must-fail-first filter limits this but cannot remove it.

**Implementation note (`be152a4`, recorded before the run).** If the
acceptance tests still fail after the last allowed agent retry, the task keeps
its visible-test verdict and its edits. There is no rollback, because a wrong
acceptance test must not throw away a correct patch. So the gate's power is
that it forces more work, not that it vetoes the result.

This does not change the measurement: hidden tests decide "solved" in both
arms. The tests are written to a hidden directory in the workspace only while
they run, then deleted, so they never reach the patch.

## Design

- **Target issues** (a (C) miss in run 2 or ablation 4): more-itertools_1304,
  parse_249, pyparsing_647, tabulate_176, toolz_634, toolz_635, boltons_458.
- **Controls** (solved in about 1 call in both runs): boltons_474,
  tabulate_256, jsonpointer_64, sqlparse_867.
- **Arms:** AWOS `off` with `AWOS_ACCEPTANCE=0` (control) versus
  `AWOS_ACCEPTANCE=1`. Same code and the same defaults, including ablation 4.
  `--repeat 2`, interleaved per issue.
- **Primary metric:** solve rate on the 7 target issues, scored per issue and
  paired.
- **Secondary metrics:**
  - billed and logged $ per run over all 11 issues;
  - minutes;
  - the share of tasks where the gate was active (at least one kept test);
  - how often a kept test failed on a run whose hidden tests passed (a false
    alarm).

## Decision rule (written before running)

**Adopt (default 1)** when all of these hold:

- the target solve rate is up, with at least 2 target issues improved and at
  most 1 worse;
- billed $ per run over all 11 issues rises at most 50%;
- no control issue drops from 2/2 to 0/2.

Significance is not required at this n; a consistent direction is.

**Reject (default 0)** otherwise. Report the false-alarm rate either way: a
high rate means the generator needs work, not that the idea is wrong. Tuning
the prompt after seeing the data needs a new pre-registered run.

## Result (2026-10-08): REJECTED (default stays 0)

Runs 2026-10-07 17:00 to 2026-10-08 01:39, K = 2, interleaved per issue. The
Mac slept through several gaps, and the clock-based timings exclude sleep.

| Issue | Control (acc 0) | Acceptance (acc 1) |
|---|---|---|
| more-itertools_1304 | 2/2 · $0.002 | 2/2 · $0.004 |
| parse_249 | 2/2 · $0.008 | 2/2 · $0.005 |
| pyparsing_647 | 2/2 · $0.014 | 2/2 · $0.011 |
| tabulate_176 | 1/2 · $0.014 | **2/2** · $0.035 |
| toolz_634 | 1/2 · $0.026 | 0/2 · $0.013 |
| toolz_635 | 2/2 · $0.003 | 2/2 · $0.003 |
| boltons_458 | 1/2 · $0.002 | **2/2** · $0.011 |
| **Target** | **11/14** | **12/14** |
| boltons_474 (control) | 2/2 | 2/2 |
| tabulate_256 (control) | 2/2 · $0.002 | **0/2** · $0.036 |
| jsonpointer_64 (control) | 2/2 | 2/2 |
| sqlparse_867 (control) | 2/2 · $0.002 | **0/2** · $0.142 |

Dollar amounts are logged $ per run.

**Decision checks:**

- **Solve rate:** target +7 pp, with 2 improved and 1 worse (p = 1.0). This
  check passes.
- **Cost:** billed $/run over all 11 issues rose **+215%** (limit +50%). This
  check fails.
- **Collapse:** **2 controls fell from 2/2 to 0/2.** This check fails.

The change is rejected.

**Why: wrong acceptance tests that the must-fail-first filter cannot catch.**
On both collapsed controls the sequence was the same:

1. The one-shot made the edit that solves the issue in one call without the
   gate.
2. The kept acceptance tests still failed: sqlparse 0/4 passed, tabulate 2/5.
   They were **wrong tests**. They fail on the start state **and** on a
   correct fix, so the must-fail-first filter cannot tell them from real
   tests of new behaviour.
3. The agent chased them, up to 136 turns and $0.25 logged, made no progress,
   and was stopped (no_progress or retries).
4. The task was marked failed, and the full rollback **discarded the correct
   one-shot edit**, so the hidden tests saw the base.

So the "no rollback on a final acceptance failure" rule (`be152a4`) only
covered the green-loop path. An acceptance-triggered fallback that ends in
`no_progress` still rolls back.

The gate was active on 23 of 24 acceptance runs. One generator call timed
out after a wake from sleep, and the gate was inactive for that run.

**What a revised C would need.** Each item is its own pre-registered run.

1. **Never lose the one-shot edit.** If a fallback was triggered **only** by
   acceptance tests and the agent loop then fails, restore the post-one-shot
   state and its visible-green verdict, not a rollback to base. This alone
   would have kept both collapsed controls at 2/2.
2. **Bound the extra work.** An acceptance failure buys at most one repair
   attempt (a few turns), not a full agent loop.
3. **Arbitrate test validity.** When visible tests are green and acceptance
   fails, one cheap call decides whether the code or the test is wrong, and
   drops the tests it judges wrong.
