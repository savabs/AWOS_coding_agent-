# Stage A training: rejection-sampling SFT, cloud first

Stage A of `RL_PLAN.md`: take verified, safe successes, run LoRA SFT on Qwen3.5-2B/4B, export a
GGUF, evaluate it with the unchanged harness. The work runs **cloud first**:

| step | where | what |
|---|---|---|
| 1. data | GitHub Actions (`rl-train-data.yml`), CPU | oracle demos + rl-eval collection rollouts → `scaffold.rl.train.dataset` → SFT JSONL |
| 2. train | **Kaggle**, GPU T4 x2 (fp16) | `kaggle/rl-sft/train.py`: transformers + PEFT LoRA, loss on the reply only |
| 3. export | GitHub Actions (`rl-export.yml`), CPU | merge the LoRA into the original bf16 shards → `convert_hf_to_gguf.py` (llama.cpp **b11146**) → `llama-quantize Q4_K_M` (unsloth imatrix) → llama-server check → artifact / pre-release |
| 4. eval | GitHub Actions (`rl-eval.yml`) | same protocol as `BASELINE_PROTOCOL.md`, model pinned by URL + sha256 (needs the small change in §6) |

The laptop is the last resort (§8). Nothing here needs it.

## 1. What is trained, exactly

**Data** (`scaffold/rl/train/dataset.py`, stdlib only). It keeps an episode only if:
- the verifier says `score.success` and `score.unsafe` is empty;
- for `kind=pause`, the run really stopped (`outcome == "needs_user"`); for act/answer, it did not end in `needs_user`;
- it has no infra error. When a row is duplicated, the latest one for each (task, sample) wins.

**Eval splits are a hard error, not a filter.** A row with factory `ood`, miniwob `holdout`, or any split
other than `train` aborts the build. `kaggle_driver stage` checks every row again before upload.

Within kept episodes:
- steps that MicroAgent did not accept (`valid=false`) are dropped;
- json-format steps (no GBNF grammar) and empty replies are dropped;
- exact duplicates of (system, user, reply) are dropped across all sources.

Optional settings: `--max-per-task` (keeps the shortest episodes) and per-source mixing weights
(`--input LABEL=PATH:WEIGHT`). The validation split is chosen **by task id**.

**Prompt text = inference text.** At inference, `LocalGBNFModel` POSTs
`messages=[system, user]` with `chat_template_kwargs={"enable_thinking": false}` and a GBNF grammar.
llama-server renders that with the GGUF's Qwen3.5 template as:

```
<|im_start|>system\n{system|trim}<|im_end|>\n<|im_start|>user\n{user|trim}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n
```

The empty think block is part of the **prompt**. The model generates `reply<|im_end|>`.

`chat.render_prompt` / `render_completion` produce exactly this text. Each SFT row stores it as
`prompt` / `completion`, and `train.py` tokenises the two separately, so generation starts at a
token boundary. Loss covers the completion only.

The equality is checked at four points:
1. The tests capture LocalGBNFModel's real request body and compare it with the vendored template,
   rendered by Jinja.
2. `train.py` compares a sample of rows against `tokenizer.apply_chat_template(..., enable_thinking=False)`
   and aborts on any mismatch.
3. `serve_check` asks the exported GGUF's llama-server (`/apply-template`) to render the prompt and
   requires byte equality.
4. `serve_check` also compares `/tokenize` with the HF tokenizer's ids.

unsloth's GGUF template differs from Qwen's only in how it iterates tool-call arguments, which our
prompts never use. Oracle replies are about 6 tokens; prompts are 580–1930 tokens (p50 ≈ 1.1k).

**Model and LoRA** (`kaggle/rl-sft/train.py`):
- base model: HF `Qwen/Qwen3.5-4B@851bf6e8` / `Qwen/Qwen3.5-2B@15852e8c`. These are the post-trained
  chat models, and the `base_model` of `unsloth/Qwen3.5-{2B,4B}-GGUF`, which we evaluate. `-Base` is
  the pretrain checkpoint and is not used.
- loader: `Qwen3_5ForConditionalGeneration`, which keeps the checkpoint's key names.
- LoRA: r=16, alpha=32, dropout 0 on every projection of every language-model block:
  - full attention: `q/k/v/o_proj`;
  - GatedDeltaNet: `linear_attn.in_proj_qkv`, `in_proj_z`, `out_proj`;
  - MLP: `gate/up/down_proj`.
- not trained: the tiny per-head `in_proj_a/b` gates and the vision tower.
- optimiser: AdamW, lr 1e-4, cosine schedule with 5% warmup, 2 epochs.
- batching: one sequence per device per micro-step (no padding), grad_accum 8 × 2 GPUs = 16 sequences/step.
- gradient checkpointing is on.
- the vocabulary projection runs only on the completion positions (`logits_to_keep`). Full logits for
  248k × 1.1k tokens would be about 1 GB fp32 per sequence, and the loss masks them out anyway.

## 2. Qwen3.5 on a T4: the main risk, plainly

- **GatedDeltaNet kernels.** transformers' Qwen3.5 (checked in the 5.19/5.20 source) uses
  flash-linear-attention's Triton kernels and `causal_conv1d` if they import. Otherwise it falls back
  to its own pure-torch implementations:
  - `torch_chunk_gated_delta_rule`: chunk 64, fp32 inside, `torch.linalg.solve_triangular`;
  - a torch causal conv.

  The fallback runs on any CUDA arch, including the T4 (sm_75). By default `train.py` blocks `fla`
  (`gdn_impl: "torch"`), so the run never depends on Triton kernels tuned for Ampere+/bf16.
  Cost: the fallback is slower. Each layer does a batched `solve_triangular` per chunk plus a Python
  loop over T/64 chunks.

  **Verified:** forward and backward with PEFT LoRA on the GDN projections through this exact path,
  on CPU, on a tiny random Qwen3.5 (same class, key names and head dims).
  **Not verified:** a T4 itself, real-weight throughput, memory.
- **fp16.** The T4 has no bf16, and Qwen3.5 was trained in bf16, so fp16 activations can overflow.
  `train.py` therefore runs a preflight: it computes the base model's completion loss on validation
  rows in the training dtype and **aborts on inf/nan** before training. LoRA weights stay fp32 (PEFT
  autocast) and the Trainer uses fp16 AMP with GradScaler.

  I could not measure fp16 overflow on real weights: the MPS check on the laptop was too slow on
  battery and was stopped.
- **Fallbacks, in order:**
  1. **2B in fp32** (`--set dtype=fp32`). That is 7.6 GB of weights and fits one T4; the T4's
     ~8 TFLOPS fp32 makes it slower.
  2. **2B first** in fp16 to calibrate speed and memory (`size=2B`).
  3. **Attention + MLP only** (drop `linear_attn` from `target_regex`). This saves little compute,
     because the GDN forward and backward still run.
  4. Kaggle **P100** (`machine_shape: NvidiaTeslaP100`, fp16 slow, 16 GB) is not better.

  bf16 hardware means Colab/Modal (an L4 or A10 has bf16), which costs money.
- **Kaggle image.** `train.py` pip-installs `transformers==5.19.0 peft==0.21.2 accelerate==1.15.0`
  on top of Kaggle's torch. If Kaggle's torch is too old for transformers 5.19, the log will say so;
  pin a compatible torch in `PINS`.

## 3. Memory and time (T4 x2, estimates; calibrate with a 30-step run)

**4B fp16 memory**, per GPU (DDP keeps a full copy on each):

| part | size |
|---|---|
| weights (incl. ~0.3B vision tower) | ~9.3 GB |
| LoRA (~30M params) + AdamW states, fp32 | ~0.4 GB |
| checkpointed activations at 2k tokens | ~0.4 GB |
| one recomputed layer (the fp32 GDN chunk tensors dominate) | ~1–2 GB |
| completion-only logits | <0.1 GB |
| **total** | **~11–12.5 GB of 15 GB** |

2B fp16 is about 6 GB; 2B fp32 is about 10 GB.

**Time.** About 6·N FLOP per trained token (forward + recompute + backward; weight gradients only
for LoRA): 2.5e10 FLOP/token for 4B. Example: 5k sequences × 1.1k tokens × 2 epochs = 11M tokens,
which is 2.8e17 FLOP. At ~15 TFLOPS effective per T4 that is about 2.6 h; with the torch GDN
fallback, expect **~3–5 h for 4B** and **~1.5–2.5 h for 2B**.

Kaggle gives ~30 GPU-h/week and a 12 h session cap. `train.py` stops and saves at `max_hours`
(default 10.5).

**Calibrate first:** `-f config="max_steps=30"`. `metrics.json` reports `s_per_step`, `tokens_per_s`
and `peak_mem_gb`.

## 4. One-time setup the owner does

1. Create a Kaggle account and **verify a phone number** (Settings → Phone verification). Kaggle needs
   this to give kernels GPU and internet access, and the kernel downloads Qwen from HF and pip-installs
   transformers.
2. Create an API token: Settings → API → "Create New Token".
   - Legacy `kaggle.json` gives `username` and `key`.
   - The newer flow gives an access token (`KAGGLE_API_TOKEN`).
3. Add GitHub repository secrets (Settings → Secrets and variables → Actions):
   - `KAGGLE_USERNAME`: always;
   - `KAGGLE_KEY` (legacy key) **or** `KAGGLE_API_TOKEN` (new token).

   The workflows pass them only as step env to the `kaggle` CLI. They are never written to files or echoed.
4. (Optional) First manual check on kaggle.com after the first push: open the kernel `awos-rl-sft` and
   confirm Accelerator "GPU T4 x2" and Internet "On". The kernel metadata sets
   `machine_shape: NvidiaTeslaT4` and `enable_internet: true`. The log line `env {... "gpus": [...]}`
   shows whether there are 2 GPUs.

## 5. Commands

```bash
# 0. CPU smoke of the whole path (no GPU, no Kaggle; also runs on push to bench-cloud/rl-train-*)
gh workflow run rl-export.yml --ref <branch> -f mode=smoke

# 1. data (+ start training on Kaggle). collect_runs = rl-eval COLLECTION run ids (split=train, SAMPLES>1)
gh workflow run rl-train-data.yml --ref <branch> -f collect_runs="<id1> <id2>" -f size=4B \
   -f config="max_steps=30" -f push=true                     # calibration run first
gh workflow run rl-train-data.yml --ref <branch> -f collect_runs="<id1> <id2>" -f size=4B -f push=true

# 2. watch it: https://www.kaggle.com/code/<KAGGLE_USERNAME>/awos-rl-sft   (or locally:)
python -m scaffold.rl.train.kaggle_driver wait --kernel <user>/awos-rl-sft

# 3. export when complete (fetches the kernel output, merges, converts, quantises, checks, releases)
gh workflow run rl-export.yml --ref <branch> -f mode=export -f size=4B -f name=qwen3.5-4b-rft-v1 -f release=true
# same-pipeline baseline (untouched base through the identical export) for a fair A/B:
gh workflow run rl-export.yml --ref <branch> -f mode=base -f size=4B -f name=qwen3.5-4b-base-ours -f release=true
```

**Data for 2B.** The size only changes `base_model` in `train_config.json`; the data is the same.
Model rollouts collected with 4B are fine for 2B SFT, because they are verified successes in the
same action language.

**Hyperparameters.** Override with `-f config="epochs=1 lora_r=32 learning_rate=5e-5 dtype=fp32"`
(any key of `DEFAULTS` in `train.py`).

**What the export does.** The adapter is merged into the original bf16 shards, not via
`merge_and_unload`. The reasons:
- the Kaggle copy of the base is fp16;
- transformers does not load the checkpoint's `mtp.*` tensors, so `save_pretrained` would drop them.

Conversion uses `--no-mtp`, as unsloth's GGUFs do (block_count 24/32, no nextn layer).
Quantisation uses `llama-quantize --imatrix` with unsloth's own `imatrix_unsloth.gguf` (pinned
revision + sha256), because the evaluated unsloth Q4_K_M files carry `quantize.imatrix.*`; a plain
Q4_K_M would confound the A/B. Even so, also evaluate the `mode=base` export: it isolates the training
effect from any remaining export difference.

**Release assets** must be < 2 GiB. The 4B Q4_K_M is 2.74 GB, so `train_export.sh` also writes
`llama-gguf-split` shards (< 1900 MB). `llama-server -m <first shard>` loads all of them (tested).

## 6. Evaluating a released GGUF with rl-eval (change needed, not applied)

`ci/rl_eval.env` pins HF files. A minimal change lets a `MODEL_SPECS` line point at a GitHub release
instead. Write the repo as `gh-release:<owner>/<repo>`, the revision as the release tag, the file as
the first shard (or the single file), and the sha256 as that of the file named there:

```
qwen3.5-4b-rft-v1  gh-release:OWNER/REPO  qwen3.5-4b-rft-v1-Q4_K_M-00001-of-00002.gguf  rl-qwen3.5-4b-rft-v1  <bytes> <sha256> 3
```

```diff
--- scripts/rl/ci_matrix.py
-        if not re.fullmatch(r"[0-9a-f]{40}", rev) or not re.fullmatch(r"[0-9a-f]{64}", sha):
+        if (not repo.startswith("gh-release:") and not re.fullmatch(r"[0-9a-f]{40}", rev)) \
+                or not re.fullmatch(r"[0-9a-f]{64}", sha):
             raise SystemExit(f"{label}: revision must be a 40-hex commit and sha256 64-hex")
--- .github/workflows/rl-eval.yml   (both "Download the GGUF" steps; add GH_TOKEN: ${{ github.token }} to their env)
-          curl -fL --retry 5 -o ~/models/"$FILE" "https://huggingface.co/$REPO/resolve/$REV/$FILE"
+          case "$REPO" in
+            gh-release:*)   # all shards of a split GGUF; llama-server -m <first shard> loads the rest
+              gh release download "$REV" --repo "${REPO#gh-release:}" --dir ~/models --clobber \
+                --pattern "${FILE%-00001-of-*}*.gguf" ;;
+            *) curl -fL --retry 5 -o ~/models/"$FILE" "https://huggingface.co/$REPO/resolve/$REV/$FILE" ;;
+          esac
           echo "$SHA  $HOME/models/$FILE" | sha256sum -c -
```

The cache `path:` in the `model` job and the restore step would also need `~/models/` (or a glob),
so that the other shards are cached as well. Alternatively, keep caching only the first file and let
the run job's cache-miss path download all shards. `gh release download` works for a private repo
with the job token (`contents: read`).

## 7. What has been verified (2026-10-23)

- **Unit tests** (stdlib-only parts also run in the harness venv):
  - `tests/rl/test_train_dataset.py` (22): filters, eval-split hard error, invalid/grammarless steps,
    dedupe, task-level validation split, weights, cap, LocalGBNFModel body == training messages,
    Jinja template equality, real oracle-demo fixture;
  - `tests/rl/test_train_kaggle.py` (8): staging, eval rows refused, dry-run push/wait/fetch without
    credentials;
  - `tests/rl/test_train_export.py` (2, needs torch/transformers 5/peft): completion-only loss ==
    full-logit masked CE; `merge_lora` == peft `merge_and_unload` on all 25 targets incl. GDN; other
    tensors bit-equal.
- **`scripts/rl/train_smoke.sh` on the laptop CPU, about 30 s in total**, with a tiny random Qwen3.5:
  - 10 committed oracle episodes → dataset → `kaggle_driver stage` + `push --dry-run`;
  - `train.py` (CPU, fp32, 3 steps, LoRA on the GDN, attention and MLP projections, torch GDN path);
  - `merge_lora` → `convert_hf_to_gguf.py` b11146 `--no-mtp` → `llama-quantize Q4_K_M` →
    Homebrew `llama-server` b11146 loads it;
  - `/apply-template` == training prompt, `/tokenize` == HF ids, and the grammar-constrained reply
    parses with `parse_text_action`, for 3 steps.

  Split shards also load.
- **Not yet run:**
  - anything on Kaggle;
  - real-weight 2B/4B training or export in Actions;
  - a 4B GGUF evaluation.

## 8. Laptop (last resort)

The same scripts run locally:
- `train_smoke.sh` with `TRAIN_PY` = a venv with torch + transformers 5.19 + peft;
- `train_export.sh` with `PY` = a venv with llama.cpp's `requirements-convert_hf_to_gguf.txt`, and
  `LLAMA_SRC` = the b11146 source tarball;
- `QUANTIZE` / `SERVER` / `GGUF_SPLIT` = `/opt/homebrew/bin/llama-*` (b11146).

Respect the power rule: `pmset -g batt`, and only the tiny smoke on battery.

Notes from the abandoned MLX attempt (mlx 0.32.4, mlx-lm 0.32.2):
- mlx-lm loads Qwen3.5 and can attach LoRA to the GDN projections. In training mode, however, it
  runs GatedDeltaNet as a per-token Python loop. Measured, one layer at T=1024, forward + backward:
  2B 3.9 s / 3.8 GB; 4B 29 s / 7 GB.
- A chunked (WY) rewrite in mlx ops fixed the speed (4B layer 0.4 s / 1 GB) and matched the
  reference values and gradients. Its first version used a Neumann-product inverse, which overflowed
  to NaN on real activations; a blocked forward substitution was correct.
- mlx-lm's own `fuse` must not feed `convert_hf_to_gguf`. It saves the mlx layout: RMSNorm weights
  +1, conv1d transposed, no MTP.
