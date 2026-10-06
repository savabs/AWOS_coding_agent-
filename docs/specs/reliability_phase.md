# Reliability phase: real issues, frontier baseline, trustworthy "done"

Started 2026-10-06. Stage 1 (one excellent worker). The question changes from
"is AWOS cheap?" to "**can AWOS's results be trusted unattended?**"

## Why

The cost phase is done. One-shot is now the default, and AWOS is about 3× from
Aider on solved jobs per dollar while solving more jobs. Aider is a cost-floor
reference, not the product target (`VISION.md`: maximum reliable work, run
autonomously).

What still blocks unattended use:

- **(C)** One-shot accepts visible tests alone and misses hidden requirements.
- **(G)** The agent loop can spin for 90–130 turns without making an edit.
- **Test discovery breaks on real repos.** After a one-shot, "no tests ran"
  sends the job to the agent loop.
- **There is no frontier reference.** We do not know the solve rate a strong
  agent reaches on these tasks.
- **Only 3 real issues exist.**

## Pieces

| Piece | What it delivers |
|---|---|
| R1, R2 | 20–24 more validated real issues: merged 2025–2026 PRs from non-SWE-bench repos, judged by each PR's own tests. Stored outside the repo in `~/.awos-harness/real_series/`. |
| F1 | When the changed-file → test mapping finds nothing, TestRunner runs the full visible suite. |
| F2 | Agent-loop no-progress stop, re-read note and wall budget. These stops are not resumed. |
| C (separate ablation, next) | Acceptance tests derived from the task's requirements gate "done". |

## Measurement

The primary metric is the **real-issue solve rate**:

- the share of issues whose hidden tests pass;
- reported with a Wilson CI, pass@1 and pass^2 over K = 2 runs;
- with $/issue and minutes/issue, plotted as a Pareto chart.

The arms on the same issues are:

- **AWOS** with the current defaults: pinned fp8 providers, one-shot, the
  reviewer gate, F1 and F2;
- **Aider + DeepSeek V4 Flash**, the cost floor;
- **Aider + Claude Sonnet 5.5**, the frontier reference (the `aider-frontier`
  arm already in the runner).

**Budget.** AWOS and Aider-Flash cost under $1 together. The frontier arm costs
about $0.05–0.15 per issue, so about $2–4 for about 25 issues. That needs a
key top-up beyond the current ~$2.5.

## Decision rules

These are written before any run.

1. **F1 and F2 must not lower the solve rate.** Each lands as a fix after its
   own unit tests and one live real-issue check.
2. **The headline result is AWOS vs frontier on real issues.** It shows where
   the quality ceiling sits and how far AWOS is from it at what cost.
3. **C is pre-registered on its own once the real-issue set exists.** Primary
   metric: real-issue solve rate up, without cost rising more than 50%.

## Run 1 finding (2026-10-06): the instrument was broken for AWOS, not (C)

The first baseline was stopped after 9 of 28 issues: AWOS 8/18, Aider 14/18.
All 10 AWOS misses submitted **unchanged code** (`files_changed: 0`). None of
them was a wrong answer, and none was the (C) "visible tests only" miss.

- **A: test crash, then rollback (8 runs).** The repos had no pytest config
  section, so pytest searched upward and hit the checkout's `pytest.ini` above
  the job workspace, which the seatbelt sandbox denies (EPERM). The crash
  parsed as 0/0, the task failed, and a full rollback discarded the edit. In
  jsonpointer #64 that edit was the reference fix. This affects 18 of the 28
  repos. Aider does not run in the sandbox and is unaffected.
- **B: no-op one-shot edit (2 runs).** On cachetools #423 a block's REPLACE was
  identical to its SEARCH. It counted as applied, and the visible suite was
  already green, so the task reported "solved".

Fixes (`c5afbd9`, `5e18fa8`, `5e6042d`):

- No-config projects get `-c /dev/null --rootdir=. --confcutdir=.`.
- An environmental pytest crash is `infra_error`, reported as "tests could not
  run", and the edit is kept. A crash inside project code still counts as a
  failure.
- A no-op block is a failed block, so it goes to repair; "no net change" is
  never counted as solved.

Live check in the benchmark layout: jsonpointer went from 0/0 to 24 passed,
more-itertools from 0/0 to 595 mapped / 740 full.

**Run 2** re-runs AWOS on all 28 issues with these fixes. Aider's first 9
issues are reused, and it runs on the other 19. The run 1 AWOS rows stay as the
pre-fix record.
