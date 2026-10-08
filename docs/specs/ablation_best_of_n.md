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
