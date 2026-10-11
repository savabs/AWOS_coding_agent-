# Aider arm on Anthropic Haiku 5.5 (track B1, 2026-10-11)

Pre-registered before any live run. Committed together with the code it proves.

## What changed

- `scripts/harness_aider.py` gains `--provider auto|openrouter|anthropic` (default
  `auto`). A bare `claude-*` id (no `/`) runs on Anthropic directly as litellm's
  `anthropic/<id>`, with `ANTHROPIC_API_KEY` read from the source `.env`
  (`AWOS_JOB_DOTENV`, never copied) and every other provider variable dropped.
  Ids with a `/` keep the old OpenRouter path (the `aider-frontier` arm is unchanged).
- On Anthropic, Aider runs with `--cache-prompts` (AWOS also caches on Anthropic).
- Cost: the harness computes `cost_usd` from Aider's exact per-response tokens at
  flat Haiku 5.5 prices: $0.10/M input, $0.50/M output, $0.01/M cache read,
  $0.125/M cache write. These are the same prices AWOS bills itself
  (`scaffold/agent/providers.py`). litellm's own table adds a 5x tier above 100k
  prompt tokens, so the harness does not use Aider's figure. The report records
  `cost_source = "token_price_table"`.
- `scripts/job_series.py`'s `aider` arm already passes the pinned model, so
  `AWOS_SERIES_MODEL=claude-haiku-5-5` is all it takes.

## Live proof (not an A/B)

**Issues.** The first 4 real issues in sorted order under
`~/.awos-harness/real_series`: `real_attrs_1513`, `real_attrs_1593`,
`real_boltons_428`, `real_boltons_458`.

**Arm.** `aider` only, one run each.

**AWOS reference.** The Haiku 5.5 baseline (`docs/memory/runs/haiku55_baseline_2026-10-11.md`)
solved all 4, at $0.0028, $0.0051, $0.0028 and $0.0035.

**Pass criteria for the wiring.** All of the following must hold:
1. Every run is valid: no `invalid_reason`, and no `aider_no_edit` with a setup reason.
2. The `[harness_aider]` banner shows `model=anthropic/claude-haiku-5-5 provider=anthropic key=set`.
3. `report.json` has `provider = "anthropic"` and `cost_source = "token_price_table"`, and `cost_usd > 0` for any run that made at least one model call.
4. Cross-check: for runs whose prompts are all under 100k tokens, Aider's own litellm session cost agrees with our computed cost within 10%.
5. Total spend is at most $0.50. Stop early if the cumulative cost passes $0.40.

**Solved count.** Reported as is. With n = 4 and a single run, it supports no comparative claim about Aider versus AWOS. The stats rule (`docs/specs/stats_decision_rule.md`) does not apply, because this is not an A/B.

## Result so far (2026-10-11, partial)

- Run 1, `real_attrs_1513`: Anthropic returned a 400 ("`temperature` is deprecated for this model") and Aider got 0 responses. job_series nonetheless scored it a fair "not solved". Both problems are fixed in commit 3e352cc: the harness sends no temperature to Claude 5.x, and a run with a provider error and 0 responses is now `aider_no_edit=no_model_response`, which counts as invalid.
- Run 2, `real_attrs_1513`: **solved**, hidden tests 163/163, 1 turn, $0.008396 (`token_price_table`). This matches litellm/Aider's own figure of $0.008396 exactly. That confirms `prompt_tokens` include the cache writes: 65,205 written, 0 read. AWOS reference on this issue: $0.0028.
- `real_attrs_1593`, `real_boltons_428` and `real_boltons_458` were started but had not finished when this track had to report. Their logs are in `b1_runs/*.log`. Spend so far is about $0.0084. Not counted: whatever the three in-flight runs used before they were cut off; the arm's per-job cap is $1.
