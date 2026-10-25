# Online-Mind2Web on a free Kaggle GPU

Status: built and unit-tested, not launched yet (2026-10-26).

Code: `kaggle/web-eval/` (kernel `run.py`, `stage.py`, `kernel-metadata.json`), `.github/workflows/rl-web-kaggle.yml`,
`ci/rl_web_kaggle.env`, and a `--base-url` option in `scaffold/rl/web/runner.py`. Tests:
`tests/rl/test_web_kaggle.py`.

## Why

The first Online-Mind2Web baseline (rl-web run 40209008622, `docs/specs/web_harness.md`) served the models with
llama-server on 4-vCPU GitHub runners. A 4B step took 100-150 s there, so most tasks hit the 15-minute cap after
2-4 steps, while real tasks need 10-20. A T4 runs the same 4B Q4_K_M step in about 1-3 s.

The harness, the caps (25 steps, 900 s per task), the server flags and the output layout are the same as in
`rl-web.yml`. Only the place where the model runs changes.

## What runs where

**CI, launch job** (`rl-web-kaggle.yml`, ubuntu-latest, no GPU):

1. Read `ci/rl_web_kaggle.env` through `kaggle/web-eval/stage.py settings`.
   - `LLAMA_TAG` and `MODEL_SPECS` come from `ci/rl_eval.env`.
   - `CTX_PER_SLOT` comes from `ci/rl_web.env`.
2. Refuse to launch if the kernel is still `running` or `queued`.
3. Download `Online_Mind2Web.json` with the `HF_TOKEN` secret. Only its task count is printed.
4. Download each model's GGUF, all shards for `gh-release:` models, and check the sha256 the same way as `rl-eval.yml`.
5. Stage with `stage.py stage`. This builds a **private** dataset `<user>/<DATASET_SLUG>` and the kernel
   `<user>/<KERNEL_SLUG>`. The kernel has GPU T4 x2 and internet on, and is private.
6. Push with `kaggle_driver.push`. This creates or versions the dataset, waits until it is ready (up to 1 h for
   several GB of GGUFs), and then pushes the kernel. A push that Kaggle refuses fails the job.
7. Delete the task file from the runner.

**Kaggle kernel** (`kaggle/web-eval/run.py`):

1. Unpack `scaffold/`.
2. Install `playwright==1.64.0` and run `python -m playwright install --with-deps chromium`.
3. Get a CUDA llama-server (see below).
4. For each model, start one llama-server per GPU:
   - pinned with `CUDA_VISIBLE_DEVICES=<i>`, listening on `127.0.0.1:808<i>`;
   - flags `-ngl 99 -c CTX_PER_SLOT*slots -np slots --jinja --reasoning off --cache-prompt --temp 0 --metrics`.
5. Send a 1-token smoke request to each server.
6. Run the harness:
   `python -m scaffold.rl.web run --model local --split <SPLIT> --workers <WORKERS> --max-steps 25 --max-seconds 900
   --base-url http://127.0.0.1:8080/v1 --base-url http://127.0.0.1:8081/v1`. Worker `i` talks to server `i mod 2`.
7. Send progress pings to the ntfy topic of `kaggle/rl-sft/train.py`, labelled `[RUN_NAME]`. The pings carry counts
   and timings only, never task text.
8. Stop the harness at `DEADLINE_HOURS` (11 h). Kaggle ends a session at 12 h, and the harness can resume.

Kernel output in `/kaggle/working`:

| Path | Contents |
|------|----------|
| `<model>__web-<split>/` | `trajectories.jsonl`, `judge/<task_id>/` (result.json + trajectory/*.png), `run.json`, `summary.json`, `summary.md`, `harness.log`, `validate.txt`, `llama-server-gpu<i>.log` |
| `summary.json`, `summary.md` | all models, plus the llama.cpp source that was used |
| `env.json` | driver CUDA version, GPUs |
| `llama-cuda-<tag>-sm75.tar.gz` | only after a source build |

The task file is never copied to the output.

**CI, watch job:**

1. Wait for the kernel with `kaggle_driver wait`, for up to 320 min, printing the latest ntfy ping each minute.
2. Fetch the output with `stage.py collect`. This also deletes any stray copy of the task file.
3. Upload one artifact per model, **`rl-web-merged-<model>-<split>`**. It contains `<model>__web-<split>/`, the same
   folder `rl-web.yml` puts under `merged/`.
4. Upload `rl-web-kaggle-summary` (the summaries, logs and env, with no judge folders).

## Launch

1. Edit `ci/rl_web_kaggle.env`. The settings are `MODELS` (labels from `ci/rl_eval.env`, at most 4), `SPLIT`
   (dev|all), `WORKERS`, `GPUS`, `MAX_STEPS`, `MAX_SECONDS`, `KERNEL_SLUG`, `DATASET_SLUG`, `RUN_NAME`,
   `LLAMA_BUILD_SOURCE` and `DEADLINE_HOURS`. The file holds no secrets.
2. Push to a branch named `bench-cloud/rl-web-kaggle-<anything>`.
   - `bench-cloud/rl-**` is excluded from the paid ladder (`ladder.yml` line 7).
   - `rl-web.yml` now excludes `rl-web-kaggle-**` and `rl-web-judge-**`, so the CPU run does not start too.
3. Secrets: `HF_TOKEN`, `KAGGLE_USERNAME`, and `KAGGLE_KEY` or `KAGGLE_API_TOKEN`. A new-style token stored in
   `KAGGLE_KEY` is moved to `KAGGLE_API_TOKEN` by `kaggle_driver`.
4. If the watch job times out (GitHub jobs last at most 6 h), go to Actions -> rl-web-kaggle -> Run workflow with
   `watch_only` (the default). This only waits and fetches.

To judge the results:

- **o4-mini:** in `ci/rl_web_judge.env`, set
  `RUNS="A=<watch run id>:rl-web-merged-qwen3.5-4b-* B=<watch run id>:rl-web-merged-a-final-*"`, then push
  `bench-cloud/rl-web-judge-*`. Use one glob per model. The default `rl-web-*` mixes the models of a run, and the
  judge rejects duplicate task ids.
- **WebJudge-7B:** download those artifacts, then run
  `python kaggle/web-judge/stage.py --runs A=<dir> B=<dir> ...`.

Locally, without a GPU:

```
python kaggle/web-eval/stage.py settings
python kaggle/web-eval/run.py --src . --inputs <staged dataset dir> --out /tmp/we --dry-run
```

The dry run prints the server and harness commands and starts nothing.

## llama.cpp with CUDA

The official `b11146` release ships `llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz`, plus
`cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz` with libcudart and cuBLAS. This was checked with
`gh release view` on 2026-10-26. That release is built with the default arch list in
`ggml/src/ggml-cuda/CMakeLists.txt`, which has `75-virtual`: Turing is covered by PTX only, and the driver JIT
compiles it at load time. That needs a driver that supports CUDA >= 12.8.

The kernel tries these in order, and keeps the first that starts and answers the smoke request:

1. **attached:** a `llama-cuda-<tag>-sm75.tar.gz` from an earlier run, set with
   `LLAMA_BUILD_SOURCE=<user>/<kernel>`. The workflow adds that kernel as a kernel source.
2. **prebuilt:** the release binary above, only if `nvidia-smi` reports CUDA >= 12.8. The first load can spend a
   few minutes on PTX JIT.
3. **source:** `git clone --branch b11146`, then
   `cmake -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DBUILD_SHARED_LIBS=OFF` and build only `llama-server`.
   Expect about 15-25 min on the 4 vCPUs. The binary is saved as `llama-cuda-b11146-sm75.tar.gz` in the output.
   To skip the build next time, set `KERNEL_SLUG` to a new name (for example `awos-web-eval-2`) and
   `LLAMA_BUILD_SOURCE=<user>/awos-web-eval`. A kernel cannot attach its own output, and a re-run replaces it.

`summary.json` field `llama` records which of the three was used.

## Time and GPU-hours (estimate, before the first run)

Assumptions:

- 4B Q4_K_M on one T4, prompts of at most about 6k tokens (the harness clips pages), about 30 output tokens.
- Prefill runs at about 1.5-2.5k tok/s, shared by 2 slots, and decode at about 50 tok/s.
- That gives **about 1-3 s of model time per step**.
- The browser adds about 2-5 s per step (load, settle, screenshot), so a step takes **about 4-8 s** of wall time.

Per episode:

- Easy tasks need about 4 reference steps and hard tasks about 12. A small model that answers or reaches the cap
  averages about 10 steps.
- Blocked start pages stop in seconds.
- That is about 1-1.5 min per episode. The worst case is 25 steps x 8 s = about 3.5 min, under the 900 s cap.

| | per model, 100 dev tasks | 2 models (baseline) | all 300 tasks, 1 model |
|---|---|---|---|
| Harness wall time (4 workers) | 25-60 min | 50-120 min | 1.3-3 h |
| Kernel setup (pip, Chromium, llama.cpp; + about 20 min for a source build) | 5-10 min once | 5-10 min | 5-10 min |
| Kaggle session time (charged to the about 30 h/week GPU quota) | about 0.5-1.1 h | **about 1-2.2 h** | about 1.5-3.2 h |
| T4 device-hours (2 GPUs) | about 1-2.2 | about 2-4.4 | about 3-6.4 |
| GitHub minutes (launch about 15-30 min: downloads + dataset upload; watch = kernel time) | | about 1.5-2.5 h, free tier | |

For comparison, the CPU baseline spent 100-150 s per step, and 12 runners per model hit the cap.

If steps are slower than this estimate, look at `model_ms_per_step` in `summary.json` (model time only) against
`seconds_per_episode`. With 4 vCPUs, Chromium may be the bottleneck rather than the GPU. Do not raise `WORKERS`
much beyond 4-6.

## Limits

- **Bot walls.** Kaggle sessions run on cloud datacenter IPs, like GitHub runners. Commercial sites will still serve
  CAPTCHAs, Cloudflare interstitials and 403/429 to part of the tasks. They are detected (`scaffold/rl/web/blocked.py`),
  reported as `blocked` with kind and evidence in `summary.json`, and counted as failures. They are never bypassed:
  no CAPTCHA solving, no proxies, no stealth patches. Compare the blocked share with the GitHub run's share before
  reading success rates.
- **Concurrency and quota.**
  - Kaggle allows 2 GPU sessions at a time across the account (rl-sft, rl-grpo and web-judge all count), and
    `kaggle_driver.push` fails loudly when Kaggle refuses.
  - The weekly GPU quota is about 30 h.
  - A session lasts at most 12 h, and the kernel stops the harness at `DEADLINE_HOURS`.
  - Kernels with internet need a phone-verified account.
- **One kernel per launch.** The models run one after the other, not one per GPU. Both GPUs serve the same model,
  which keeps a model's run in one folder with one `run.json`.
- **The task file.** It lives in a private Kaggle dataset (the dataset is CC-BY-4.0 but gated on HF; the HF token
  itself never goes to Kaggle). It is never printed, never committed (`.gitignore`), never in the kernel output,
  and `collect` deletes any stray copy before the upload. Every dataset version stays private. Delete the dataset
  if the account is ever shared.
- **Upload size.** Each launch uploads every GGUF again as a new dataset version (about 4.6 GB for the baseline).
- **Output size.** About 15 PNGs per episode. Expect a few hundred MB per model per 100 tasks, well under
  Kaggle's 20 GB output limit.

## Not tested

- Nothing has run on Kaggle or in this workflow on GitHub. The workflow passed actionlint (without shellcheck).
- The prebuilt CUDA 12.8 binary on Kaggle's T4 driver: whether the driver is >= 12.8, and how long the PTX JIT takes.
  The source-build fallback, its cmake flags against b11146, and its build time.
- The real step time and episode time on a T4. The table above is an estimate.
- Two servers sharing 4 vCPUs with 4 Chromium workers, and GPU memory with `-np 2 -c 24576` (it should fit easily:
  a 2.7 GB model on a 16 GB T4).
- The dataset upload and processing time for several GB, and Kaggle's handling of the sharded `a-final` GGUF.
- `kaggle kernels output` layout of nested folders, and the judge reading the `rl-web-merged-<model>-<split>`
  artifacts.

What is tested: the settings, the staged files and metadata, the server and harness command lines, the dry run,
the CLI and `kaggle_driver.push` dry run, `collect`, the workflow wiring, and that the task file is never printed
or tracked. The new `--base-url` was run end to end: the real CLI with 2 workers, 2 fake llama-servers and
headless Chromium on a local site, and both servers served steps.
