# Evaluation instrument: preflight, health check, report

This is Stage 1 support work. AWOS cannot improve what it cannot measure
honestly. The protocol behind it is
`docs/research/evaluation_first_principles_2026-10.md`, rules 1–6 and 8–10.

## Why

Twice we drew conclusions from runs whose harness was broken. The Aider arm got
no files, a notebook was silently overwritten, and an unpinned Sonnet reviewer
ran on every job. Both times we found out only after the conclusion.

## Pieces

**A. Preflight and manifest** (`scripts/job_series.py`)

- `run` runs `validate` first. Every selected job must have:
  - hidden tests that fail on the start state;
  - hidden tests that pass with the reference.
  An invalid job aborts the run before any spend.
- Each (repeat, arm, job) row records an input manifest:
  - the project tree hash;
  - the task hash;
  - the command, with secrets masked;
  - the pins;
  - for Aider, the files in chat.
- If arms of the same job received different inputs, that is a fatal violation.
- The results file records provenance: git SHA, dirty flag, config hash and
  timestamps.

**B. Call log and health check** (`scaffold/agent/llm_call_log.py`,
`scripts/eval_health.py`)

- Every model call appends one line to `.awos/llm_calls.jsonl`. Each line has
  the component, requested and response model, finish_reason, tokens
  (including reasoning and cached), cost and visible chars. Writing the line
  never raises.
- `check_run` returns `{ok, violations[], stats}`.

**Fatal violations:**

- A model outside the pinned allowlist.
- A truncated or empty reply from a component that writes state.
- The notebook shrinks by more than 40% between jobs.
- Aider had 0 files in chat.
- A zero-work job that is not marked invalid.
- A preflight or manifest mismatch.

**Warnings:**

- No call log.
- Agent-loop truncation above 5%.
- No billed figure.
- Billed vs logged cost differing by more than 50%.
- More than 25% of jobs dropped.

**C. Report** (`scripts/eval_report.py`)

`write_report(results, out_dir)` writes `report.md` and `pareto.svg`. The
report contains:

- a VALID/INVALID banner from `health.json`;
- per arm: solve rate with Wilson 95% CI, pass@1/pass^K, $ per job, $ per
  solved job, and solved per $1;
- paired comparisons:
  - K=1: exact McNemar.
  - K>1: sign-flip permutation and bootstrap CI.
  - In both cases: cost and turn differences, and the MDE;
- a per-job table;
- failing hidden tests;
- a Pareto chart.

## Live proof

- `validate` runs clean on ordertool and backupd.
- `eval_health` on run 20260930T170402 flags these as fatal:
  - the Aider jobs with no files;
  - the notebook drop from 2732 to 364 chars.
- `eval_report` reproduces that run's numbers with intervals and p-values.
- The next real run ends with `health.json`, `report.md` and a VALID or
  INVALID verdict.

## Out of scope

- Commit replay.
- Memory redesign.
- Cost changes.
