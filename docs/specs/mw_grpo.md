# Online RL (GRPO) harness: mw_grpo

Status, 2026-10-24: built and tested on CPU. **Not run on a GPU yet.** This is the prep for run 6 in
`docs/research/rl_cua_2026-10-24/NEXT_WEEK_PLAN.md` (online RL go / no-go on free T4s, 2B).

## What it does

`scaffold/rl/train/grpo.py` runs GRPO with one Hugging Face model in one process. There is no
llama-server, so it fits inside a Kaggle kernel. Each iteration:

1. **Pick tasks.** It picks `tasks_per_iter` tasks from the factory **train** split. The pick is
   deterministic per (seed, iteration), so a resumed run sees the same batches. A task whose last
   group all succeeded is skipped with probability `skip_solved_p`. Pause tasks are off by default,
   because the product guard acts before the model is asked.
2. **Roll out.** It runs `group_size` rollouts per task through the product harness
   (`harness.episode.run_episode`: MicroAgent prompt, observation and parser, headless Chromium),
   sampling at temperature > 0. Every model call keeps its exact prompt and sampled token ids
   (`PolicyActionModel`). The harness is imported, not changed.
3. **Reward.** `contract.Score.reward` (+1 for a verified success, -1 for any unsafe code), minus an
   optional `step_penalty` per step (default 0, because the research says a step penalty teaches
   giving up early), minus `invalid_penalty` (0.5) once if any reply did not parse, minus an extra
   `unsafe_penalty` (default 0).
4. **Groups.**
   - Advantages are normalised within each group: (r - mean) / (std + eps). `scale_std=false` gives
     the Dr. GRPO variant, which only subtracts the mean.
   - Groups where every reward is the same are dropped (DAPO).
   - In a group where every rollout failed, the verified oracle run takes the place of one member
     (ARPO replay), and its reply is teacher-forced. An oracle run is never injected if it failed or
     was unsafe.
5. **Loss.** Token level, over the policy's own action tokens in every step of every kept rollout:
   `-min(rho*A, clip(rho, 1-0.2, 1+0.28)*A) + kl_coef * k3(pi, ref)`, averaged over all tokens.
   - The reference is the same model with the LoRA adapter disabled, so no second copy is loaded.
   - With `ppo_epochs = minibatches = 1`, pi_old = pi.detach(). The ratio is then 1, clipping does
     nothing, and the extra no-grad forward pass is skipped. With more than one epoch or minibatch,
     pi_old comes from a no-grad pass and clipping applies.
6. **Update.** AdamW on the LoRA, using the same target regex as the SFT kernel (attention,
   GatedDeltaNet projections and MLP). fp16 on T4 uses a GradScaler.

**Logging and checkpoints.**
- Each iteration appends one row to `grpo_log.jsonl`:
  - rewards: mean reward, success, unsafe and invalid rates, mean steps, kept groups, injected runs,
    infra errors (counted over the policy's own samples; injected oracle runs are left out);
  - the update: loss, pg, kl, clip fraction, grad norm, tokens;
  - speed and memory: rollouts/s, generated tokens/s, train tokens/s, s/iteration (generation and
    training separately), GPU memory (CPU RSS when there is no GPU).
- `rollouts.jsonl` holds `runner.record` rows plus iter, reward, adv, injected and kept, so it can be
  reused as SFT or DPO data.
- A checkpoint (`ck/iter_N/`: adapter, `optimizer.pt`, `state.json`) is written every
  `checkpoint_every` iterations, when `max_hours` is reached, and at the end. A restart resumes from
  the newest complete checkpoint automatically.

**Warm start.** Point `base_model` at the merged SFT checkpoint (`merge_lora.py`), so that "adapter
off" is the SFT policy. Alternatively, set `init_adapter=<SFT LoRA>` to merge it in memory before the
RL LoRA is added. The research says RL from the base model without SFT does not help
(WebAgent-R1-Zero), so the real run should start from the run-1 or run-2 SFT model.

**The model interface.** The model sits behind `Policy` (`sample`, `encode`, `token_logps`).
`train(..., generator=)` accepts a faster sampler, such as vLLM on the second T4. Weight sync for
that is not built.

## Exam integrity

- `check_trainable` refuses a task unless its split is `train`, using `dataset.check_split`, which
  fails closed on unknown splits.
- It also refuses tasks from a factory **ood family** (wizard, files_tree, calendar) and any
  **MiniWoB holdout type or family** (`miniwob/data/holdout.json`: 15 types, including
  `click-tab-2`), even when the split label says `train`.
- The check runs in `select_pool`. Explicit ood task ids raise an error; they are not filtered out
  silently. It runs again in `HarnessEnv.rollout`, in `GRPOConfig.validate` (split must be `train`),
  and in `kaggle/rl-grpo/stage.py`.
- `test_select_pool_never_contains_exam_tasks` asserts that all 99 ood ids are absent from the pool.

## How to run

```
# plan only (no model, no browser): pool size, families, rollout count, paid_api_calls: 0
python -m scaffold.rl.train.grpo --out /tmp/g --dry-run
# CPU / GPU run (needs torch + transformers>=5 + peft, and playwright + chromium)
python -m scaffold.rl.train.grpo --base-model <merged SFT dir> --out runs/rl/grpo-2b/ --set iterations=50
# speed / go-no-go on the current device
python scripts/rl/grpo_speed.py --model Qwen/Qwen3.5-2B --dtype fp16 --measure-env
# Kaggle: build the dataset (repo tarball + config) and the kernel folder; the owner pushes
python kaggle/rl-grpo/stage.py --out stage-grpo/ --user <kaggle user>
kaggle datasets create -p stage-grpo/dataset && kaggle kernels push -p stage-grpo/kernel
```

The kernel (`kaggle/rl-grpo/run.py`) does four things:
0. if `kernel.resume_from` is set, finds the newest complete `ck/iter_*/` of that earlier run first
   (`find_resume`: a path, or a slug with an optional `owner/` prefix, searched at depths 1-4 under
   `/kaggle/input`, so both `/kaggle/input/<slug>/` and `/kaggle/input/notebooks/<owner>/<slug>/` mounts
   are found). If none is found it exits before any install or probe time is spent;
1. unpacks the repo;
2. pip-installs the SFT kernel's pins (read from `kaggle/rl-sft/train.py`, which is not edited) plus
   playwright, then installs Chromium;
3. runs the speed probe (`speed.json`, the go / no-go number);
4. runs GRPO (`kernel.speed_only=true` stops after step 3).

Default config: 2B, 4 tasks x 6 rollouts, 8-step horizon, 50 iterations, fp16, `max_hours` 4.5.
Nothing calls a paid API. GPU time is the only cost, and `max_hours` caps it.

Tests: `PYTHONPATH=.:tests python -m pytest tests/rl/test_grpo.py -q`.
- The model tests need torch, transformers, peft and a Qwen3.5 tokenizer (from the HF cache or
  `AWOS_QWEN35_TOKENIZER`). The end-to-end test also needs playwright.
- No local venv has all of these. I ran them with `~/.awos/train-cpu-venv/bin/python` plus a
  `PYTHONPATH` overlay of symlinks to playwright, greenlet and pyee from `~/.awos/rl-venv`.

## Measured (CPU only, this Mac, 2026-10-24)

| What | Result |
|---|---|
| `tests/rl` (rl-venv, no torch) | 191 passed, 9 skipped after the mw-grpo-r fix (was 189); without test_grpo.py 179 passed, 7 skipped (the 2 model tests skip there) |
| `tests/rl/test_grpo.py` (torch env + playwright overlay) | 12 passed in ~3 min; after mw-grpo-r, train-cpu-venv without the overlay: 13 passed, 1 skipped (no playwright) |
| Real-harness end-to-end, tiny random Qwen3.5, `factory/assign/answer_owner/001`, 2 iterations x 2 rollouts | loss finite (0.756), LoRA changed, 1 injected oracle per iteration, 8.4 s/iteration, 0.15 rollouts/s, 12 generated tokens/s, 0.69 GB RSS |
| Kernel entry, local (`run.py --repo .`, tiny model, 2 iterations) | 415 train tasks in the pool, 18-22 s/iteration |
| `grpo_speed.py --tiny --measure-env` (CPU, fp32) | prompt 844 tokens, 40-token reply 4.8 s (8.4 tokens/s), train fwd+bwd 0.47 s, ref fwd 0.20 s, browser 0.11 s/step, so 113 rollouts/h vs 240/h needed: no-go on CPU (expected) |
| `grpo_speed.py` on the real Qwen3.5-2B, CPU bf16, 20-token replies, 1 repeat | did not finish: I killed it after 40 min wall-clock (26 min CPU) with no result, while other agents were loading the machine. CPU is not an option for 2B; the T4 number is still unknown |

Decoding is slow even for the tiny model because transformers falls back to its reference PyTorch
GatedDeltaNet and causal-conv kernels (`fla` and `causal_conv1d` are missing, and `gdn_impl=torch`
blocks `fla` on purpose for the T4). The fallback cost is per token, so it hits HF `generate` hard.
**This is the main risk for the T4 number.** The speed probe in the kernel measures it before any
training starts.

## Limits / not done

- **Not run on a GPU.** fp16 overflow, T4 memory and real rollouts/hour are all unmeasured. The
  kernel's first step measures them.
- **No grammar-constrained decoding.** HF `generate` has no GBNF. An unparseable reply becomes an
  invalid MicroAgent step and costs `invalid_penalty`. After SFT this should be rare; for a base
  model it is not. A vLLM or xgrammar generator would fix it (`generator=` hook).
- Single GPU only, with generation and training in sequence. The two-T4 split is a projection in
  `grpo_speed.py` and is not built.
- No horizon curriculum: `max_steps` is one value per run. The 0 < pass < 1 filter is approximated
  by `skip_solved_p` and the zero-variance drop; there is no pass@8 pre-filter.
- After a resume, `grpo_log.jsonl` can contain duplicate rows for iterations that ran after the last
  checkpoint and before the crash. The latest row wins.
- Rollouts run in sequence in one browser, with no worker pool.
