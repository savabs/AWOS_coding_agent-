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
