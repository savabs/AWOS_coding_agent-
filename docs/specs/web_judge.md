# WebJudge for Online-Mind2Web: layout contract, judge, costs

Status: built 2026-10-25 on branch `claude/web-judge`. No paid call was made during development.
Code: `scaffold/rl/web_judge/` (port), `scripts/web/judge.py` (CLI), `kaggle/web-judge/` (WebJudge-7B on a
free Kaggle GPU), `.github/workflows/rl-web-judge.yml` + `ci/rl_web_judge.env` (o4-mini in CI).

## Why

We publish an Online-Mind2Web success rate next to Fara1.5-4B (57.3%) and MolmoWeb-8B (35.3%). Those numbers
come from WebJudge, the benchmark's automatic judge (o4-mini: 85.7% agreement with humans; WebJudge-7B:
87.0%). So we use the judge itself, with its prompts and rules unchanged, and record every deviation.

Upstream: https://github.com/OSU-NLP-Group/Online-Mind2Web @ `f0d805ee0e9e0b3ea70911e45e5264b72968f3dc`
(2026-05-28), MIT. The licence and attribution are in `scaffold/rl/web_judge/LICENSE` and `NOTICE`.

## Important: o4-mini has been shut down

OpenAI's deprecation page (https://developers.openai.com/api/docs/deprecations, read 2026-10-25) lists
`o4-mini` and `o4-mini-2025-04-16` for shutdown on **2026-10-23**, with `gpt-5.6-terra` as the replacement.
The pricing page still lists the model. If the API now refuses it, the o4mini backend stops at the first
call (fatal `model_not_found`, exit 2) and spends nothing more. That leaves two options:

* **WebJudge-7B** (`osunlp/WebJudge-7B`, the open model from the benchmark authors). It is the only judge
  left that matches published methods, and its reported human agreement (87.0%) is higher than o4-mini's.
  Run it with `kaggle/web-judge`. Label results "WebJudge-7B".
* Another OpenAI model (`--model`, with a price added to `cost.py`). That is a different judge, so its
  numbers are not comparable with the leaderboard. Report them as such, or don't report them.

## Layout contract (the official submission layout)

The harness (branch `claude/web-harness`) writes one directory per episode. `find_episodes` walks a run
directory recursively, so CI artifact nesting is fine. Task ids must be unique within a run.

```
<run>/.../<task_id>/
    result.json
    trajectory/
        0_full_screenshot.png  1_full_screenshot.png ...      (v1: ordered by the first integer in the name)
```

`result.json`, v1 (what `data/example/example_v1` uses):

| field | type | used by the judge |
|---|---|---|
| `task_id` | str | id (falls back to the directory name) |
| `task` | str | yes: every prompt |
| `final_result_response` | str | **no**: WebJudge never shows the agent's answer to the judge, only screenshots + actions |
| `action_history` | [str] | yes: numbered in the outcome prompt. Facts only (e.g. `<button> -> CLICK`). Upstream README: "do not include the final response or any other agent's outputs". |
| `thoughts` | [str] | no (other upstream modes only) |

v2 (`"schema_version": "online-mind2web-v2"`, upstream `data/schema_v2`) is also read: the
`action_history[*].action` strings in step order, screenshots from `action_history[*].screenshot`. Steps with
a null screenshot are skipped. `agent_final_answer` is not shown to the judge. Note that v2's terminal action
`TASK_COMPLETE -> ANSWER: ...` *is* in the action history, so in v2 the judge sees the answer text. That is
upstream behaviour.

AWOS fields, all optional. The official judge ignores them.

| field | values | effect |
|---|---|---|
| `level` | `easy` / `medium` / `hard` | per-level rates (or pass `--levels` with the Online-Mind2Web task file) |
| `harness_status` | `ok` (default) / `blocked` / `infra_error` | blocked = CAPTCHA, bot wall, or site refusing the agent; infra_error = our browser or harness failed. Aliases are accepted: captcha, bot_wall, login_wall → blocked; infra, infra_failed, error, crash → infra_error. Anything else is a layout error. |
| `harness_note` | str | copied into the verdict |

Screenshots: PNG, JPEG or WebP. Viewport screenshots (e.g. 1280×720) keep costs down; full-page screenshots
are allowed. The judge re-encodes each one as JPEG, as upstream does.

## Judge (unchanged from upstream)

Per episode, `1 + N + 1` calls (N = screenshots):

1. Key points: `KEY_POINTS_SYSTEM` + `Task: {task}`. Take the text after `**Key Points**:` (otherwise after
   the last `Key Points:`), collapse blank lines, left-strip each line.
2. One call per screenshot: `IMAGE_SYSTEM` + task + key points + the JPEG at `detail: high`. Score = first
   `[1-5]` after the first `Score` (0 if unparsable). Key screenshot = score ≥ 3. Its reason = the first
   paragraph after `**Reasoning**:`. At most 50 key screenshots, in trajectory order.
3. Outcome: `OUTCOME_SYSTEM` + task + key points + numbered action history + numbered reasons + the key
   screenshots. When no screenshot is key, the prompt variant without reasons is used.
   Success = `"success"` in the lower-cased text after the first `status:`; 0 otherwise.

Run success rate (upstream) = successes / all task directories.

Faithfulness check, done on 2026-10-25 and not in CI because it needs Pillow and the upstream source: on the
upstream v1 example task with real Pillow encoding and a deterministic fake model, upstream
`WebJudge_Online_Mind2Web_eval` and our `judge_episode` produced identical final messages (images
included), identical input text, image records, key points and label, at thresholds 1, 3, 5 and 6 (the
no-image branch). The prompts are checked against upstream sha256 values in `tests/rl/test_web_judge.py`.

### Deviations (also in NOTICE)

1. **o4-mini parameters**: `max_completion_tokens=8192` and no `temperature`, because the API refuses
   upstream's `max_tokens=512, temperature=0` for o-series models. Reasoning effort is the default. The
   WebJudge-7B request is upstream's.
2. **Errors**: after 3 tries, the episode is recorded as `judge_error` and the run continues. Upstream lets the
   worker die. A fatal error (bad key, model shut down, no quota) stops the run.
3. **Screenshot listing**: non-image files are ignored, and a v2 step with a null screenshot is skipped.
   Upstream crashes on both.
4. **JPEG modes**: every mode other than RGB/L is converted to RGB. Upstream converts only RGBA and crashes on
   P/LA/CMYK.
5. **Harness status**: `blocked` / `infra_error` episodes count as failures, as upstream counts them, but by
   default they are not sent to the judge (`--judge-blocked` sends them) and are reported separately.
6. **Cost cap**: see below. Upstream has none.

Kept as upstream, quirks included: the key screenshots are labelled `data:image/png` although they hold
JPEG bytes; a key screenshot with an empty reason still contributes its image; and the reason is the whole
first paragraph when the model does not bold `**Reasoning**:`.

## Results file (`--out results.json`)

```
{"judge": {upstream, upstream_commit, backend_name, model, score_threshold, ...},
 "cost": {max_usd, spent_usd, calls, prompt_tokens, completion_tokens, cached_tokens, refused_calls},
 "stopped": null | "cap: ..." | "fatal: ...",
 "estimate": {...dry-run numbers...},
 "runs": {LABEL: {"summary": {...}, "tasks": [verdict, ...]}},
 "comparisons": [{a, b, n_paired, a_only_success, b_only_success, both_success, both_fail,
                  rate_a, rate_b, diff_b_minus_a, mcnemar_exact_p}]}
```

Verdict `status`: `judged`, `blocked`, `infra_error` (label 0, not judged), `judge_error`, or `not_judged`
(cap or fatal). Each verdict keeps the key points, the per-screenshot responses and scores, the key
screenshot names, the outcome prompt text, the response and `predicted_label`.

Summary: counts of each status, `complete` (no judge_error or not_judged), and two rates, each with a
95% Wilson CI, overall and by level:

* **official** = successes / (judged + blocked + infra_error). This is the number to publish, and only when
  `complete` is true and the run covers all 300 tasks.
* **executable** = successes / judged episodes with harness_status ok. It is the agent's rate when the site
  let it work. It is not comparable with the leaderboard.

A/B: tasks scored in both runs are paired (official labels), and the exact two-sided McNemar p-value is
computed from the discordant pairs.

`<out>.verdicts.jsonl` collects verdicts as they arrive. Running again with the same backend, model,
threshold and max tokens resumes from it (`--fresh` starts over).

## Cost and cap (o4-mini)

Prices (https://developers.openai.com/api/docs/pricing, checked 2026-10-25), per 1M tokens: Standard
$1.10 in / $0.275 cached / $4.40 out; Flex $0.55 / $0.138 / $2.20. Reasoning tokens are billed as output.
Images: 32 px patches × 1.72, rounded up. The estimate shrinks images to 1536 patches (the budget from
earlier versions of the docs; the current page states none for o4-mini). A 1280×720 screenshot is 1,583
tokens.

Dry-run estimate for 300 tasks (`--backend dry`; synthetic runs, 4 chars per token, expected output
500 / 700 / 1500 tokens per key-point / screenshot / outcome call including reasoning, 37% key screenshots
as in the published o4-mini records):

| screenshots per task | size | calls | Standard | Flex |
|---|---|---|---|---|
| 10 | 1280×720 | 3,600 | ~$22 | ~$11 |
| 15 | 1280×720 | 5,100 | ~$31 | ~$15 |
| 25 | 1280×720 | 8,100 | ~$49 | ~$24 |
| 15 | 1280×1100 | 5,100 | ~$37 | ~$18 |

So the default `MAX_USD=12` covers roughly 115 tasks at Standard prices (15 screenshots per task), or about
230 at Flex, and then stops with partial results. Raise `MAX_USD`, or use `SERVICE_TIER=flex`, for a full
run. Reasoning length is the main unknown: about half of the estimate is the output allowance.

The cap works per call. Before each request it reserves the worst case: estimated input × 1.25, plus the
whole `max_completion_tokens`. The request is sent only if spent + reserved + this call ≤ cap. After the
call, the reservation is replaced by the cost billed from `usage`. A failed request is charged at its
worst case. Hitting the cap stops new work: episodes not finished are `not_judged`, results are written, and
the exit code is 3.

## Backends

| `--backend` | what | request |
|---|---|---|
| `dry` | no call; estimate only (also `--dry-run`) | none |
| `o4mini` | `https://api.openai.com/v1/chat/completions`, key from `OPENAI_API_KEY` (never printed) | `max_completion_tokens`, optional `service_tier=flex` |
| `webjudge7b` | any OpenAI-compatible server (`--base-url`, default `$WEBJUDGE_BASE_URL` or `http://127.0.0.1:8000/v1`) | `max_tokens=512, temperature=0` (upstream) |

WebJudge-7B is `osunlp/WebJudge-7B`, revision `bb550f8e301855933db5490eb8128217b3aca5dc` (Qwen2.5-VL-7B
fine-tune, Apache-2.0). The model card serves it with `vllm serve osunlp/WebJudge-7B`, so the Kaggle kernel
uses vLLM: pinned `vllm==0.31.0`, fp16 (T4 has no bf16), tensor parallel 2, `--max-model-len 65536`,
`--limit-mm-per-prompt {"image": 50}`, and the model's own image preprocessing (`mm_max_pixels` null).

```
python kaggle/web-judge/stage.py --out stage-wj --runs A=runs/web/A B=runs/web/B [--levels tasks.json]
kaggle datasets create -p stage-wj/dataset --dir-mode tar
kaggle kernels push -p stage-wj/kernel           # outputs: results.json, vllm.log, judge.log, env.json
```

## CI

`.github/workflows/rl-web-judge.yml` runs on a push to `bench-cloud/rl-web-judge-**`. Settings are in
`ci/rl_web_judge.env`: `RUNS` (rl-web run ids, `LABEL=ID[:ARTIFACT_GLOB]`), `BACKEND` (o4mini | dry),
`MAX_USD` (12), `SERVICE_TIER`, `MAX_OUTPUT_TOKENS`, `LEVELS_URL`. The workflow:

1. downloads each run's artifacts;
2. prints the dry-run estimate in the step summary;
3. judges with o4-mini under the cap, using the `OPENAI_API_KEY` secret;
4. uploads `judge/` (results.json, verdicts.jsonl, logs).

A cap stop is a warning. A fatal backend error fails the job. `bench-cloud/rl-**` is excluded from the paid
ladder workflow (`.github/workflows/ladder.yml` line 7: `"!bench-cloud/rl-**"`).

## Not tested

* No real o4-mini or WebJudge-7B call was made. Until the first real run, the following are unverified:
  request acceptance, usage accounting against a real bill, flex availability, and whether o4-mini is still
  served after 2026-10-23.
* vLLM 0.31 on Kaggle T4s (sm_75, fp16, TP 2) with Qwen2.5-VL, and memory for 50-image prompts at the
  model's default max_pixels. If it fails, set `max_model_len` / `mm_max_pixels` / `vllm_pin` via
  `stage.py --set`. Note that `mm_max_pixels` changes the judge's input relative to upstream.
* The workflow has passed actionlint but has never run on GitHub. The artifact names of the rl-web
  harness are not known yet (`ARTIFACT_PATTERN` defaults to `rl-web-*`).
