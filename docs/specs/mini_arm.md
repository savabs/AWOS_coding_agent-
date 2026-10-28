# Minimal-harness baseline arm ("mini", "mini-local")

**Question:** how much does the AWOS harness add over the raw model with a shell?
AWOS vs Aider compares two engineered harnesses; neither tells us the floor.
The "mini" arm is that floor: the same model, the same goal text, the same repo
snapshot, driven by the smallest credible agent loop.

## What runs

[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) (pip
`mini-swe-agent`, 2.4.6 at the time of writing), the ~100-line bash-only agent from the
SWE-agent team: one `bash` tool, one command per step, observation back,
finish with `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`. Stock prompts from its
`config/mini.yaml` are used **unchanged**: no repo map, no edit tool, no
planner, no test runner, no memory, no AWOS tricks.

`scripts/harness_mini.py` follows the `harness_aider.py` contract:

* `python scripts/harness_mini.py <job_dir> <project> --model <id>`, cwd = the arm's state dir
* reads `task.json`; writes `./report.json` with `success`, `usage`
  (`cost_usd, turns, input_tokens, output_tokens, models, llm_calls`), `harness: "mini"`,
  `exit_status`, `invalid_reason`, cache tokens, limits
* key from the environment or `AWOS_JOB_DOTENV`; other providers' keys/base URLs
  are stripped (`harness_aider.child_env`); `BROWSER=true`
* mini-swe-agent lives in its own venv `~/.awos-harness/venvs/mini`
  (`AWOS_MINI_VENV`); the harness runs an inline launcher in that venv that
  uses the Python API (`DefaultAgent` + `LocalEnvironment`, cwd = project), so
  nothing prompts for confirmation. `AWOS_MINI_CMD` swaps in a fake launcher for tests.

Install: `python3.12 -m venv ~/.awos-harness/venvs/mini && ~/.awos-harness/venvs/mini/bin/pip install mini-swe-agent`

### Parity settings

| | value |
|--|--|
| model (`mini`) | `PINNED_MODEL` (`AWOS_SERIES_MODEL`, e.g. `claude-haiku-5-5`) → litellm `anthropic/claude-haiku-5-5`, Anthropic direct, prompt caching (`set_cache_control: default_end`), **no temperature** (Claude 5.x rejects it, cf. 3e352cc), `max_tokens` = `AWOS_AGENT_MAX_OUTPUT_TOKENS` (16384) |
| model (`mini-local`) | `local/<AWOS_LOCAL_MODEL or qwen3.5-9b>` → `openai/<name>` at `AWOS_LOCAL_BASE_URL` (default `http://127.0.0.1:8080/v1`), $0 |
| step limit | `task.json` `max_turns` |
| cost limit | `max_cost_usd`, lowered to `AWOS_JOB_MAX_COST_USD` (series budget) |
| wall time | `timeout_min*60 − 30 s` (agent's own limit `−30 s` more); hard kill at the deadline |
| per-request timeout | `AWOS_MODEL_TIMEOUT_S` (120 s), as AWOS and Aider |
| per-command timeout | 120 s (`AWOS_MINI_CMD_TIMEOUT_S`; mini's default 30 s kills test runs) |
| shell `python`/`pytest` | the runner's interpreter first on `PATH` (the one AWOS/Aider test with) |
| task text | the job's `goal` + one line: "The repository is your current working directory (<project>)." (mini's own SWE-bench configs name `/testbed` the same way; without it Haiku ran `find / -name timeutils.py` and found other copies of the repo) |
| sandbox | every shell command runs under macOS `sandbox-exec` (`state/mini.sb`): no reads of `~/.awos-harness`, the job dir and series dir (hidden tests, reference), the arm's state dir, any `.awos` dir or `.env`; no writes under `$HOME` except the project. Aider can't reach those (no shell) and AWOS's tools are project-scoped, so an unconfined shell would be an unfair leak (and could corrupt a concurrent run). `AWOS_MINI_SANDBOX=0` disables it; it's off automatically where `sandbox-exec` doesn't exist |
| provider retries | mini's tenacity retry, capped at 5 attempts (`MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT`) |

### Cost

The launcher computes each call's cost from litellm's exact usage at
`harness_aider.ANTHROPIC_PRICES` (litellm's Anthropic `prompt_tokens` include
cache reads and writes: uncached = prompt − read − write), the same table AWOS and the
Aider arm bill at, and feeds it back to mini so its own `cost_limit` binds on
that figure. Every call is one `[mini-usage]` line in the job log; empty
responses are billed. Local: $0. OpenRouter ids: litellm's price table.

### INVALID vs not solved

The harness prints `[harness_mini] invalid_run reason=<r>`; `job_series.detect_invalid`
scores it `mini_invalid (<r>)` (retried by job_series, then dropped pairwise):

* `empty_model_response`: the run ended on calls with no text and no tool call
  (mini's 3-strikes `RepeatedFormatError` on empties), or every call was empty
* `provider_error`: a model call still failed after retries (litellm/openai/anthropic exception)
* `harness_error`: the launcher raised a non-provider exception, died, or printed no final line
* `no_model_response`: zero responses

A banner without a `[harness_mini] done` line is `harness_crashed`. Fair results:
`Submitted`, `LimitsExceeded` (steps/cost), `TimeExceeded`, a format-error
exit with non-empty responses, and a hard kill at the deadline after real work.
`turns` = responses with content.

## Running

```
AWOS_SERIES_MODEL=claude-haiku-5-5 python scripts/job_series.py run \
  --series fresh_boltons_441 --series-root ~/.awos-harness/fresh_series \
  --arms mini --repeat 1 --out-root .awos/jobs/mini_live/fresh_boltons_441
```

Pair with `off`/`aider` in the same `--arms` list for a head-to-head. `mini-local`
needs a running llama-server on `AWOS_LOCAL_BASE_URL`.

## Live proof (2026-10-13, claude-haiku-5-5, sandbox on, n=1 each)

| issue | result | turns | cost | min |
|--|--|--|--|--|
| fresh_boltons_441 | SOLVED (hidden 13/13) | 13 | $0.0136 | 1.7 |
| fresh_attrs_1592 | SOLVED (hidden 52/52) | 26 | $0.0274 | 2.8 |
| fresh_pathspec_157 | SOLVED (hidden 130/130) | 15 | $0.0216 | 2.2 |

3/3, $0.063 total. A smoke test, not a comparison: the floor is high on these
issues, so the AWOS-over-raw-model gain has to be measured paired on the full set.

## Caveats

* At the deadline the harness sends SIGTERM (the launcher kills its running
  command, which mini starts in its own session), then SIGKILL after 5 s.

* mini's stock prompt asks the model to write a reproduction script; it may leave
  stray files in the project (counted in `files_changed`, harmless to the judge).
* The local model must support OpenAI tool calls (llama-server `--jinja`); mini's
  text-based mode is not wired.
* `[mini-step]` echoes each command (first 160 chars) into the job log; a command
  that literally contains a job_series INVALID marker string would trip it, as with
  Aider's streamed output.
