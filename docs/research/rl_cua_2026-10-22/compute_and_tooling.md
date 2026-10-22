# Small-model computer-use RL on $0–$200: compute and tooling (research, 2026-10-22)

Research agent, web sources. Throughput numbers are estimates: measure them on the first run.

## Bottom line

**$0 cannot do real online GRPO on 1–3k tasks.** Free T4 time is about 30 h a week; online GRPO needs roughly 100–250 T4-hours. The realistic $0 route is:
1. local llama.cpp rollouts;
2. filter the successes;
3. **rejection-sampling SFT (expert iteration)** on Kaggle or Modal;
4. repeat for 2–3 rounds.

**Online GRPO is the part worth paying for:** about $50–150 of H100 time.

## Free GPU options

| Option | What you get | Verdict |
|---|---|---|
| **Kaggle** | T4×2 or P100 (16 GB); 12 h sessions; about 30 GPU-h/week (reported, not published); internet after phone verification | **Best $0 option.** T4×2 means one GPU for rollouts, one for training |
| **Colab free** | T4, up to 12 h, ~90 min idle timeout, dynamic limits, sometimes no GPU at peak | Testing only |
| **Lightning AI free** | 15 credits/month (~80 h T4 spot, third-party claim) | Uncertain |
| **Modal Starter** | $30/month free (~7 H100-hours) | **Best free real GPU.** Check whether a card is required |
| HF ZeroGPU | 5–40 min/day | Useless for training |
| Paperspace free | 8 GB GPU | Useless |
| RunPod / Lambda | No free tier | — |
| SageMaker Studio Lab | Closed to new users | — |

**Startup credit programmes:** Google for Startups ($2k MVP tier; up to $350k if AI-first and funded), AWS Activate ($1k), Microsoft for Startups, NVIDIA Inception. Most need a registered entity (Pvt Ltd or LLP) and a website. Google's $2k tier is the most plausible.

## Tooling

- **TRL GRPOTrainer (v1.16) is the best fit.**
  - It has a native multi-turn `environment_factory`: `reset()` plus one method per tool, with the reward read from the environment state.
  - `rollout_func` gives a fully custom loop; `AsyncGRPOTrainer` is experimental.
  - vLLM in colocate mode (1 GPU) or server mode (2 GPUs, which matches Kaggle T4×2); PEFT LoRA; a BrowserGym example.
  - **Caveat:** `max_completion_length` caps tokens across all turns, so we need truncated history per step.
- **Unsloth:**
  - Qwen3.5 bf16 LoRA VRAM: about 5 GB (2B), 10 GB (4B), 22 GB (9B). They advise against QLoRA on Qwen3.5.
  - GRPO works, but not with vLLM, so generation is slow.
  - **A 9B LoRA does not fit on a T4.** The T4 also has no native bf16.
- **ART (OpenPipe):** multi-step GRPO, LoRA hot-loaded into vLLM, client/server split (client on the Mac). Qwen3.5 support unverified.
- **prime-rl + verifiers:** async multi-turn; has a 2-GPU LoRA example on Qwen3-4B.
- **SkyRL:** multi-turn on verl; LoRA described as complete in SkyRL-tx v0.2.
- **verl / rLLM / OpenRLHF / AReaL:** cluster-oriented; no good single-T4 LoRA path.
- **MLX:** `mlx-lm` for LoRA SFT plus `fuse`. `mlx-lm-lora` adds GRPO/GSPO/DAPO/DPO, but no documented multi-turn environment rollouts. **On the Mac: SFT and DPO**, or GRPO only through our own loop.
- **Qwen3.5 gotchas (single community source):**
  - LoRA targets must include the GatedDeltaNet projections (`in_proj_qkv`, `in_proj_z`, `in_proj_b`, `in_proj_a`, `out_proj`).
  - flash-attention with packing reportedly sends the loss to zero.
  - vLLM supports Qwen3.5 (GDN) from v0.19; vLLM LoRA for Qwen3.5 is unverified.

## Throughput (estimates)

Assumed episode: about 10 steps × (3k tokens in + 150 out), training on truncated per-step history.

| Setup | Throughput |
|---|---|
| T4, 4B LoRA, no vLLM | ~80–150 episodes/h (~10–20 GRPO steps/h at 8 rollouts per task) |
| M5 16 GB MLX, 4B 4-bit | ~30–60 episodes/h |
| H100 with vLLM + LoRA | ~10–20× a T4 (~1–2k episodes/h for 4B) |

- **Serving:** vLLM is 3–10× llama.cpp in aggregate under concurrency; single-stream speed is about the same.
- **SFT:** 3k trajectories ≈ 90M tokens:
  - T4: ~12–15 h per epoch;
  - H100: ~1–1.5 h per epoch;
  - deduplicating repeated observations could cut this 2–3×.

## Plans

### $0 plan (about 4–6 weeks)

1. **Rollouts:**
   - llama.cpp on the Mac (2B/4B Q4, parallel slots) plus GitHub Linux runners for environments, rewards and slow CPU rollouts.
   - Collect 2–5k successful trajectories, optionally with a stronger teacher for hard tasks.
2. **SFT:** rejection-sampling SFT on Kaggle (4B bf16 LoRA, checkpoint each session) or Modal's free credits.
3. **More rounds:** 2–3 rounds of expert iteration, optionally DPO on success/failure pairs on the Mac, optionally a small GRPO pilot on Kaggle T4×2 (~200–300 tasks × 4 rollouts, ~15–25 h).

### $50–200 plan (about 1–2 weeks)

- Free data collection as above.
- Rent an H100 ($1.99–2.72/h) or A100 80 GB ($1.2–1.8/h):
  - SFT: 4B about 2 h (~$5);
  - GRPO via TRL / ART / prime-rl on 1–2k tasks × 8 rollouts: about 8–16 h (~$20–45 for 4B);
  - **including debugging: 4B ≈ $50–100, 9B ≈ $120–200.**
- Run the environment remotely, and checkpoint to the HF Hub.

## Exporting back to GGUF

- **Adapter:** `convert_lora_to_gguf.py <peft_dir> --base <hf_base> --outtype f16`, then `llama-server --lora`. Hot-swap with `--lora-init-without-apply` and `POST /lora-adapters`.
- **Merged model:** Unsloth `save_pretrained_gguf(q4_k_m)`, or PEFT merge, then `convert_hf_to_gguf.py` and `llama-quantize`.
- **From MLX:** `mlx_lm.fuse --dequantize`, then convert.
- **Uncertain:** whether `convert_lora_to_gguf` handles the Qwen3.5 GDN tensors. If not, export a merged model.
- **Chat template:** use the exact training template and EOS token in llama.cpp; a mismatch is the main cause of quality loss.

## Sources

- gpuperhour.com/blog/free-cloud-gpus-and-credits (Kaggle)
- hivenet.com (Colab)
- usagepricing.com/blueprint/lightning-ai
- huggingface.co/docs/trl/en/openenv (TRL multi-turn)
- unsloth.ai/docs/models/qwen3.5/fine-tune
- github.com/OpenPipe/ART
- docs.primeintellect.ai/prime-rl/overview
- novasky-ai.notion.site/skyrl-tx-v02
- github.com/Goekdeniz-Guelmez/mlx-lm-lora
- huggingface.co/danielcherubini/Qwen3.5-DeltaCoder-9B
- docs.vllm.ai (gdn_linear_attn)
- deploybase.ai (llama.cpp vs vLLM)
- findstack.com (GPU rental prices)
- github.com/ggml-org/llama.cpp/discussions/10123 (LoRA → GGUF)
