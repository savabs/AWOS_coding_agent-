# Harbor head-to-head on GitHub Actions

`.github/workflows/harbor.yml` runs coding-agent harnesses on **Terminal-Bench
2.1** through [Harbor](https://github.com/harbor-framework/harbor) (Apache-2.0).
Every agent uses `anthropic/claude-haiku-5-5`. The jobs run on free
`ubuntu-latest` runners (Docker, 4 vCPU / 16 GB, about 14 GB of disk before
cleanup). The matrix is agent × task shard. The study design and analysis plan
are in `docs/specs/harbor_head_to_head.md`. This file covers operations only.

## Files

| File | Purpose |
|---|---|
| `ci/harbor.env` | All settings: `DATASET`, `TASKS`, `AGENTS`, `MODEL`, `N_ATTEMPTS`, `N_CONCURRENT`, `N_SHARDS`, `SHARDS`, `MAX_PARALLEL`, `CAP_AGENT`, `TASK_CAP`, `HARBOR_VERSION`, `PIN_*`, `AWOS_GIT_REF`, `AWOS_MAX_STEPS`, `PRUNE_BELOW_GB`, `SHARD_MINUTES` |
| `ci/harbor_tb21_tasks.json` | Metadata for the 89 TB 2.1 tasks (category, difficulty, timeouts, resources), read from `task.toml` at revision 6 |
| `ci/harbor_tasks_sample.txt` | The 10 tasks of Harbor's `terminal-bench-sample@2.0`, run from TB 2.1 (all 10 exist there) |
| `ci/harbor_tasks_<seed>.txt` | Seeded stratified subsets (`ci/harbor_tasks_1.txt`: 30 tasks, seed 1) |
| `harbor_agents/awos_agent.py` | AWOS as a Harbor installed agent (`-a harbor_agents.awos_agent:AwosAgent`) |
| `harbor_agents/pricing.py` | One per-request Haiku 5.5 price table, used for every agent's cost |
| `scaffold/agent/headless_loop.py` | AWOS's headless entry: the `loop` executor on one goal, with no host, no git and no test gate |
| `scripts/harbor_tasks.py` | `metadata`, `sample`, `stratified --seed S --count N` |
| `scripts/harbor_run.py` | `ci-config`, `tasks`, `cmd`, `shard` (runs on the runner) |
| `scripts/harbor_collect.py` | `stage` (runs on the runner), `report` (runs on the laptop) |

Pushes to `bench-cloud/harbor-**` start this workflow. `ladder.yml` ignores
those branches (it still runs on every other `bench-cloud/**` branch).

## Harbor facts these files rely on (harbor 0.24.0, PyPI, read from source at f527f8e)

- Install with `pip install harbor==0.24.0` (needs Python ≥ 3.12). The CLI is `harbor`.
- Dataset: `-d terminal-bench/terminal-bench-2-1@6`. This is a Harbor Hub package
  (`org/name@ref`, where ref is a revision number, `latest`, a tag or a sha256).
  Revision 6 was `latest` on 2026-10-15 (content hash `7d7bdc1c…`), with 89 tasks.
  The legacy `registry.json` only has `terminal-bench@2.0` and
  `terminal-bench-sample@2.0`. There is no TB 2.1 sample package
  (`terminal-bench/terminal-bench-2-1-sample` and similar names return
  "not found"), so the sample is the 2.0 sample's task list run from 2.1.
- `-i/--include-task-name` takes a glob matched against `org/name`
  (`terminal-bench/regex-log`) and can be repeated. Related flags:
  `-x/--exclude-task-name`, `-l/--n-tasks`, `-k/--n-attempts`,
  `-n/--n-concurrent`, `-o/--jobs-dir`, `--job-name`, `-y` (no prompts),
  `-q` (quiet), `--timeout-multiplier` (left unset here).
- Agents are chosen with `-a NAME` or `-a module.path:ClassName` (the module
  must be importable by the Harbor process, so set `PYTHONPATH=.`). Use
  `--ak key=value` for kwargs (values are parsed as JSON or Python literals)
  and `--ae KEY=VALUE` for env vars. The `--ae` vars are layered over each
  agent exec, and they win over the agent's own per-exec env. Use
  `harbor agent schema NAME` to list an agent's options.
- Installed agents (`BaseInstalledAgent`) are installed into the task
  container in `install()` and run with `exec_as_agent`. External agents
  (`BaseAgent`, for example terminus-2) run their loop on the host and act
  through `environment.exec`. AWOS is an installed agent.
- Version pins come from `--ak version=…`:
  - claude-code: `curl …/claude-code-releases/bootstrap.sh | bash -s -- <version>`, or npm on Alpine
  - mini-swe-agent: `uv tool install mini-swe-agent==<version> --with litellm`. **litellm itself is not pinned.**
  - opencode: `npm i -g opencode-ai@<version>`
  - openhands: `uv pip install openhands-ai==<version>`
  - goose: the download_cli.sh of a GitHub release
- Output layout: `<jobs-dir>/<job-name>/<trial-name>/result.json` (`TrialResult`),
  with `agent/`, `verifier/` (`reward.txt`, `test-stdout.txt`) and `exception.txt`
  alongside it. The job-level `result.json` sits in `<job-name>/`.
  `TrialResult` contains `task_name`, `trial_name`, `agent_info{name,version,model_info}`,
  `agent_result{n_input_tokens (includes cache), n_cache_tokens, n_output_tokens, cost_usd, metadata}`,
  `verifier_result.rewards{reward}`, `exception_info.exception_type`, and timings
  (`agent_execution`, `verifier`, …). Agents that write ATIF save per-step usage
  to `agent/trajectory.json`.
- Telemetry is off with `HARBOR_TELEMETRY=0`, which the workflow sets.

## Fairness settings (scripts/harbor_run.py `agent_args`)

| | AWOS | claude-code | mini-swe-agent |
|---|---|---|---|
| model | `anthropic/claude-haiku-5-5` | same (`ANTHROPIC_MODEL`) | same (LiteLLM) |
| helper / subagent model | n/a | `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL` = `claude-haiku-5-5` via `--ae` (Harbor sets these only when a base URL is configured) | n/a |
| web tools | none exist | `disable_web_search=true` → `--disallowedTools WebSearch,WebFetch` | none exist |
| version | git sha of `AWOS_GIT_REF` | `2.1.292` (npm `stable` tag on 2026-10-15) | `2.4.6` (same as the ladder's mini arm) |
| effort / thinking | not sent (API default) | Claude Code's own default | not sent (API default) |
| step limit | `AWOS_MAX_STEPS=1000` (the product default is 60) | none | none (mini default `step_limit: 0`) |
| per-trial cost cap | `cost_cap` | `max_budget_usd` | `cost_limit` |
| task timeout | dataset default | dataset default | dataset default |
| concurrency | `N_CONCURRENT=1` inside a harbor run; one task per harbor run | same | same |

Every agent's cost in the report is recomputed with one per-request price table
(`harbor_agents/pricing.py`), not taken from each harness's own estimate.

### What could not be equalised

- **Effort and thinking.** Claude Code picks its own effort and thinking
  settings, and its `--effort` default for Haiku 5.5 was not checked. AWOS and
  mini send no `effort`, so they get the API default (`medium` on Haiku 5.5,
  thinking adaptive). Passing Harbor's `--effort` to everyone would change
  AWOS's default, so it is left unset.
- **Prompt caching.** AWOS sets 5-minute cache breakpoints. Claude Code caches
  in its own way (possibly 1-hour writes). mini-swe-agent depends on what
  LiteLLM does for Anthropic. This affects cost but not pass rate.
- **Context management.** Claude Code compacts long contexts. AWOS's loop and
  mini do not. Prompts over 100K tokens are billed on the ×5 card.
- **Tools.** Claude Code has editor, search, todo and subagent (Task) tools.
  AWOS and mini have bash only. Command timeouts differ too: AWOS 120 s
  (`cmd_timeout_s`, up to 600), Claude Code's Bash default, mini's own.
- **Cost caps.** Each agent applies its cap to its own estimate. mini's estimate
  may be 0 if LiteLLM has no price for `claude-haiku-5-5`, in which case its cap
  never fires. The shard cap (`CAP_AGENT / N_SHARDS`, recomputed uniformly after
  every task) applies to all agents in the same way.
- **litellm version** inside mini's tool venv is not pinned (Harbor installs it
  with `--with litellm`).
- **The task container is changed by installation.** Each installed agent adds
  its own runtime (node for Claude Code, a uv Python for mini and AWOS) and may
  `apt-get install` curl or bash.

## AWOS inside the task container

`AwosAgent.install()` takes `git archive <AWOS_GIT_REF> scaffold/agent` on the
host and uploads it to `/installed-agent/awos`. It then installs uv, Python
3.12 and `anthropic==0.125.0` / `openai==3.3.1` (the ladder CI lock) into
`$HOME/.awos-venv` and test-imports the headless entry. **The ref must be a
commit that contains `scaffold/agent/headless_loop.py`.** In CI, `HEAD` is the
pushed commit.

`run()` sets `AWOS_SANDBOX=container` and runs
`python -P -m scaffold.agent.headless_loop` in the container's working
directory. The instruction is passed in an env var, never on the command line.
The loop runs until the model submits, or until it hits the step cap, the cost
cap or Harbor's timeout. There is no git branch, handoff or test gate: the task
has no visible suite, and Harbor's verifier runs afterwards. Every model call
is appended to `agent/awos_calls.jsonl` as it happens. As a result a run killed
at the timeout still reports its tokens, and `populate_context_post_run` fills
in Harbor's `AgentContext`.

`AWOS_SANDBOX=container` is an explicit mode (`sandbox.ContainerSandbox`). It
runs commands directly, with credentials and AWOS's own `PYTHON*`/`AWOS_*`
vars stripped from their env. `auto` never selects it. In this mode the loop's
prompt says the environment is a disposable container and does not promise a
sandbox.

## Launch

The repo secret `ANTHROPIC_API_KEY` must be set (the ladder already uses it).

**Pilot**: the 10-task sample, with agents claude-code, mini-swe-agent and awos, k=1.
These are the committed defaults: `TASKS=sample`, `N_SHARDS=2`, `CAP_AGENT=3`,
`TASK_CAP=1.0`.

    git switch -c bench-cloud/harbor-pilot
    git push -u origin bench-cloud/harbor-pilot

That gives 6 matrix jobs (3 agents × 2 shards of 5 tasks). Each agent can
spend at most $3.

**Full run**: all 89 tasks, or a seeded subset. Edit `ci/harbor.env`, commit,
then push to a fresh branch such as `bench-cloud/harbor-full-1`:

    TASKS=file:ci/harbor_tasks_all.txt   # or seed:1:30 (ci/harbor_tasks_1.txt)
    N_SHARDS=10
    MAX_PARALLEL=10
    CAP_AGENT=30

To make a list of all tasks, run
`python3 -c "import json;print('\n'.join(t['name'] for t in json.load(open('ci/harbor_tb21_tasks.json'))['tasks']))" > ci/harbor_tasks_all.txt`.
To make a new subset, run `python scripts/harbor_tasks.py stratified --seed S --count N`
and set `TASKS=seed:S:N`.

Two TB 2.1 tasks have agent timeouts of 7200 s and 12000 s. When a task would
not fit in the remaining `SHARD_MINUTES`, the shard driver skips it and lists
it under `skipped_time` in `shard.json`. Run such tasks in their own shard or
on another machine.

**Local equivalent** (needs Docker). These commands print the `harbor run`
command without running it:

    python scripts/harbor_run.py cmd --agent awos --task terminal-bench/regex-log
    python scripts/harbor_run.py shard --agent awos --shard 0 --jobs-dir /tmp/hj --dry-run

## Collect

    gh run list --workflow harbor.yml --limit 5
    gh run download <run-id> -D ~/.awos-harness/harbor_dl
    python scripts/harbor_collect.py report ~/.awos-harness/harbor_dl --json ~/.awos-harness/harbor_report.json

The report prints one table per agent with:

- pass rate and its Wilson 95% CI, where infra errors count as failures
- the same rate with infra errors excluded
- mean cost per trial at uniform prices
- mean agent time
- counts of infra errors, agent timeouts and refusals
- where each trial's usage numbers came from

It then pairs AWOS against each other agent on the tasks both ran:

- k=1: exact McNemar
- k>1: paired bootstrap over tasks of the per-task pass rate
- either way, a paired bootstrap of the cost difference

Outcome classes per trial:

- `infra`: there is no verifier result, or the exception is an
  environment, verifier, provider or harness-setup error (the list is in
  `harbor_collect.INFRA_EXCEPTIONS`)
- `agent_timeout`: Harbor's `AgentTimeoutError`. The verifier still scores
  the trial.
- refusals are flagged separately and do not change the outcome: Harbor's
  `AgentSafetyRefusalError`, a `"stop_reason":"refusal"` in an agent log, or
  an AWOS call with `finish_reason=refusal`

Each artifact is small: one `summary.json` per trial, Harbor's `result.json`,
the verifier reward and output, and the agent logs cut to 64 KB (head and tail).
`trajectory.json` files and container files are never uploaded.

## Unverified assumptions

- Haiku 5.5 prices ($0.10 / $0.50 per M tokens, ×5 above 100K prompt tokens;
  cache read 0.1×, 5-minute write 1.25×, 1-hour write 2×) come from the
  claude-api skill's model notes (cached 2026-10-06), not from the live
  pricing page. Three further points are assumed rather than documented:
  - the cache multipliers apply on the long card
  - the 100K split counts the whole prompt (cached + written + fresh tokens)
  - writes with no TTL breakdown are priced as 5-minute writes
- How LiteLLM reports Anthropic cache writes in mini's trajectory
  (`prompt_tokens_details.cache_creation_tokens`) was not observed. Where
  absent, mini's writes are priced as fresh input.
- When a trajectory's per-step prompt total differs from the agent's reported
  total by more than 2% (for example, Claude Code subagent calls kept in
  separate files), the trial falls back to one aggregate row priced on the
  short card. This can under-count long-prompt requests. The report shows
  `usage src` for every agent.
- No trial has been run end to end yet, neither locally nor in CI. The
  adapter, driver and collector are tested with fakes only.
