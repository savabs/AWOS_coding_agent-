# Ablation C2: a fail-safe acceptance gate

Pre-registered 2026-10-08, before any run. Stage 1 (one excellent worker).
It follows ablation C (`docs/specs/ablation_acceptance.md`, rejected).

## Why

Ablation C showed that acceptance tests written from the issue do catch real
misses: targets went 11/14 → 12/14, with tabulate_176 and boltons_458 fixed.
But **wrong** tests caused damage. These are tests that fail on the start
state and also on a correct fix, so the must-fail-first filter cannot catch
them.

On two controls they rejected a correct one-shot edit. The agent then chased
them for up to 136 turns, stalled, and the task rollback discarded the
correct edit:

- tabulate_256 and sqlparse_867 both went from 2/2 to 0/2;
- billed $/run rose 215%.

The idea is sound; the gate was not fail-safe.

## Change (one feature: `AWOS_ACCEPTANCE=2`, default 0)

Generation and the must-fail-first filter are unchanged from C (`be152a4`).
Mode 2 adds three safety rules. They are tested as one package, the
fail-safe gate.

1. **Arbitration.** When the visible suite is green but some kept acceptance
   tests fail, one cheap utility call (reasoning off) judges each failing
   test. It gets the goal text, the test source, the failure output and the
   current diff, and answers one of two things:
   - **WRONG_TEST:** the test expects behaviour the issue does not require,
     or it contradicts the visible suite or the issue's examples. The test
     is dropped.
   - **CODE_INCOMPLETE:** the code misses a stated requirement. The test is
     kept.

   If no tests are left, the result is accepted.
2. **Bounded repair.** Only CODE_INCOMPLETE failures trigger repair, and they
   get **one** bounded attempt: an agent loop capped at 10 turns (env
   `AWOS_ACCEPTANCE_REPAIR_TURNS`) with the failing tests as feedback. It is
   never a full loop and never more than one acceptance-triggered resume per
   task.
3. **Never lose the green edit.** Before any acceptance-triggered repair,
   snapshot the workspace. It is visible-green at that point. If the repair
   does not end with the visible suite green **and** the acceptance tests
   passing, restore the snapshot and finish with the visible-green verdict
   (success, no rollback). An acceptance failure can only add bounded work;
   it can never turn a visible-green result into a failure or a rollback.

The rules apply the same way after a green one-shot and after a green
agent-loop "done".

## Design

- **Issues:** the same as C, so the results are comparable.
  - Targets: more-itertools_1304, parse_249, pyparsing_647, tabulate_176,
    toolz_634, toolz_635, boltons_458.
  - Controls: boltons_474, tabulate_256, jsonpointer_64, sqlparse_867. The
    last two collapsed under C.
- **Arms:** AWOS `off`, `AWOS_ACCEPTANCE=0` (a fresh control) versus
  `AWOS_ACCEPTANCE=2`. Same code, `--repeat 2`, interleaved per issue.
- **Primary metric:** solve rate on the 7 targets, scored per issue and
  paired.
- **Secondary metrics:**
  - billed and logged $/run over all 11 issues;
  - minutes;
  - the share of runs where the gate was active;
  - arbitration verdicts (WRONG_TEST / CODE_INCOMPLETE counts);
  - restores (a repair that failed, so the green edit was restored);
  - false alarms (a kept test failing on a run whose hidden tests passed).

## Decision rule (written before running)

**Adopt (default 2)** when all of these hold:

- the target solve rate is up, with at least 2 targets improved and at most
  1 worse;
- billed $/run over all 11 issues rises at most 50%;
- **no control issue drops from 2/2 to 0/2.**

Significance is not required at this n; a consistent direction is.

**Reject (default 0)** otherwise. Report the arbitration and restore counts
either way. A re-tune, such as different caps or a different arbitration
prompt, is a new pre-registered run.

## Result (2026-10-08): ADOPTED (default 2)

Runs 2026-10-08 10:32 to 15:45, K = 2, interleaved per issue. The run was
paused for about 2 h while the OpenRouter key was revoked. Pairs that failed
on the key were re-run, so none are counted. Report:
`docs/memory/runs/report_ablationC2_acceptance/report.md`.

| Issue | Control (acc 0) | Fail-safe gate (acc 2) |
|---|---|---|
| more-itertools_1304 | 0/2 · $0.002 | **2/2** · $0.005 |
| parse_249 | 2/2 · $0.016 | 2/2 · $0.014 |
| pyparsing_647 | 1/2 · $0.040 | 1/2 · $0.007 |
| tabulate_176 | 1/2 · $0.018 | **2/2** · $0.019 |
| toolz_634 | 0/2 · $0.032 | 0/2 · $0.019 |
| toolz_635 | 1/2 · $0.003 | 1/2 · $0.005 |
| boltons_458 | 2/2 · $0.002 | 2/2 · $0.007 |
| **Target** | **7/14** | **10/14** |
| boltons_474 (control) | 2/2 | 2/2 |
| tabulate_256 (control; v1: 0/2) | 2/2 | **2/2** |
| jsonpointer_64 (control) | 2/2 | 2/2 |
| sqlparse_867 (control; v1: 0/2) | 2/2 | **2/2** |

Dollar amounts are logged $ per run.

**Decision checks:**

- **Solve rate:** target +21 pp per issue, CI [0, +50], sign-flip p = 0.50.
  That is not significant, but the rule did not require significance.
- **Issues:** 2 improved, 0 worse.
- **Cost:** billed **−7%**, logged −29% over all 11 issues, within the +50%
  guardrail.
- **Controls:** all four stayed 2/2.

All checks pass, so the change is adopted. `AWOS_ACCEPTANCE` defaults to 2
outside pytest.

**What the gate did:**

- **Generation:** tests generated on 23 runs. The gate was inactive 5 times,
  including two collection errors in the generated file and generator calls
  around the key outage.
- **Arbitration:** 6 arbitrations of 13 failing tests.
  - 7 were CODE_INCOMPLETE, each followed by one repair of 6–8 turns, and all
    3 repairs passed. These were the more-itertools_1304, toolz_635 and
    boltons_458 runs; each was then solved.
  - 6 were WRONG_TEST, dropped with no extra work: tabulate_176 ×2 and
    tabulate_256, all solved.
- **Restores:** 0 restores were needed.
- **Why cost fell:** catching a miss early replaced some long agent loops,
  for example pyparsing_647 at $0.040 → $0.007 per run.

**Watch:**

- A generated file with a collection error turns the gate off for that task.
  It is harmless, but it is lost coverage.
- toolz_634 is unsolved in both arms, and also for Aider. It is likely
  beyond this model.
