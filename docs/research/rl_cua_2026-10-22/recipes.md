# Open recipes for training small computer-use agents (research, 2026-10-22)

Research agent. (v) = checked against a primary source; the rest is from prior knowledge, so verify before citing.

## Key works

| Work | Base model | Observation | Method | Headline result | Release / licence |
|---|---|---|---|---|---|
| **WebRL** (2411.02337) | Llama-3.1-8B, GLM-4-9B | **text** | Online RL, self-evolving curriculum, outcome reward model | WebArena-Lite **4.8→42.4% (v)** | **No licence: don't use** |
| **WebAgent-R1** (2505.16421) | Qwen2.5-3B, Llama-8B | **text** | Behaviour-cloning warm-up → multi-turn GRPO with binary reward | **3B 6.1→33.9%, 8B 8.5→44.8% (v)**; the warm-up matters | Amazon |
| AgentGym-RL (2509.08755) | Qwen2.5-3B/7B | text | Multi-turn RL with a growing turn budget | 7B ~26% WebArena | GitHub |
| **Go-Browse** (2506.03533) | Qwen2.5-7B | text | Exploration → SFT on successes (RFT) | WebArena 21.7% (+13.4) from 10K trajectories; **weak out-of-domain (5.3%)** (v) | CC-BY |
| NNetNav, AutoWebGLM, Synatra | 6–8B | text | Exploration, curriculum SFT/DPO/RFT, synthetic demos | moderate | mixed |
| AgentTrek, OS-Genesis, Explorer | ~7B VL | screenshot + a11y | Tutorial replay, reverse task synthesis, synthetic trajectories | SFT gains | check |
| UI-TARS / 1.5 / 2 | Qwen2-VL 2–72B | screenshot | SFT, DPO, multi-turn RL | UI-TARS-2: OSWorld 47.5; RL adds +10.5 (v) | 1.5 Apache; 2 closed |
| **OpenCUA** | Qwen2.5-VL 7–72B | screenshot | SFT on AgentNet (22.6K tasks incl. macOS) | 7B 27.9 OSWorld-Verified (v) | **MIT (v)** |
| **EvoCUA** (2601.15876, Jan 2026) | Qwen3-VL 8B/32B | screenshot | Cold start → **RFT** → **step-level DPO**, iterated, no online RL; 1M+ accelerator-hours | **8B 46.1, 32B 56.7 OSWorld (v)** | **Apache-2.0 (v)** |
| ARPO | UI-TARS-1.5-7B | screenshot | GRPO + success replay | 23.5→29.9 (v) | open |
| ZeroGUI, SEAgent | ~7B | screenshot | A VLM proposes tasks and judges them; self-evolving | Large gains on trained apps | open |
| GUI-R1 / UI-R1 / InfiGUI / GUI-G1 | Qwen2.5-VL **3B**/7B | screenshot | Single-step rule-reward GRPO | UI-R1: **136 samples** → +15% | open |
| DigiRL / DigiQ | 1.3B | screenshot | Offline-to-online RL | 17.7→67.2% on AitW | open |
| **Fara-7B** (2511.19663) | Qwen2.5-VL-7B | screenshot | SFT on 145K verified trajectories | WebVoyager 73.5% (62% by an independent rater) (v) | **MIT (v)** |

**2026 small models:** EvoCUA-8B is the strongest verified small open agent. No verified Qwen3.5-2B/4B computer-use RL paper exists.

## What the evidence says

1. **Small text-observation models work.** 3–9B text models go from about 5% to 34–45% on WebArena-Lite with a short behaviour-cloning warm-up followed by online multi-turn RL using binary verifier rewards.
   - RL from scratch does worse than starting from a warm-up.
   - What matters: a compact observation, a full fixed action grammar (+4.8 points in EvoCUA), short reasoning before each action, and a curriculum over difficulty and episode length.
2. **RFT gets most of the gain.** EvoCUA reached state of the art without online RL:
   - 32B steps: cold start +2.6, RFT +3.1, step-DPO +3.2, second iteration +1.9.
   - Online RL adds more on hard multi-turn tasks: WebRL and WebAgent-R1 roughly double what filtered behaviour cloning gets; UI-TARS-2 gains +10 from RL.
   - **Order: RFT → DPO → GRPO.**
3. **How much data:**
   - single-step GRPO: 136–3K samples;
   - multi-turn web RL: hundreds to ~1–2K tasks;
   - RFT: 1K–10K successful trajectories;
   - **out-of-domain transfer needs breadth (many apps), not depth.**
4. **Failure modes and fixes:**
   - **Overfitting to the training environments:** hold out whole apps.
   - **All-fail GRPO groups give no learning signal:** only train on tasks with 0 < pass@k < 1, and replay past successes.
   - **Stopping behaviour:** verify the final state programmatically; never reward a self-declared "done".
   - **Judge reward hacking:** use executable verifiers.
   - **Long horizons:** step curriculum, step-DPO at the first wrong step, context compression.
   - **Loops:** penalty plus a detector.
   - **Off-policy instability:** use data from recent iterations.
   - **Forgetting:** mix in 10–20% general data and use LoRA.
   - **Noisy evaluation:** average 3 runs.

## Recommended recipe for AWOS

Base model: Qwen3.5-4B (14/15 today), with 2B as the "small" target.

**Stage A: RFT and distillation (~1–2 weeks, M5 MLX LoRA)**
- 200–500 parameterised tasks across 10–20 apps/sites, each with a verifier; hold out 4–5 apps.
- 8–16 rollouts per task from 4B, plus a teacher on hard tasks.
- LoRA-SFT 2B and 4B on 2–5K trajectories.
- Expected: 2B from 0/15 to ~6–10/15; 4B +5–15 points on held-out apps.

**Stage B: step-DPO on failure pairs + a second RFT round (Kaggle/Colab or M5)**
- Pair the first wrong step with a correct alternative.
- Expected: +3–6 points.

**Stage C: online GRPO with replay, only once Stage B plateaus**
- Turn curriculum 5→10→20; only tasks with 0 < pass@8 < 1.
- Rewards: verifier minus a per-step penalty minus safety violations.
- Expected: +5–15 points on long-horizon tasks.
- Rollouts, not the GPU, are the bottleneck.

**Gate:** advance a stage only on **held-out-app** success.

## Data we can use commercially

**Usable:**
- **AgentNet / OpenCUA: MIT**. Includes macOS; screenshots only, so it would need converting to accessibility-tree text.
- **GUI-360: MIT.** Windows Office, 13.7K successes + 62K failures (good DPO pairs).
- **Fara-7B weights: MIT**, usable as a teacher.
- **EvoCUA: Apache-2.0.**
- **UI-TARS 1.5: Apache.**

**Flagged:**
- **Mind2Web:** CC-BY but "research purposes", so it needs legal review.
- **WebRL:** no licence, so don't use it.
- **Teacher trajectories from closed models** (e.g. Haiku): check the provider's terms on training with outputs.

**Safest:** our own verified rollouts plus MIT data.
