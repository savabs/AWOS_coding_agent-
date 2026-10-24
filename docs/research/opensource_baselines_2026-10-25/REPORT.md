# Open-source baselines survey (2026-10-25)

Scope: recent open-source releases AWOS could build on, judged against four gaps:
- **G1:** 23% wrong repairs when the target control is deleted, against a <=2% target.
- **G2:** slow LoRA training on Kaggle T4, because GatedDeltaNet runs on the torch fallback.
- **G3:** slow CPU/Mac inference.
- **G4:** the trained 4B gets 79.8% on unseen web apps; Claude gets 87%.

Constraint: free tiers only (Kaggle 2x T4 fp16, GitHub Actions CPU; the Mac is a last resort).

Method: web search plus primary pages (GitHub repos, HF model cards, arXiv abstracts, official blogs), read 2026-10-25. Nothing
was installed or run. **(self-reported)** = the authors' own numbers, not reproduced. **(unverified)** = only secondary
sources were found.

## 0. The DeepInfer claim: VERIFIED

DeepSeek open-sourced **DeepInfer** on 2026-10-20.
- Repo: https://github.com/deepseek-ai/DeepInfer (Apache-2.0, about 21.7k stars, 4 commits on main).
- Coverage: https://siliconangle.com/2026/10/20/deepseek-open-sources-deepinfer-the-inference-framework-behind-its-production-services/
- What it supports: offline inference for **one model only, DeepSeek-V4.1-Flash** (763B total, MIT weights).
- Hardware: NVIDIA SM100 (Blackwell) or Huawei Ascend 950 only. There is no CPU, Mac or consumer-GPU path.
- Features: DSpark speculative decoding with confidence-scheduled verification; regex / JSON-schema / EBNF constrained
  output; attention-DP and MoE-EP; batch-invariant deterministic sampling.
- **Verdict: SKIP the code.** The only idea worth reading is DSpark's confidence-scheduled verification.

Other DeepSeek releases (https://github.com/orgs/deepseek-ai/repositories):

| Release | What | License | Date / maturity | Verdict |
|---|---|---|---|---|
| [deepseek-harness (dsh)](https://github.com/deepseek-ai/deepseek-harness) | Plugin-based agent harness (TS), web UI, OpenAI-compatible local servers | MIT | developer preview; date unverified | WATCH (coding/bash only; compare its approval and sandbox model) |
| [DeepSpec](https://github.com/deepseek-ai/DeepSpec) + [DSpark](https://arxiv.org/abs/2607.05147) | Drafter training and eval | MIT | Jul 2026; 8 GPUs; no Qwen3.5 drafters | WATCH |
| deepseek-recipe | API-matching token encoding | MIT | Oct 2026 | SKIP |
| DeepGEMM / DeepEP / FlashMLA / TileKernels / DeepSelect / DeepJIT / 3FS / DualPipe | Datacenter kernels, communication, storage | MIT | Sep-Oct 2026 | SKIP |
| [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017) | 763B MoE, 1M context, vision | MIT | 2026-09-10 | SKIP locally; possible cheap cloud teacher |

## 1. Short answer: what to act on, ranked by impact

1. **T4 training with real GatedDeltaNet kernels (G2).**
   - Claim: fla `main` PR #920 (merged 2026-06-03) says its GDN tests "pass on a Tesla T4 (sm_75, IEEE fallback)" in fp16
     and fp32, and it reuses the chunk kernels Qwen3.5 uses. Unsloth's Qwen3.5 guide ships T4 notebooks, including a 4B GRPO
     one. So the "fla needs newer GPUs" blocker may be stale.
   - Experiment (about 1 GPU-h): run fla's gated-delta tests on a T4 in fp16. Then 50 LoRA steps of the 2B with fla vs the
     torch fallback, plus an Unsloth arm.
   - Pass: at least 3x faster with matching loss.
   - Links: https://github.com/fla-org/flash-linear-attention/pull/920 · https://unsloth.ai/docs/models/qwen3.5/fine-tune
2. **NOT_FOUND as trained behaviour (G1).**
   - Evidence: grounders score 0.0 on OSWorld-G refusal (GroundGUI, arXiv 2610.19100). CVPR'26 GUI-HalluBench names the
     "fabricated element" failure. No small model abstains well off the shelf.
   - Experiment: SFT with deleted-target negatives and hard decoys (about 30% of rows). Then DPO with chosen=NOT_FOUND and
     rejected=the model's own wrong pick.
   - Measure: wrong-repair rate on a held-out deleted-target set, and that correct repairs do not regress.
   - *(AWOS is already doing the SFT half: G6, launched 2026-10-24.)*
3. **RL base: TRL 1.16.0 GRPO `environment_factory` (G4, G1).**
   - The `grpo_browsergym` example defaults to **Qwen/Qwen3.5-2B**, with an AX-tree observation, bid actions and binary
     reward, on 1 GPU without vLLM.
   - Experiment: a repair env with tools `pick(id)` / `not_found()`; reward +1 right, -1 wrong pick.
   - Link: https://github.com/huggingface/trl/tree/main/examples/grpo_browsergym
4. **Microsoft Fara1.5-4B (G4, approvals).**
   - What: MIT, the same Qwen3.5-4B base. Self-reported 80.8% WebVoyager and 57.3% Online-Mind2Web. Trained to stop at
     critical points (payments, submissions, sending). Screenshot-only with coordinate actions; a research preview.
   - Experiment: try it as a baseline, a teacher, and a LoRA starting point for AX-tree training.
   - Links: https://huggingface.co/microsoft/Fara1.5-4B · https://github.com/microsoft/fara
5. **Verifier design: Universal Verifier + CUAVerifierBench.**
   - What: arXiv 2604.06240, MIT code. Self-reported kappa 0.64 vs WebJudge 0.44, false positives near zero. Built on
     rubric criteria with separate process and outcome scores.
   - Experiment: measure our verifier's kappa and false-positive rate, restructure it into rubric form, re-measure.
6. **llama.cpp prompt caching for hybrid models (G3).**
   - ADOPT NOW: Qwen3.5 reuses prompt prefixes only through context checkpoints (`--checkpoint-min-step 8192` by default;
     issue #24587 reports silent full re-prefill). Keep the prompt prefix byte-stable with the AX tree last, lower `-cms`,
     log the restored `n_past`, and update llama.cpp for the GDN Metal kernel.
   - TRY after that: A/B MTP (`--spec-type draft-mtp`, merged 2026-05-16; Qwen3.5-4B has MTP heads). Expect little, since
     AWOS emits only a few tokens per step.
7. **Deterministic candidate ranker before the model (G1, G3).**
   - Idea: Healenium-style similarity score with a high cap (skip the model) and a low cap (lean to NOT_FOUND). *(AWOS's
     repair rule tier already does this; compare thresholds.)*
   - browser-use replay still "errors at the first step it can't match", so the gap AWOS fills is still open.
8. **More training data (G4).**
   - MolmoWeb (ODC-BY): 36k human + 108k synthetic trajectories, screenshot-based.
   - WebChain: AX snapshots; licence and full release unverified.
   - Experiment: replay 2-5k MolmoWeb trajectories to capture AX trees, convert them to the AWOS grammar, then check
     unseen-app accuracy before scaling.

## 2. Per-category tables

### 2.1 Inference, speculative and constrained decoding
| Item | License | Date / maturity | Fit | Verdict |
|---|---|---|---|---|
| DeepInfer | Apache-2.0 | 2026-10-20 | SM100 / Ascend only | SKIP |
| llama.cpp context checkpoints (README, issue #24587) | MIT | reworked May 2026 | CPU, Mac | ADOPT NOW |
| llama.cpp GATED_DELTA_NET op + Metal (PR #19504 / #20361) | MIT | 2026; +25% Metal (self-reported) | Mac, CPU | ADOPT NOW (update) |
| llama.cpp MTP draft-mtp (PR #22673) | MIT | merged 2026-05-16 | CPU, Mac | TRY after the cache fix |
| llama.cpp ngram drafting | MIT | 2026 | CPU, Mac | TRY (flag A/B; replayed chores repeat) |
| z-lab DFlash Qwen3.5-4B drafter | Apache-2.0 | GPU/SGLang | GPU | WATCH |
| DSpark (arXiv 2607.05147) | paper | Jul 2026 | datacenter | WATCH (idea) |
| llguidance in llama.cpp | MIT | mature | CPU, Mac | TRY only if grammar is over 5% of step time |
| XGrammar-2 | Apache-2.0 | Jan 2026 | vLLM / SGLang | WATCH |
| MLX / mlx-lm | MIT | v0.31.x; Qwen3.5 GDN parity unverified | Mac | WATCH |

### 2.2 Small models (<=10B)
| Item | License | Notes | Verdict |
|---|---|---|---|
| Fara1.5-4B/9B (Microsoft) | MIT | same base; critical-point stops | TRY |
| Qwen3.5-2B/4B (current) | Apache-2.0 | no newer small Qwen (3.6 and 3.8 are 27B and up) | keep |
| MolmoWeb-4B/8B (Ai2) | code Apache-2.0; weights licence not checked | 8B 35.3% Online-Mind2Web | WATCH (use its data) |
| UI-Venus-2-9B | "research and educational purposes only" | claims 74.0 Online-Mind2Web | WATCH (licence blocks product use) |
| GUI-Owl-1.5 2B/4B/8B | model licence not stated | Qwen3-VL base | WATCH |
| GroundGUI-8B | not stated | refusal-aware grounding | WATCH (borrow the idea) |
| Gemma 4 E2B/E4B | Apache-2.0 | standard attention (no GDN kernel issue on T4) | TRY-lite, only if item 1 fails |
| Nemotron 3.5 Bolt E4B | not stated | announced 2026-10-19 | WATCH |
| Qwen3Guard-Stream 0.6B/4B | not checked | possible backstop for the approval gate | WATCH |
| 120B-800B releases (MiniMax M3.1 Flash, Muse Spark Open, ...) | various | teachers at best | SKIP |

### 2.3 Frameworks, datasets, locators
| Item | License | Notes | Verdict |
|---|---|---|---|
| browser-use | MIT | replay errors at the first unmatched step | WATCH (reference) |
| workflow-use | AGPL-3.0 | "very early" | SKIP |
| Stagehand | MIT | cloud-LLM self-heal over a trimmed AX tree | WATCH (ideas) |
| Healenium | Apache-2.0 | similarity healing with a threshold | TRY (idea) |
| MolmoWeb data | ODC-BY | task diversity | TRY |
| WebChain | cc-by-4.0 on card (conflicting source) | AX snapshots, partial release | TRY after licence check |
| ScreenRef / OSWorld-G refusal / GUI-HalluBench | varies | abstention evals | TRY as protocol |

### 2.4 Training
| Item | License | Notes | Verdict |
|---|---|---|---|
| fla main, sm_75 IEEE fallback (PR #920) | MIT | could remove the torch fallback; GDN-1 path unproven | TRY first |
| Unsloth Qwen3.5 | Apache-2.0 | second fast-T4 route; check fp16 | TRY |
| TRL 1.16 GRPO env_factory + grpo_browsergym; DPOTrainer | Apache-2.0 | Qwen3.5-2B, AX tree, 1 GPU | ADOPT |
| OpenPipe ART + RULER | Apache-2.0 | vLLM-based; T4 fit uncertain | WATCH |
| OpenRLHF / verl-agent / SkyRL | Apache-2.0 | Ray, multi-GPU | SKIP |

### 2.5 Verifiers
| Item | Notes | Verdict |
|---|---|---|
| Universal Verifier + CUAVerifierBench (arXiv 2604.06240) | kappa 0.64, near-zero false positives (self-reported); 106 tasks | TRY now |
| WebTailBench (609 tasks, MIT) | extra unseen-app eval | TRY |
| VAGEN, Agent-RRM, OpAgent, trajectory-RM benchmark | process rewards | WATCH (for GRPO) |

## 3. Skeptic's notes
- Nearly all benchmark numbers are self-reported. Only the llama.cpp MTP gains have independent testers, and those are on
  27B GPU setups.
- No public benchmark matches the AWOS repair task (shortlist pick or NOT_FOUND over an AX tree). Our own deleted-target
  set is the one that matters.
- AWOS emits few constrained tokens over long prompts. Prefill and prefix reuse dominate, so fix those before trying
  speculative decoding.
- The T4 kernel lead rests on one PR author's test statement, for GDN-2.

## 4. Not verified
- DeepInfer's X post text; claims of a C++ rewrite; V4.1-Flash active parameters.
- dsh's release date, computer-use features and star count.
- Whether fla's T4 fallback covers Qwen3.5's GDN-1 chunk path, forward and backward.
- MTP / ngram drafting with GBNF; CPU MTP numbers for Qwen3.5-4B.
- Licences of GroundGUI / ScreenRef, GUI-Owl-1.5 weights and MolmoWeb weights; whether Qwen-UI-Agent weights exist;
  WebChain's full release.
- Current Kaggle accelerators.

## 5. Sources
DeepSeek: https://github.com/deepseek-ai/DeepInfer · https://github.com/orgs/deepseek-ai/repositories ·
https://github.com/deepseek-ai/deepseek-harness · https://github.com/deepseek-ai/DeepSpec · https://arxiv.org/abs/2607.05147 ·
https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017 · https://siliconangle.com/2026/10/20/deepseek-open-sources-deepinfer-the-inference-framework-behind-its-production-services/ ·
https://thenewstack.io/deepseek-deepinfer-huawei-nvidia/
llama.cpp: https://github.com/ggml-org/llama.cpp/pull/22673 · https://github.com/ggml-org/llama.cpp/pull/19504 ·
https://github.com/ggml-org/llama.cpp/issues/24587 · https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md ·
https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
Models: https://huggingface.co/Qwen/Qwen3.5-4B · https://huggingface.co/microsoft/Fara1.5-4B · https://github.com/microsoft/fara ·
https://allenai.org/blog/molmoweb · https://arxiv.org/abs/2604.08516 · https://github.com/inclusionAI/UI-Venus ·
https://github.com/x-plug/mobileagent · https://huggingface.co/z-lab/Qwen3.5-4B-DFlash ·
https://techcrunch.com/2026/10/19/nvidia-launches-new-open-models-to-run-agents-in-the-enterprise-and-on-pcs/
Grounding / abstention: https://arxiv.org/abs/2610.19100 · https://github.com/PrentisAI/GroundGUI ·
https://openaccess.thecvf.com/content/CVPR2026/papers/Zhang_Exposing_and_Evaluating_Hallucinations_for_GUI_Grounding_CVPR_2026_paper.pdf
Frameworks / data: https://github.com/browser-use/browser-use · https://github.com/browser-use/workflow-use ·
https://github.com/browserbase/stagehand · https://github.com/healenium/healenium-web · https://github.com/allenai/molmoweb ·
https://arxiv.org/abs/2603.05295 · https://huggingface.co/datasets/webagentlab/WebChain
Training: https://github.com/fla-org/flash-linear-attention/pull/920 · https://unsloth.ai/docs/models/qwen3.5/fine-tune ·
https://pypi.org/project/trl/ · https://github.com/huggingface/trl/tree/main/examples/grpo_browsergym · https://github.com/OpenPipe/ART
Verifiers: https://arxiv.org/abs/2604.06240 · https://arxiv.org/abs/2602.00575 · https://arxiv.org/abs/2601.22154 ·
https://arxiv.org/abs/2602.13559 · https://arxiv.org/abs/2604.08178
