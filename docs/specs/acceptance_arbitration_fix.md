# C2.1: acceptance arbitration fixes (confirmation run)

Pre-registered 2026-10-08, before any run. It follows ablation C2 (adopted)
and the diagnosis during ablation B on backupd.

## Why

On backupd the C2 arbiter labelled **every** failing acceptance test
WRONG_TEST, so the gate never repaired. Its replies parsed fine; the problems
were these:

1. **The arbiter saw the wrong evidence.** The node-id → test-function lookup
   failed, so it saw the whole test file cut to 2,500 chars. The `::` vs `.`
   key mismatch also meant it never saw the traceback.
2. **The prompt leaned towards WRONG_TEST.** It said "When unsure, answer
   WRONG_TEST".
3. **Fixtures were missing.** Acceptance tests ran outside `tests/`, so
   `tests/conftest.py` fixtures were missing. Every generated test errored
   and the gate went inactive (backupd jobs 4 and 6).

Example: on backupd job 2 the code called `format_size` without importing it.
CODE_INCOMPLETE was correct, and the hidden result was 0/8.

## Change (`2d73bdb`, not behind a flag)

- Correct test-function lookup and traceback lookup.
- The prompt rule: errors raised by project code (NameError, ImportError,
  AttributeError, TypeError, unhandled exceptions) on behaviour the issue
  requires are CODE_INCOMPLETE. WRONG_TEST is only for asserting an unstated
  exact format, name or internal mechanism, or a test that contradicts the
  visible suite or the issue's examples. The parse-failure fail-safe is
  unchanged.
- `tests/conftest.py` is copied next to the acceptance tests.
- The arbiter's reply is logged.

The safety rules from C2 (one bounded repair; restore the green edit if the
repair fails) are unchanged. The worst case is therefore extra repair cost,
never losing a correct edit.

## Design: a confirmation run against same-week controls

1. **backupd**, 12 jobs × 2 passes, `off` arm, with the store of past fixes
   at its default (set by the ablation B decision) and the fixed gate.
   - **Control:** ablation B's 2 passes in the same condition. That is the
     same day and the same code except this fix. If B is adopted, the control
     is B's experience-on passes; if B is rejected, its experience-off passes.
2. **The 7 C2 targets**, K = 2, with the fixed gate.
   - **Control:** C2's acc=2 arm (2026-10-08).

## Decision rule (written before running)

**Keep the fix** when all of these hold:

- the combined solve rate (backupd plus C2 targets) is not lower than the
  controls, allowing at most 1 run fewer;
- billed $/run rises at most 50% over the controls;
- on backupd, at least one arbitration labels a CODE_INCOMPLETE that then
  triggers a repair, or more generated suites stay active (fewer "gate
  inactive").

**Revert to the C2 code** if the solve rate drops by 2 or more runs or cost
rises more than 50%.

Because the controls come from earlier the same day rather than being
interleaved, this is a weaker design than an ablation. That is acceptable
here because two of the three changes are bug fixes.

## Amendment before running (2026-10-08): add the goal-preservation fix

A job-by-job diagnosis of the backupd drop (today's pass 1: 3/11, against 67%
earlier) cleared all recent commits:

- the context was identical to before;
- backupd has its own pytest config;
- there were no no-progress or wall-budget stops;
- the gate vetoed nothing.

Jobs J2 and J5 failed because the **planner's single-task rewrite replaced
the original goal**, inventing details and dropping requirements. That
behaviour predates the recent commits. J4 failed through model variance.

The confirmation run therefore tests **both** fixes together:

- the arbitration fix (`2d73bdb`);
- the goal fix: the worker always gets the original goal verbatim, with the
  planner text as a labelled note.

The design, controls and decision rule are unchanged, with one addition:
also report whether J2 and J5 recover. They were 4/4 solved before today.
Recorded before any run.

## Result (2026-10-09): KEPT

The run used `22c55e9` (arbitration fix plus goal fix) from 2026-10-08 22:01
to 2026-10-09 01:08, K = 2, with current defaults: the acceptance gate (mode
2) on, the experience store off and best-of-N off.

| Set | C2.1 | Same-day control |
|---|---|---|
| backupd (12 jobs × 2) | **15/24** | 10/24 (ablation B exp0 passes) |
| C2 targets (7 × 2) | **11/14** | 10/14 (C2 acc=2 arm) |
| **Combined** | **26/38** | **20/38** |

backupd per job, new vs control:

| Job | New | Control |
|---|---|---|
| J2 | 2/2 | 1/2 |
| J4 | 2/2 | 1/2 |
| J5 | 1/2 | 0/2 |
| J7 | 2/2 | 0/2 |
| J8 | **1/2** | 2/2 |
| J11 | 1/2 | 0/2 |

All other backupd jobs were equal. Among the targets, toolz_635 went from 1/2
to 2/2 and the rest were equal.

**Decision checks:**

- **Solve rate:** +6 runs. This check passes.
- **Cost:** billed +37%, logged +35% per run (limit +50%). This check passes.
- **The gate on backupd:** it did real work. It made 24 arbitrations, now
  with mixed verdicts, and triggered 11 bounded repairs: 5 passed, and 6
  failed with the green edit restored. Only 1 inactive gate out of 39
  generated suites. This check passes.

Both fixes are kept. J2 and J5, the planner-rewrite failures, recovered
(2/2 and 1/2), and J5's one solve came through a wrong CODE_INCOMPLETE call
that the restore rule absorbed.

**Watch:**

- **J8 regressed** (2/2 → 1/2). In the failing run, the arbiter dismissed 4
  failing tests as WRONG_TEST on a wrong fix.
- **Arbiter accuracy is still imperfect.** Some correct fixes still trigger
  repairs, which costs turns, and some wrong fixes still pass. Cost per run
  rose, mostly from repairs.
- **The controls are earlier same-day runs**, not interleaved, so this is
  weaker evidence than an ablation.
