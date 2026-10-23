# G5 "verifier": Qwen3.5-4B checks its own result

Goal: a LoRA-SFT Qwen3.5-4B that reads the goal, the actions taken, the last page and the agent's final
reply, then answers with one line: `VERIFIED: <evidence>`, `NOT_DONE: <what is missing>` or
`UNSAFE: <what risky thing happened>`.

**Role in the product** (see `docs/research/rl_cua_2026-10-24/repair_verify.md`): the deterministic
expected-effects checks decide PASS. The verifier may only **downgrade** a pass, never upgrade a fail.
So the headline metric is the **false-VERIFIED rate**: how often the model says VERIFIED when the label
is NOT_DONE or UNSAFE. The target is about 0. We also report recall on NOT_DONE/UNSAFE, and
`verified_kept_rate`: how many true passes it leaves alone. Without that second number, always saying
NOT_DONE would look perfect.

## Files

| Path | What |
|---|---|
| `scaffold/rl/train/verifier_data.py` | episodes -> rows (`train` and `exam` subcommands), labels, evidence, balancing, metrics, keyword baseline |
| `scripts/rl/eval_verifier.py` | scores a model (llama-server GGUF, or transformers + adapter on CPU) against the baselines on any slice |
| `.github/workflows/rl-train-verifier.yml` | macOS runner: download artifacts -> build -> baselines -> stage -> Kaggle push |
| `ci/rl_train_verifier.env` | runs, slugs and train_config for push-triggered runs |
| `tests/rl/test_verifier_data.py` | labeling, balancing, split hygiene, rendering == dataset.py, baseline math |

## Data

- **One example per episode.** Uses the latest row per (task id, sample) within each file.
  - **user:** the goal, the compact actions (MicroAction text plus control label), the last `<observation>`
    block the agent saw, and the agent's final reply. The page after the last action is not recorded.
  - **Product hint removed:** the `STATUS: the goal's success condition is met ...` line is MicroAgent's own
    heuristic. It sits outside the observation block and is never copied.
- **Labels** come from the executable verifier's `score`: if `unsafe` is set the label is UNSAFE, if
  `success` it is VERIFIED, otherwise NOT_DONE.
- **Evidence** comes from fixed templates over `score.notes`, the unsafe codes and the page `Status:` line.
  No LLM writes it, and it never contains the hidden expected value.
- **Dropped:**
  - infra errors;
  - episodes with no steps (product guard or API-quota error before the first model call);
  - prompts with no observation;
  - `pause` successes, because the product never asks the verifier about a needs_user stop. `pause`
    failures stay; they are good hard negatives.
- **Balance**, biased toward failures because small judges tend to be lenient:
  - quotas of 40% VERIFIED, 48% NOT_DONE and 12% UNSAFE of `--max-train`;
  - every UNSAFE episode is written 3 times;
  - caps of 160 per (class, family) and 4 per (class, task id);
  - **hard negatives are taken first**: the agent claimed `done(...)` but the label is a failure.
- **Split hygiene:**
  - `train` refuses eval splits. It uses `dataset.check_split`, so `ood`, `holdout` or anything not
    `train` aborts the build.
  - Validation is split by task id.
  - `exam` refuses train splits and keeps the natural class mix.
  - `stage` copies only train.jsonl and valid.jsonl. `eval.jsonl` stays in the GitHub artifact.
- **Rendering** uses `dataset.Example` (`chat.render_prompt` / `render_completion`). All 3,761 local rows
  match the real Jinja template, so the template check in train.py passes.

### Counts (built locally from the same artifacts CI downloads)

Train sources: 39852748367 and 39864014413 (Qwen 4B), 39973466526 and 40003832864 (Claude Sonnet).
In 39973466526, 479 of 1,060 episodes have no steps (API usage limit) and are dropped.

| | VERIFIED | NOT_DONE | UNSAFE | total |
|---|---|---|---|---|
| episode pool (after drops, dedupe, minus val tasks) | 1,363 | 1,399 | 66 | |
| **train.jsonl** | 1,040 | 1,248 (229 hard) | 186 (62 episodes x3; 96 rows hard) | **2,474** |
| valid.jsonl (held-out task ids, natural mix) | 133 | 164 | 3 | 300 |
| **eval.jsonl** (exam run 39817774382: factory ood + miniwob holdout, 5 models) | 297 | 670 | 20 | **987** |

The eval slices are `factory_ood` 387, `miniwob_holdout` 600, `claimed_done` 293 and `product_pass` 151.
`product_pass` means MicroAgent's own effect check said "verified"; 18 of those rows are failures. All 10
eval families (calendar, files_tree, wizard, plus 7 MiniWoB tasks) are absent from training, so the whole
eval file is out of domain. valid.jsonl is the in-domain slice: unseen task ids from seen families.

Train rows are about 2.0k characters at p50 and 3.4k at p90, roughly 0.6k tokens, which is shorter than the
1.1k-token Stage-A rows.

### Trivial baselines on eval.jsonl (the bar to beat)

| rule | slice | acc | FALSE_VERIFIED | failure recall | verified kept |
|---|---|---|---|---|---|
| status contains saved/created/done | all | 0.666 | 0.107 | 0.893 | 0.205 |
| | factory_ood | 0.432 | 0.370 | 0.630 | 0.326 |
| | claimed_done | 0.406 | 0.233 | 0.767 | 0.296 |
| | product_pass | 0.391 | **0.722** | 0.278 | 0.436 |
| | miniwob_holdout | 0.817 | 0.000 | 1.000 | 0.000 |
| broader keyword list | product_pass | 0.483 | 0.889 | 0.111 | 0.549 |
| always NOT_DONE | all | 0.679 | 0.000 | 1.000 | 0.000 |

- **MiniWoB pages have no `Status:` line.** The keyword rule therefore says NOT_DONE there every time, and
  accuracy on that slice only measures the class mix (82% failures).
- **Factory pages do show their status text**, including some success-looking messages after a wrong
  action (cookie banners, "Note saved to this browser."). That is why the regex lets 37% to 72% of failures
  through.
- **What a useful verifier needs:** false_verified well below 0.1 on factory_ood, claimed_done and
  product_pass, while keeping most true passes (verified_kept above about 0.8).

## Training (Kaggle, 2x T4)

`ci/rl_train_verifier.env` sets the Kaggle slugs:

- kernel `awos-rl-sft-g5-verifier`
- dataset `awos-rl-sft-data-g5-verifier`

The staged `train_config` is:

`run_name=G5-verifier epochs=1 max_hours=2.2 eval_rows=100 eval_steps=50 eval_gen_rows=24 preflight_rows=8 save_steps=40`

**Time estimate:** 2,474 rows / 16 per optimizer step is about 155 steps. At 35 s per step (the 1.1k-token
worst case) that is about 90 min, plus about 3 min to load the model. The real rows are about half that
length, so it should take less. `max_hours=2.2` is a hard stop that saves the adapter. Kaggle's quota
resets at about 23:40 UTC.

**Launch:** `git push origin <branch>:bench-cloud/rl-train-verifier-g5`. Progress pings carry
`[G5-verifier]` on the usual ntfy topic. The in-training "action accuracy" here is an exact match of the
whole verdict line, which is stricter than label accuracy.

## Evaluation after export

1. **Export.** Run `gh workflow run rl-export.yml --ref <branch> -f mode=export -f size=4B -f name=qwen3.5-4b-verifier-g5 -f kernel=<KAGGLE_USERNAME>/awos-rl-sft-g5-verifier`.
   This produces the `rl-gguf-qwen3.5-4b-verifier-g5` artifact: a Q4_K_M GGUF, possibly in shards. Add
   `-f release=true` to also publish a pre-release.
2. **Get the data.** Download the `rl-verifier-data` artifact of the rl-train-verifier run. It has
   `verifier/eval.jsonl`, `verifier/valid.jsonl` and `baselines.json`.
3. **Serve and score.** On a CPU runner (or the laptop), start the server with the llama.cpp b11146 binaries
   as in rl-export.yml:
   ```
   llama-server -m qwen3.5-4b-verifier-g5-Q4_K_M*.gguf --port 8091 -c 4096 --jinja -np 2 &
   python scripts/rl/eval_verifier.py --rows verifier/eval.jsonl --server http://127.0.0.1:8091 \
       --workers 2 --limit 400 --out verifier_eval.json
   python scripts/rl/eval_verifier.py --rows verifier/valid.jsonl --server http://127.0.0.1:8091 --out verifier_valid.json
   ```
   - The request is exactly [system, user] with `enable_thinking=False`, temperature 0 and a GBNF grammar
     that forces `LABEL: one line`.
   - `--limit` draws a stratified seeded sample. On a GitHub CPU runner the prompt cost (about 1k tokens
     per row) makes the full 987 rows take about 1 to 2 h; 400 rows takes about 30 to 45 min.
   - The table prints each slice next to the keyword baselines. **Read false_verified on product_pass and
     claimed_done first.**
   - As a same-pipeline control, export the untouched base with `rl-export.yml mode=base` and score it the
     same way.
4. **Quick test without a GGUF:** `--hf Qwen/Qwen3.5-4B --adapter kout/adapter --limit 20` runs on CPU
   (slow, a few rows only).

To wire this into CI, add a job to rl-export.yml after the llama-server check. It would download
`rl-verifier-data` with `gh run download <id> -n rl-verifier-data`, run the command above against the
server the job already starts, and upload `verifier_eval.json`. That is not built yet; it touches
rl-export.yml, which is outside G5's paths.

## Known label noise

The executable verifier is strict about answer wording. For example, "Found 12 paid sick days" does not
contain "12 days" and is labeled NOT_DONE. About 10 rows like this exist in train. They teach strictness,
which fits the downgrade-only role.
