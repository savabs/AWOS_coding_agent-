# Real-web teacher data: Claude on Mind2Web train tasks (2026-10-27)

Status: built and dry-run on branch `claude/web-teacher`. **Not run for real:** it spends money, so the owner
approves `MAX_USD` first. No paid API call was made during development.

## Why

The run A fine-tune (4B, synthetic sites) did not beat the base model on the real web: on Kaggle dev-100 the
base scored 22% official and run A 19%. The model needs real-web demonstrations in its own observation and
action format. The plan:

1. A strong, cheap model (Claude Haiku 5.5) plays the student's exact contract on live websites.
2. WebJudge-7B (free, Kaggle) keeps only the episodes it judges successful.
3. Those episodes become SFT rows.

Evaluation stays the 100 Online-Mind2Web dev tasks (`scaffold/rl/web/data/dev_ids.json`). They never reach
training, and neither does any task text from the full Online-Mind2Web 300.

## Pipeline

```
Mind2Web train (HF osunlp/Mind2Web, 11 shards, ~5.9 GB) --stream--> task headers (m2w_tasks headers)
   + Online_Mind2Web.json (HF_TOKEN) + dev_ids.json --> teacher task file (m2w_tasks build)
   --> python -m scaffold.rl.web run --model anthropic:claude-haiku-5-5 --split train   (rl-teach.yml, sharded)
   --> trajectories.jsonl + judge/<task_id>/ (sealed)  --> rl-web-judge-kaggle (WebJudge-7B, free)
   --> python -m scaffold.rl.train.web_teacher_data  --> rows.jsonl --> dataset.py --extra-rows teacher=rows.jsonl
```

| Piece | File | Tests |
|---|---|---|
| Teacher model | `scaffold/rl/web/teacher.py` | `tests/rl/test_web_teacher.py` |
| `--model anthropic:<id>`, `--split train`, stop when the spend cap is reached | `scaffold/rl/web/runner.py`, `source.py` | same |
| Task source | `scaffold/rl/web/m2w_tasks.py` | same |
| Workflow | `.github/workflows/rl-teach.yml` + `ci/rl_teach.env` | same (static checks) |
| SFT converter | `scaffold/rl/train/web_teacher_data.py` | same |
| Cost estimator | `scaffold/rl/web/teacher_cost.py` | same |

## 1. The teacher (`teacher.py`)

`TeacherModel` is a `micro_agent.ActionModel` with `output_format = "gbnf"`. It plugs in where `make_model`
builds the local model.

**Request.** The request carries MicroAgent's own system prompt (`SYSTEM + SYSTEM_WEB`), followed by the
strict reply-format rule that `CloudTextModel` already uses. The step's user prompt is sent unchanged, as one
turn. Settings:
- `claude-haiku-5-5`, `max_tokens` 300, `output_config.effort = "low"`;
- `thinking: {type: "disabled"}`, which Haiku 5.5 accepts at effort `high` or below;
- no sampling parameters (the API rejects them since 2026-10-21).

The reply is normalised to the GBNF text form: an optional `# reason` line plus one action line
(`normalize_text_reply`).

**Grammar enforced locally.** `grammar_error(reply, grammar)` checks the reply against that step's
`build_grammar` output:
- the verb is one the grammar offers (web verbs, copy);
- the control number is in the enumerated `1..n`;
- secret handles and copy targets are names the grammar lists;
- strings are 200 characters or fewer, the reason is 160 or fewer, and `goto` is http(s) with no spaces.

**One repair.** A reply that fails the check gets one more call: the original turn, the rejected reply as
the assistant turn, then a user turn saying why it was rejected and asking it to reply again. The request
ends on a user turn, so there is no prefill. If the repair also fails, the normalised reply is returned and
MicroAgent records an invalid step, the same as for the local model.

**What is recorded.** `RecordingModel` stores MicroAgent's `system` and `user` and the final reply. The
format rule and the repair turn never reach a training row.

**Prompt caching.** The system block carries `cache_control: {type: "ephemeral"}`. The prefix is about 1.9k
characters (roughly 500–600 tokens), right at Haiku 5.5's 512-token cache minimum.
- Below the minimum, the API silently skips the cache: no error and no write premium.
- When it does cache, reads bill at 0.1x.

The estimate below assumes no cache hits. At best caching saves about 25% of input cost.

**Hard cap.** Before each call, the teacher reserves the call's worst case against a per-process `Budget`
(`scaffold/rl/web_judge/cost.py`, the o4-mini judge's cap):
- worst case = input at 3 characters per token billed as a cache write, plus the full 300 output tokens;
- after the call, the reservation is settled with the billed usage;
- billed cost counts input, cache-write and cache-read tokens at their own prices, plus output.

If a reservation would exceed the cap, the call is never sent. The teacher raises `cloud budget reached ...`
instead. The episode becomes an infra error, so it is rerun on resume, and the runner stops dispatching new
tasks.

How the cap is split:
- `AWOS_TEACHER_MAX_USD` is divided by `--workers`;
- the workflow first divides `MAX_USD` by `SHARDS`;
- so the run's total spend can never exceed `MAX_USD`.

**Refusals.** A `stop_reason: "refusal"` reply becomes `needs_user("the model declined this step")`.

**Dry run.** `AWOS_TEACHER_DRY=1` swaps in `DryClient`:
- no network and no key;
- replies come from `AWOS_TEACHER_DRY_REPLIES`, a JSON list cycled through;
- usage is estimated from the request size, so the cost accounting and the cap are still exercised.

## 2. Task source (`m2w_tasks.py`)

**Headers.** `iter_headers` streams a Mind2Web train shard and keeps only the keys that come before
`"actions"`: website, domain, subdomain, annotation_id, confirmed_task, action_reprs. The raw HTML is never
parsed or stored. Unescaped `{"website":` only occurs where a task starts, and this was verified on
`train_10`: 9 of 9 tasks found.

**Contamination rules.**

| Rule | How | Effect |
|---|---|---|
| Text | `mind2web_data.Exclusion` against **every** Online-Mind2Web task passed with `--exclude` (all 300, every copy). Excluded at SequenceMatcher ratio ≥ 0.75 or token Jaccard ≥ 0.7. | Dropped. `--exclude` is required. |
| Website, `dev` (default) | Site key or brand equal to a website of a dev task. | Dropped. |
| Website, `all` | Site key or brand equal to any Online-Mind2Web website. | Dropped. This removes about half of Mind2Web train: 37 of 73 websites, 517 of 1,009 tasks (`web_data.md`). |
| Ids | A task id in `dev_ids.json`. | Hard error, here, at run time (`source.py --split train`) and in the converter. |

Training on the same website as an evaluation task, with a different task, is Mind2Web's "cross-task" setting.
`dev` keeps that out of the dev websites. `all` is the strict option.

**Start URL.** Mind2Web stores a site name, not a URL, so the start page is resolved in this order:
1. `--start-urls` JSON;
2. a small table (`aa`, `nps.gov`, `last.fm`, ...);
3. a dotted name with a known TLD is used as the host;
4. a dotted name without one gets `.com` (`sports.yahoo` → `https://sports.yahoo.com/`);
5. otherwise `https://www.<name>.com/`.

A wrong guess costs $0: the start page fails to load (`blocked: load_failed`) before the first model call.
`start_url_how` is recorded per task.

**Sample.** Deterministic:
- tasks are sorted by annotation_id;
- each website's tasks are shuffled with `Random(f"{seed}:{site}")`;
- websites are ordered by a seeded hash, then picked round-robin, so N tasks spread evenly over sites;
- `--repeats K` reruns tasks when N exceeds the eligible tasks. Ids get `-r1`, `-r2`, ... and share
  `base_task`, so the SFT validation split keeps repeats together.

**Output.** A JSON list in the `Online_Mind2Web.json` shape, with `source: "mind2web-train"`.
`OnlineMind2WebSource.tasks("train")` refuses any other file and any dev id. The stats file holds counts and
Mind2Web site names only, never task text.

## 3. Workflow (`rl-teach.yml`, `ci/rl_teach.env`)

**Trigger:** push to `bench-cloud/rl-teach-**`. `ladder.yml` excludes `bench-cloud/rl-**`, and no other
workflow matches `rl-teach`; a test asserts both.

**Jobs:**
1. **tasks.**
   - Download the Online-Mind2Web file (HF_TOKEN).
   - Stream the 11 Mind2Web train shards into headers, at pinned revision `M2W_REVISION`, cached.
   - Run `m2w_tasks build`, seal the task file, upload it.
2. **run.** A matrix of `SHARDS` jobs (max-parallel 10). Each one:
   - installs Playwright Chromium and unseals the tasks;
   - runs `scaffold.rl.web run --model $TEACHER --split train --shard k/SHARDS --workers $WORKERS` with
     `AWOS_TEACHER_MAX_USD = MAX_USD / SHARDS`;
   - writes its spend to the step summary, then seals and uploads.
3. **summary.** Merge the shards, summarize, validate the judge layout, seal, and upload as
   **`rl-web-merged-<MODEL_LABEL>-train`**. That is the artifact name `rl-web-judge-kaggle.yml` downloads.

**`DRY_RUN=1` is the committed default.** No `ANTHROPIC_API_KEY` reaches the process and
`AWOS_TEACHER_DRY=1` is set. The key is exported only when `DRY_RUN=0`.

**Sealing.** Every artifact is sealed with `scripts/ci_seal.sh` (BENCH_DATA_KEY), because the repo is public.
If the key is missing, the step fails closed.

## 4. SFT rows (`web_teacher_data.py`)

**Inputs:**
- the run directories (`trajectories.jsonl`, searched recursively);
- WebJudge `results.json` or `<out>.verdicts.jsonl`; a task whose files disagree on the label is dropped;
- `--exclude` (required).

**Kept episodes:**
- source `mind2web-train`, split `train`. A dev id or an Online-Mind2Web text match **aborts the build** with
  `ContaminationError`; it is not a filter;
- no infra error, not blocked;
- verdict `judged` with label 1 and harness_status ok;
- the last action is `done(...)`.

**Kept steps:**
- valid: parsed, grammar-checked, and not rejected by MicroAgent's code;
- has a grammar and a non-empty reply;
- not an exact duplicate.

Each row is checked with `dataset.validate_row`.

**Recovery rows.** A valid step right after an invalid or rejected one is tagged `meta.recovery = true`. Its
prompt's history shows the rejection.

With `--recovery-from-failed`, these rows are also taken from episodes the judge failed (not blocked, no
infra error). Done and needs_user steps are excluded, because a claim on a failed task is not a target.

**Row format.** The rows use `dataset.py`'s output schema: `messages`, `prompt` / `completion` from
`chat.py`, and `meta`. Fields in `meta`:
- `task_id` = the base task;
- `split: "train"`, `task_source`, `family`, `episode`, `step`, `teacher`, `recovery`.

Feed them with `--extra-rows teacher=rows.jsonl`. That run then shares dedupe, the task-id validation
split, `--clean` and `--reasons`, and stages to `kaggle/rl-sft/train.py` as usual.

## 5. Cost estimate

**Method.** `python -m scaffold.rl.web.teacher_cost` was run on the two Kaggle M0 dev-100 runs
(`trajectories.jsonl`; only aggregates are read).
- Input per call = MicroAgent system + format rule + user prompt. The mean is 6,610 characters and the p95
  is about 9,150.
- 3.2 characters per token, measured from llama-server's `prompt_tokens` on the same prompts (3.21 and 3.28).
  Claude's tokenizer is assumed to be similar; at 2.8 characters per token, add 14%.
- Output per call = reply characters / 3.2 + 10 tokens, about 20–35 tokens.
- 10% of steps need a repair call.
- No cache hits assumed.
- Haiku 5.5 is priced at $0.10 / $0.50 per 1M tokens (claude-api skill, cached 2026-10-06).

**Episode profile.**
- From Kaggle IPs, 60% of episodes reached the model. The other 40% were blocked or failed to load before
  the first call, and cost $0.
- Episodes that reached the model ran 8.65 steps (base 4B) or 14.8 steps (run A, which hits the 25-step cap
  more often).
- Mind2Web train sites from GitHub runner IPs may be blocked more or less often. The worst-case rows assume
  every episode reaches the model.

| Teacher | Profile | Calls / episode | $ / episode | **300 episodes** | **1,000 episodes** |
|---|---|---|---|---|---|
| Haiku 5.5 | like base 4B (60% reach, 8.7 steps) | 5.7 | 0.0013 | **$0.38** | **$1.27** |
| Haiku 5.5 | like run A (60% reach, 14.8 steps) | 9.8 | 0.0021 | **$0.64** | **$2.14** |
| Haiku 5.5 | 100% reach, 14.8 steps (no blocks) | 16.3 | 0.0036 | $1.07 | $3.56 |
| Haiku 5.5 | **worst case** (cap view: every episode 25 steps × 2 calls, p95 prompt, full 300 output tokens) | 50 | 0.022 | $6.56 | $21.88 |
| Sonnet 5.5 (for comparison) | like run A | 9.8 | 0.043 | $12.82 | $42.72 |
| Sonnet 5.5 | worst case | 50 | 0.44 | $131 | $438 |

**Recommended caps** (Haiku 5.5):
- 300 episodes: `MAX_USD=5`. The expected spend is about $0.4–1.1, and the hard cap stops the run if real
  prompts are larger than measured.
- 1,000 episodes: `MAX_USD=10`. The expected spend is about $1.3–3.6.

**WebJudge part ($0).** WebJudge-7B runs on a free Kaggle T4 x2 (`rl-web-judge-kaggle.yml`). Per judged
episode it makes 1 key-points call, 1 call per screenshot (about steps + 2), and 1 outcome call. Blocked and
infra-error episodes are not sent to the judge.

These figures are estimates; no teacher-sized judge run has been timed:
- At about 2–4 s per call on vLLM, a 15-screenshot episode takes about 0.5–1 min.
- 300 episodes (about 180 judged) take about 1.5–3 h of the about 30 h/week Kaggle GPU quota.
- 1,000 episodes (about 600 judged) take about 5–10 h. That may need two judge runs to stay under the 12 h
  session limit; use `N_TASKS`, or split the merged artifact.

**GitHub Actions ($0).** This is a public repo on standard runners.
- Harness time is about 1–1.5 min per reaching episode.
- 300 episodes over 10 shards × 2 workers take about 20–40 min per shard; 1,000 take about 1–2 h per shard.
- Header streaming takes about 10 min, once; it is cached.

**Expected yield.** This is a guess. Haiku 5.5 should land between the 4B student's 22% and frontier agents'
50–60% on reachable tasks. 300 episodes might then give about 60–110 judged successes, at about 10 rows
each, so about 600–1,100 rows. 1,000 episodes would give about 3x that. Measure it on the first 300.

## 6. Launching it

Everything below needs the owner's approval of the cap first.

1. **Settings.** Edit `ci/rl_teach.env` on a launch branch:
   - `DRY_RUN=0`;
   - `MAX_USD="5"` (300 episodes) or `"10"` (1,000 episodes);
   - `N_TASKS=300` or `1000`. For 1,000 with `EXCLUDE_WEBSITES=dev`, check `eligible` in the tasks job's
     summary. If it is short, set `REPEATS=2`;
   - keep `TEACHER="anthropic:claude-haiku-5-5"`, `MODEL_LABEL="teach-haiku55"`, `SHARDS=10`, `WORKERS=2`.

   A free smoke run first is recommended: keep `DRY_RUN=1` and push. It exercises the tasks job (HF_TOKEN,
   header streaming, exclusion counts), the runners, sealing and the merge, for $0.
2. **Secrets.** These already exist: `ANTHROPIC_API_KEY` (only read when `DRY_RUN=0`), `HF_TOKEN`
   (Online-Mind2Web, for the exclusion) and `BENCH_DATA_KEY` (sealing).
3. **Push** to `bench-cloud/rl-teach-<name>`, for example `bench-cloud/rl-teach-haiku55-300`. Branch from
   `claude/web-teacher` with the env edit.
4. **Judge.** Push `bench-cloud/rl-wj7b-<name>` with these values in `ci/rl_web_judge_kaggle.env`:
   - `SOURCE_RUN=<the rl-teach run id>`, `SOURCE_ENC_RELEASE=""`;
   - `MODELS="teach-haiku55"`, `SPLIT="train"`, `N_TASKS=0`;
   - the Kaggle secrets that already exist.
5. **Rows.** Download and unseal both artifacts (`scripts/ci_unseal.sh`), then run:
   ```
   python -m scaffold.rl.train.web_teacher_data --runs <merged>/teach-haiku55__train \
       --verdicts <judge_out>/results.json --exclude Online_Mind2Web.json --out runs/rl/teacher_rows.jsonl
   ```
   Then build the dataset with `--extra-rows teacher=runs/rl/teacher_rows.jsonl` and train
   (`kaggle/rl-sft`) as usual.
6. **Evaluate** the new adapter on dev-100 (rl-web-kaggle + rl-wj7b). The comparison is against base 22% and
   run A 19%.

## Risks and open points

- **Start URLs are guesses** for site names outside the table. A failed load costs $0, but it lowers yield.
  Check `start_url_how` and the `load_failed` count after the smoke run, and add `--start-urls` entries.
- **Blocking from GitHub runner IPs** (Azure) may differ from Kaggle's. The M0 run lost 43% of tasks to bot
  walls, Cloudflare and 403s. Blocked episodes cost $0 but yield nothing.
- **Live sites changed** since Mind2Web was recorded (2023). Some tasks are no longer possible, and the judge
  filters those out.
- **The teacher is not deterministic** (no temperature). Reruns give different episodes, which is fine for
  rejection sampling.
- **The prompt-cache minimum is borderline** for Haiku 5.5. `usage.cache_read_tokens` per episode shows
  whether it applies.
- **Haiku 5.5 refusals have no server-side fallback.** A refusal ends the step as `needs_user`, and it is
  counted in `usage.refusals`.
