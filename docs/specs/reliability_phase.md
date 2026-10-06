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
