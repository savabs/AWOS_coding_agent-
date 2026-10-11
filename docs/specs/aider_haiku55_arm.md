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

## Final result (2026-10-11, track B1 second attempt)

The second attempt merged the first attempt's branch (`worktree-wf_0d963615-8f0-2`)
and reran the 3 unfinished pre-registered issues, one run each, at commit 241aeae
plus the two fixes below. Run files are in `docs/memory/runs/aider_haiku55_liveproof_2026-10-11/`.

| issue | solved | hidden tests | turns | Aider cost (token_price_table) | Aider/litellm own figure | AWOS Haiku 5.5 reference |
|---|---|---|---|---|---|---|
| real_attrs_1513 (attempt 1, run 2) | yes | 163/163 | 1 | $0.008396 | $0.008396 | $0.0028 |
| real_attrs_1593 | yes | 48/48 | 1 | $0.008254 | $0.008254 | $0.0051 |
| real_boltons_428 | yes | 45/45 | 1 | $0.005329 | $0.005329 | $0.0028 |
| real_boltons_458 | yes | 30/30 | 1 | $0.007786 | $0.007786 | $0.0035 |

**Wiring criteria: all five pass.**
1. All 4 counted runs are valid: `invalid: false`, `aider_no_edit: null`, `attempts: 1`, and `health.json` reports `ok: true`. The one warning is `missing_billing`, because Anthropic has no per-request billed figure.
2. The banner reads `model=anthropic/claude-haiku-5-5 provider=anthropic key=set` every time.
3. `report.json` has `provider: anthropic`, `cost_source: token_price_table`, and `cost_usd > 0`.
4. Every prompt is under 100k tokens (the largest is 63,046). The `[aider-usage]` cost, which is Aider's `total_cost` delta (litellm), equals our computed cost to the microdollar.
5. Spend. This attempt's counted runs total $0.021369. The 3 crashed runs (below) made 0 model calls and cost $0. Attempt 1 spent about $0.0084 counted, plus about $0.021 in its cut-off runs: their logs show per-response costs of $0.0080, $0.0052 and $0.0079. Cumulative spend is about $0.051, under both the $0.40 stop and the $0.50 cap.

**Solved: 4/4.** That is a single run on n = 4, which supports no comparative claim (see above). On these 4 issues Aider costs 1.6 to 3.0 times AWOS's reference cost. Almost all of it is the first-call cache write: 37k to 62k tokens at $0.125/M, because Aider sends a repo map plus the whole files. This is an observation, not a test.

### Deviation and two bugs found by the live proof

- **Relative `--out-root` crashed the Aider arm before any model call.** The first rerun used `--out-root b1_runs/<issue>`. job_series ran the harness with the arm's state directory as its working directory. The harness resolved the relative project path against that directory, and `Popen(cwd=...)` raised `FileNotFoundError`. job_series then scored all 3 as a fair **"not solved"**, because the log had no `[aider-final]` line and the fallback in `aider_no_edit` returned `no_edits`. Those logs are kept, uncommitted, under `b1_runs/crashed_relative_outroot/`. The results were discarded, which is allowed: they are infrastructure failures with 0 model calls and $0 spent. The issues were rerun.
  - Fix 1, `scripts/job_series.py main`: `--series-root`, `--out-root` and `--env-file` are resolved to absolute paths.
  - Fix 2, `aider_no_edit`: if the harness banner is followed by a Python traceback, with no `[aider-final]` line, no `[aider-usage]` line, no applied edit and no harness no-edit line, the result is `harness_crashed`. That reason is in `NO_EDIT_SETUP_REASONS`, so the run is **invalid** and gets retried instead of being scored as the model's failure.
  - Tests: `test_aider_harness_crash_is_invalid_not_a_fair_failure` and `test_main_resolves_relative_roots`.
- **Test timeouts.** Under parallel-agent load (load average about 10), 5 to 9 end-to-end tests in `tests/test_job_series_runner.py` went over the suite's 10s per-test timeout. They take 8 to 25s even when they pass. They now carry `pytest.mark.timeout(120)`, and their logic is unchanged. `tests/test_harness_aider*.py` plus `tests/test_job_series_runner.py`: 90 passed, 1 skipped.

## How to run the Aider arm on Haiku 5.5

Prerequisites:
- `ANTHROPIC_API_KEY` is set in the repo's `.env`, or in the file named by `--env-file`. job_series points `AWOS_JOB_DOTENV` at that file and does not copy it.
- Aider is installed in `~/.awos-harness/aider-venv`.

```bash
cd <repo or worktree>
PYTHONUNBUFFERED=1 AWOS_SERIES_MODEL=claude-haiku-5-5 caffeinate -i \
  /Users/becmachlean/Projects/AWOS_coding_agent/.venv/bin/python scripts/job_series.py run \
  --series real_boltons_428 \
  --series-root ~/.awos-harness/real_series \
  --arms aider \
  --out-root /abs/path/to/results        # relative now works too (fix 1)
```

- `AWOS_SERIES_MODEL=claude-haiku-5-5` pins the model. A bare `claude-*` id makes `harness_aider --provider auto` call Anthropic directly as `anthropic/claude-haiku-5-5`, with `--cache-prompts` on and no temperature. Never use OpenRouter for this arm.
- Add `off` to `--arms` (for example `--arms off,aider`) to run AWOS on the same inputs, interleaved job by job.
- Check that every run worked:
  - The banner says `provider=anthropic key=set`.
  - The row has `invalid: false`.
  - `report.json` has `cost_source: token_price_table`.
- Resumable loop over several issues: `docs/memory/runs/aider_haiku55_liveproof_2026-10-11/run3.sh` skips any issue that already has a `job_series_*.json`.
- Running the harness alone, outside job_series: `python scripts/harness_aider.py <job_dir> <project_dir> --model claude-haiku-5-5 --max-reflections 10`, run from an empty state directory.
