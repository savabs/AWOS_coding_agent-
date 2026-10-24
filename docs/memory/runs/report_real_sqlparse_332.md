# Evaluation report — real_sqlparse_332 20261005T041122

> **VALID** — health check `health.json`: 0 fatal, 0 other violation(s).

## Run

- results: `/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/bench-run/.awos/job_series_20261005T041122.json`
- run id: `20261005T041122`
- series: `real_sqlparse_332` · arms: `off`, `aider`
- model pin: `deepseek/deepseek-v4-flash` · blocked: deepseek-v4-pro
- jobs: 1 · repeats K = 1 · rows: 2
- dropped (paired exclusion): none

## Per arm

Rows valid in every arm only. Intervals are 95%. 

| arm | solved/n | rate | Wilson CI | Clopper-Pearson CI | mean turns | mean $/job | $/solved | solved per $1 | mean min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `off` | 1/1 | 100.0% | [20.7, 100.0] | [2.5, 100.0] | 39.0 | $0.0274 | $0.0274 | 36.4 | 6.98 |
| `aider` | 1/1 | 100.0% | [20.7, 100.0] | [2.5, 100.0] | 1.0 | $0.0000 | $0.0000 | - | 0.53 |

$ is reconciled provider billing (`billed_usd`).

## Paired comparisons

Each pair uses the jobs valid in both of its arms. Difference = first − second. K = 1: exact McNemar on the discordant pairs (b = only first solved, c = only second solved).
MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).

### `off` vs `aider` — 1 tasks, 1 paired runs

- solved: off 1/1 vs aider 1/1 · mean difference 0.0%
- discordant: b = 0, c = 0 → exact McNemar p = 1.0000
- **Not significant at 0.05.** MDE: not computable (fewer than 2 tasks).
- billed $/job difference: $0.0274 · CI [+0.0274, +0.0274] · sign-flip p = 1.0000 (1 tasks)
- turns difference: 38.0 · CI [+38.0, +38.0] · sign-flip p = 1.0000 (1 tasks)

## Per job

Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $.

| job | id | `off` | `aider` |
| --- | --- | --- | --- |
| 1 | `01_get_real_name_dotted` | ✓ 88/88 · 39t · $0.0274 | ✓ 88/88 · 1t · $0.0000 |

\* ledger cost (no reconciled billing for that row).

## Failures (valid rows)

Includes valid rows of jobs dropped by paired exclusion.

### `off` — 0 unsolved valid run(s)

none

### `aider` — 0 unsolved valid run(s)

none

## Pareto: solve rate vs $

![pareto](pareto.svg)

| arm | mean $/job | solve rate |
| --- | --- | --- |
| `off` | $0.0274 | 100.0% |
| `aider` | $0.0000 | 100.0% |

Protocol rule 9: compare against a simple retry baseline at the same budget before claiming a cost win.
