# AWOS computer-use RL: design and plan (2026-10-22)

Built from `recipes.md`, `environments.md`, `mimo_v2_6.md` and `compute_and_tooling.md` in this folder.

## Goal

Make a **small local model** a reliable, **safe**, general computer-use agent **with AWOS's own interface**:
- **observation:** the accessibility tree as numbered controls;
- **action:** the constrained grammar (click, fill, select, scroll, done, needs_user);
- **safety:** pause at login/OTP/captcha; never act on pay/delete/transfer or injected instructions.

This improves every product (dashboard tasks, teach-once repair, document pack), not one workflow.

**Today's baseline (live dev_dashboard):**

| Model | Passes |
|---|---|
| Qwen3.5-4B | 14/15 |
| Qwen3.5-2B | 0/15 |
| Qwen3-4B-2507 | 0/15 |
| Qwen3.5-9B | 8/15 (out of memory on cloud Macs) |

## Decisions

### 1. Base models

- **Qwen3.5-4B:** primary.
- **Qwen3.5-2B:** the "runs on any Mac" target.
- **MiMo-V2.6-Distill-Qwen-9B** (MIT, same architecture as Qwen3.5-9B, better agentic priors and harness transfer): A/B it against Qwen3.5-9B first. It is cheap, just a GGUF swap.

### 2. Recipe (what the evidence supports)

Order: **RFT → step-DPO → GRPO**. EvoCUA reached state of the art without online RL; online RL adds the most on long-horizon tasks.

- **Warm-up:** a short behaviour-cloning step on our grammar. RL from scratch does worse.
- **Rewards:**
  - **executable verifier on the final state**, including "nothing else changed";
  - **safety penalty** for any forbidden action, or for acting on an injection;
  - **segment-level penalty** for malformed or invalid actions (MiMo);
  - a small per-step cost.
  - **Never reward a self-declared `done`.**
- **GRPO hygiene:**
  - only tasks with 0 < pass@k < 1 (dynamic filtering);
  - replay of past successes;
  - asymmetric clipping;
  - a turn curriculum (5 → 10 → 20);
  - truncated history per step.
- **Avoid forgetting:** LoRA, plus 10–20% general data mixed in.

### 3. Environments

**Train:**
- **Our app factory (the main source):**
  - generated real HTML apps in **headless Chromium**;
  - the accessibility tree via CDP/Playwright, **put through AWOS's own serialiser** so training and deployment observations are byte-identical;
  - Python state as ground truth;
  - a scripted oracle must solve each task before it is admitted;
  - checkpoints (login/OTP/captcha → correct action is pause), traps (pay/delete/transfer, decoys) and injections everywhere;
  - diversity in domain data, layout, labels, ARIA quality, themes, timing and distractors.
- **MiniWoB++** (MIT, 20% of task types held out).
- **WebShop** (MIT).
- **Our testbeds:** variants of dashboard, teach-once and doc-pack.
- **Optional:** MiMo "general" workspaces re-skinned as apps; AgentNet (MIT) converted for a warm-up.

**Evaluate only, never train on:**
- the factory's frozen out-of-domain split (unseen component families and themes);
- WebArena-Verified hard (258);
- OSWorld-Verified in a11y mode;
- MacArena's permissive subsets on free cloud Macs;
- our live testbeds on real Safari (pass^5 + unsafe count).

**Never train on** NC data (macOSWorld, MacArena's macOSWorld subset, WebLINX, OpenApps code) or on WebRL (no licence). Use closed-model teacher outputs only if the provider's terms allow it.

**Teachers:** our own models' verified rollouts plus MiMo-Distill-9B (MIT).

### 4. Compute

**$0 path:**
- **Rollouts:** llama.cpp on the M5 (parallel slots) and free GitHub Linux runners (environments + CPU rollouts with 2B).
- **Training:** LoRA SFT/DPO on **Kaggle T4×2** (~30 h/week) or **Modal's $30/month credit** (~7 H100-hours), and **MLX on the M5** for 2B/4B SFT and DPO.

**Online GRPO** is the one stage where a **small spend (~$50–100 for 4B, ~$150–200 for 9B)** buys about 10× the speed of free T4s. That is optional, and the owner decides.

**Export:** LoRA → GGUF (`convert_lora_to_gguf`, or a merged model if Qwen3.5 GDN tensors fail), hot-swapped in llama-server. Use the exact training chat template.

### 5. Measurement and gates

**Report:**
- pass@1 and pass^5 on held-out apps and benchmarks;
- **unsafe-action count (must stay 0)**;
- needs_user rate;
- steps and tokens per success;
- results by ARIA-quality and theme variant.

**Gate:** advance a stage only if **held-out** success improves with zero safety regressions.

## Milestones

| Week | Work |
|---|---|
| 1 | **App factory v0** (≥30 component types, 300+ tasks, oracle-gated, OOD split) + **rollout harness** + **baseline evaluation** (2B/4B/9B and MiMo-9B on factory OOD + MiniWoB holdout + live testbeds) |
| 2 | **Stage A:** collect 2–5k verified trajectories → LoRA SFT on 2B and 4B (Kaggle/MLX) → GGUF → re-evaluate |
| 3 | **Stage B:** step-DPO on first-wrong-step pairs + a second RFT round → re-evaluate. Add WebArena-Verified and OSWorld-a11y evaluation runs |
| 4+ | **Stage C:** GRPO pilot (Kaggle T4×2, or a small paid H100 run if approved) with a turn curriculum → full evaluation → publish if the results hold |

## Risks

- **Synthetic-to-real gap:** mitigated by real Chromium, the same serialiser, calibration against real macOS dumps, and real-app evaluation.
- **Reward hacking:** executable verifiers, red-teamed, plus side-effect checks.
- **Overfitting to factory apps:** breadth over depth; a frozen OOD split.
- **Unverified tooling for Qwen3.5:** vLLM LoRA, LoRA→GGUF for GDN tensors, and GatedDeltaNet LoRA targets all need checking.
- **Throughput** is bounded by rollouts.
- **Time:** this is a research bet, run alongside the product, not instead of it.
