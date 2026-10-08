# Ablation A: best-of-N first attempts

Pre-registered 2026-10-08, before any run. Stage 1 (one excellent worker).

## Why

On the 28 real issues (run 2, fair), AWOS has pass@1 of 64% but pass^2 of only
54%. Many issues are solved on one repeat and missed on the other. If either
of two runs counted, about 75% would be solved (2 × 0.643 − 0.536). The lever
is **variance, not ability**.

A one-shot first attempt costs about $0.002, so sampling several and keeping
the best is cheap. It needs no training and works with any model.

## Change (one change: `AWOS_BEST_OF_N`, default 1 = today's behaviour)

1. **Sample.** With N > 1, the one-shot phase makes N independent first-attempt
   calls in parallel, with the same context and sampling temperature 0.7 or
   the provider default. Each reply is applied to its own scratch copy of the
   start-state workspace, never to the real one.
2. **Score.** Run the visible suite on each candidate, with the same
   TestRunner and sandbox as today. Score by:
   - visible green;
   - then the number of acceptance tests passed, when `AWOS_ACCEPTANCE` is on
     and active;
   - then the fewest visible failures;
   - then the smallest diff.

   Hidden tests are never seen.
3. **Choose.** Apply the best candidate to the real workspace. If it is
   visible-green, the task proceeds exactly as a green one-shot does today,
   including any acceptance gate. If none is green, the best partial goes into
   the agent loop as today's failed one-shot does.
4. **Account.** All N calls and test runs are counted in cost and time. A
   malformed reply or a failed apply is just a losing candidate.

N = 3 for this ablation.

## Design

- **Targets:** the issues that flip-flopped (1/2 or 0/2) in run 2 or a later
  ablation control.
  - cachetools_405, parse_137, boltons_458, tabulate_176;
  - toolz_634, pyparsing_647, sqlparse_332, more-itertools_1304.
- **Controls** (stable 2/2): boltons_474, jsonpointer_64, tabulate_256,
  sqlparse_867.
- **Arms:** AWOS `off`, `AWOS_BEST_OF_N=1` (a fresh control) versus
  `AWOS_BEST_OF_N=3`. Same code and current defaults, with `AWOS_ACCEPTANCE`
  at its default (0). `--repeat 2`, interleaved per issue.
- **Primary metric:** solve rate on the 8 targets, scored per issue and
  paired.
- **Secondary metrics:**
  - billed and logged $/run over all 12 issues, and minutes;
  - how often the winning candidate was not candidate 1 (selection mattered);
  - how often 0 of 3 were green.

The targets were picked because they varied before. Regression to the mean
affects both arms equally, because the control is re-run fresh on the same
issues.

## Decision rule (written before running)

**Adopt (default 3)** when all of these hold:

- the target solve rate is up, with at least 2 targets improved and at most
  1 worse;
- billed $/run over all 12 issues rises at most 100%. The extra calls are the
  point, so the cap is wider than C's 50%; the real cost per solved issue must
  still beat or match the control's;
- no control drops from 2/2 to 0/2.

**Reject (default 1)** otherwise. A different N or scoring rule is a new
pre-registered run.

## Amendment before running (2026-10-08)

Ablation C2 was adopted after this spec was written, so `AWOS_ACCEPTANCE` now
defaults to 2. Both arms run on that new default: the fail-safe gate is on in
both, which measures best-of-N on top of AWOS as it now ships. Everything
else is unchanged. Recorded before any ablation A run.

## Result (2026-10-08): REJECTED (default stays 1)

Runs 2026-10-08 15:30 to 18:30, K = 2, interleaved per issue, with the
acceptance gate (C2) on in both arms. No invalid runs. Report:
`docs/memory/runs/report_ablationA_best_of_n/report.md`.

| Issue | Control (N=1) | Best-of-3 |
|---|---|---|
| cachetools_405 | 1/2 · $0.097 | **2/2** · $0.049 |
| parse_137 | 2/2 | 2/2 |
| boltons_458 | 2/2 | 2/2 |
| tabulate_176 | 2/2 | 2/2 |
| toolz_634 | 0/2 | 0/2 |
| pyparsing_647 | 2/2 | 2/2 |
| sqlparse_332 | 1/2 | 1/2 |
| more-itertools_1304 | 2/2 | 2/2 |
| **Target** | **12/16** | **13/16** |
| Controls (4) | 8/8 · $0.001–0.003/run | 8/8 · $0.003–0.013/run |

Dollar amounts are logged $ per run.

**Decision checks:**

- **Solve rate:** only **1** target improved; the rule needs 2. This check
  fails.
- **Collapse:** 0 issues worse, and the controls are intact.
- **Cost:** billed +13%, logged −7% over all 12 issues (limit +100%). This
  check passes.

The change is rejected. `AWOS_BEST_OF_N` stays 1 and the code stays behind
the flag.

**Why the gain was small:**

1. **The headroom was already taken.** The run 2 estimate (pass@1 64% →
   about 75% if either of two runs counted) predates ablation 4 and C2. With
   both adopted, the control solved 12/16 of these targets, and the C2 gate
   already catches many of the misses that sampling would.
2. **The ranking puts visible-green above acceptance.** The visible suite is
   often already green on the base, so a near-no-op candidate (a failed block,
   0/6 acceptance) can outrank candidates that implement the feature (6/6
   acceptance) but break 2 visible tests. This happened on parse_137. No
   candidate was green in 6 of 25 phases.
3. **Time.** Visible tests run N times. The phase took 75–220 s on
   pyparsing, about 1,850 tests.

**Selection statistics:** a candidate other than #1 was chosen in 16 of 25
phases.

**A revised A, if ever:** rank by acceptance passes first when the gate is
active, and sample extra candidates only after the first one fails. That
would be a new pre-registered run. It is low priority: the measured gain
here is small.
