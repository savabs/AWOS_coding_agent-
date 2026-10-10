# Local / open models landscape

*Expedition chart 07, 2026-10-10. Built from four scout reports: open model families, small agent/tool models, MoE vs dense on consumer hardware, and cheap post-training. Method caveat: the shared WebSearch budget ran out early, so the evidence comes from direct reads of primary sources (model cards, LICENSE files, arXiv, leaderboard CSVs). JS-rendered leaderboards (Terminal-Bench, OSWorld) could not be read. Unless marked independent, numbers are vendor- or author-reported. Builds on `docs/research/local_first_architecture_2026-10.md` and `docs/research/trick_book_2026-10.md` without repeating them.*

---

## 1. Summary

- **Vendor cards overstate small-model agency by about 2-3x.** Qwen3.6-35B-A3B claims 73.4 on SWE-bench Verified but scores 24.7% pass@1 on fresh SWE-rebench tasks. Qwen3.6-27B claims 77.2 and scores 31.2% pass@1 / 57.7% pass@5. Frontier models score 56-64% on the same window ([SWE-rebench](https://swe-rebench.com/)).
- **pass@k plus a good verifier is where small models earn their keep.** The best small dense model gains about 26 points going from pass@1 to pass@5. Hybrid verifiers lift a 32B from 34% to 51% ([R2E-Gym](https://arxiv.org/abs/2504.07164)). The precision of the gate, not the quality of the generator, is the binding constraint.
- **Most small-model failures are wrong logic, not formatting.** In one independent same-scaffold run, 76% of Qwen3.6-35B-A3B's failures were wrong logic, 14% incomplete patches and 10% no submission ([ai-muninn](https://ai-muninn.com/en/blog/swe-bench-qwen36-failure-modes)). Edit-format work fixes only a small share.
- **The licence-clean frontier at 20-35B is Qwen3.6/3.8-27B, Qwen3.6-35B-A3B, GLM-4.7-Flash, gpt-oss-20b, Gemma 4 and Devstral Small 2** (Apache/MIT). Meta has released nothing open since April 2025. Qwen has moved its newest mid-size model (Qwen3.8-Flash-Next) to a restrictive community licence that names "AI Work Assistant" businesses.
- **Hardware physics favours sparse MoE for a single stream.** Decode on Apple silicon is bandwidth-bound, so a 3B-active MoE decodes about 3-8x faster than a dense 27-32B model ([Apple MLX M5](https://machinelearning.apple.com/research/exploring-llms-mlx-m5)). Within one family, dense still wins on quality: Qwen3.6-27B beats 35B-A3B by about 4-8 points on every agentic benchmark on the card.
- **The current AWOS host (M5, 16 GB) cannot hold the planned L0 model.** Qwen3.6-35B-A3B at Q4 is about 20 GB. gpt-oss-20b (MXFP4, about 16 GB) is the only strong MoE that fits as published.
- **Cheap post-training is now a proven path.** On-policy distillation costs about 1/10 the GPU-hours of RL ([Qwen3 report](https://arxiv.org/html/2505.09388)). SFT-only SWE agents reach 42-54% Verified at 8-32B for hundreds to thousands of dollars ([SERA](https://allenai.org/blog/open-coding-agents), [SWE-Lego](https://arxiv.org/abs/2601.01426)). A 7B on-device computer-use agent trained on about $1-per-trajectory verified data exists ([Fara-7B](https://arxiv.org/abs/2511.19663)).
- **Local parallelism is limited by memory, not by a model count.** The "8 agents" cap the user hit is a harness setting (Claude Code subagent concurrency, plus a shared 200-call web-search budget that this run used up). On a local box, the limits are KV-cache memory per agent and MoE expert coverage, which grows with batch size.

---

## 2. What matters most (ranked)

### 1. Independent, fresh-task evaluation over vendor cards
SWE-rebench (111 tasks from 2026-05-15 to 07-01, 65 repos) reports Qwen3.6-27B at 31.2% pass@1, 57.7% pass@5, $0.62 per problem and about 3.1M tokens per problem. Qwen3.6-35B-A3B scores 24.7% / 43.2% with about 3.7M tokens, and Qwen3.5-35B-A3B 17.1%. For comparison, DeepSeek-V4-Pro scores 40.2% at $0.15, GLM-5.2 62.9%, and GPT-5.6 Sol 62.3% using 0.6M tokens. Small models therefore burn about 5-10x more tokens per problem. That costs seconds and watts even when the dollar cost is zero. There are no SWE-rebench entries yet for Devstral, Gemma, Kimi, GLM-4.7-Flash, gpt-oss-20b, Nemotron Nano or Qwen3.8. Source: [swe-rebench.com](https://swe-rebench.com/).

Public tool-calling benchmarks are noisy too. An audit found evaluator/expert disagreement on 18.5% of 496 tasks, and on LiveMCPBench 23 repeat runs of one setup ranged from 57.9% to 76.8% ([arXiv 2607.02577](https://arxiv.org/abs/2607.02577)). A gap of a few points between models on a public board means little.

### 2. Sample-k + hybrid verification gate
- R2E-Gym-32B goes from 34.4% to 51% by combining execution-based and execution-free verifiers. Either one alone plateaus at 42-43% ([arXiv 2504.07164](https://arxiv.org/abs/2504.07164)).
- SWE-Lego-8B goes from 42.2% to 49.6% with a verifier choosing from 16 samples ([arXiv 2601.01426](https://arxiv.org/abs/2601.01426)).
- An Oct-2026 preprint ("Teaching Agents to Code Reliably", arXiv 2610.03984; reported by the scout, not re-fetched) trains location and edit diversity plus a verifier into 7-30B policies: pass@1 rises from 31.9% to 43.0% and verifier precision from 26.8% to 41.7%.

Locally, each extra sample costs only seconds and watts. This is the main argument for the Gatekeeper design.

### 3. Memory and KV cache, not weights, cap local concurrency
Standard-attention Qwen3-30B-A3B needs about 96 KB of KV per token in fp16 (scout calculation), so about 3 GB per 32K-context agent. Ten agents need about 30 GB, more than the weights. Hybrid linear-attention models change this. In Qwen3.6-35B-A3B only 10 of 40 layers use full attention, and in the 27B 16 of 64; the rest hold a fixed-size Gated DeltaNet state ([Qwen3.6-27B card](https://huggingface.co/Qwen/Qwen3.6-27B)). Kimi-Linear-48B-A3B claims up to 75% less KV and up to 6x faster decode at 1M context ([card](https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct)). Qwen claims 10x throughput above 32K context for Qwen3-Next-80B-A3B over dense 32B ([card](https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct)). Whether llama.cpp and MLX support these layers well on Metal, including prefix caching, is **unverified**.

### 4. MoE batching does not scale like dense
Assuming uniform routing, one stream on Qwen3-30B-A3B (128 experts, top-8) touches 6.25% of experts per layer. Eight concurrent streams touch about 52 experts, about 40%, so the server reads about 6.5x the expert bytes for 8x the tokens. Real routing is skewed, so this overstates the effect. A dense model reads the same bytes regardless of batch size ([Tensor Economics](https://www.tensoreconomics.com/p/moe-inference-economics-from-first)). Expect 8 local streams to give perhaps 3-5x one stream's aggregate throughput, not 8x. This is an estimate that needs measuring. Speculative decoding still helps MoE across a wider batch range than dense, up to 2.29x ([MoESD](https://arxiv.org/abs/2505.19645)).

### 5. Prefill is the hidden latency cost
On an M2 Ultra, gpt-oss-20b runs about 1,889 tok/s prefill against 116 tok/s decode ([llama.cpp #15396](https://github.com/ggml-org/llama.cpp/discussions/15396)). M5's Neural Accelerators give 3.3-4x faster TTFT but only 1.19-1.27x faster decode over M4 ([Apple](https://machinelearning.apple.com/research/exploring-llms-mlx-m5)). Agent turns re-read 10-40K tokens, so prefix and KV reuse (AWOS T7b stable prefix) matter as much as choosing MoE or dense.

### 6. Tool surface design, a training-free lever
- TSCG compiles JSON tool schemas into structured text. It takes Phi-4 14B from 0% to 84.4% accuracy at 20 tools and cuts schema tokens by 52-57% ([arXiv 2605.04107](https://arxiv.org/abs/2605.04107)).
- In MCP-GRANITE, a 4-tool interface beats fine-grained primitives by 16.4 points and a monolithic tool by 33.6. A 3.2B model at the right granularity beats a 20.9B model at the wrong one ([arXiv 2609.24161](https://arxiv.org/abs/2609.24161)).

Both are single-group preprints.

### 7. Constrained decoding (structure only) and quantization (hidden failures)
- Grammar-constrained decoding takes 0.6-4B schema validity from 78.6-92.9% to 100%, but semantic errors persist ([arXiv 2609.23742](https://arxiv.org/abs/2609.23742)).
- 4-bit weights look lossless on headline tau2-style scores, but amplify existing failure modes (such as tool-name hallucination) by up to 2.5x, which is up to 17.6 points per task. The benchmark's error allowance hides this ([arXiv 2607.27275](https://arxiv.org/abs/2607.27275)).
- Separately, agentic tool use falls off a cliff between 3-bit and 2-bit (scout report, PolAgentBench).

### 8. Cheap post-training from verified trajectories
- **On-policy distillation:** Qwen3-8B with on-policy distillation took about 1,800 GPU-hours against 17,920 for RL, and scored 74.4 vs 67.6 on AIME'24. pass@64 also rose ([Qwen3](https://arxiv.org/html/2505.09388), [Thinking Machines](https://thinkingmachines.ai/blog/on-policy-distillation/)).
- **RFT on coding trajectories:** 5,016 trajectories took a 32B to 40.2% Verified, but a 7B to only 15.2% on the same data ([SWE-smith](https://arxiv.org/html/2504.21798)). Student size matters.
- **SFT-only SWE agents:** SERA-32B reaches 54.2% with SFT only. Soft-verified data scales like hard-verified data, and reproducing the previous open SOTA costs about $400 ([AI2](https://allenai.org/blog/open-coding-agents)).
- **Synthetic multi-turn data:** xLAM-2-8b, trained on about 5K blueprint-verified synthetic multi-turn trajectories, beats 2024 GPT-4o on BFCL v3 multi-turn ([APIGen-MT](https://arxiv.org/html/2504.03601)).
- **Training-free memory distillation:** building memories from a teacher's successful trajectories adds 27.2 points on AppWorld for 4-8B students ([arXiv 2608.07169](https://arxiv.org/abs/2608.07169)).

### 9. Small specialists for computer use
- Fara-7B is an on-device computer-use agent trained on multi-verifier-filtered synthetic trajectories at about $1 each. It is reported competitive on WebVoyager and Online-Mind2Web ([arXiv 2511.19663](https://arxiv.org/abs/2511.19663)).
- OpenCUA covers 3 operating systems and 200+ apps, and reaches 45.0% OSWorld-Verified at 72B ([arXiv 2508.09123](https://arxiv.org/abs/2508.09123)).
- Phi-Ground-Any-4B (MIT) outputs click coordinates, which suits a planner-plus-grounder split ([card](https://huggingface.co/microsoft/Phi-Ground-Any)).
- Qwen-AgentWorld-35B-A3B (Apache) predicts the next environment state across 7 domains ([card](https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B)).

All of these are self-reported.

### 10. Licence gating
The Qwen3.8-Flash-Next LICENSE requires a separate licence for commercial use by a "Model as a Service or AI Work Assistant business". It also adds attribution above 100M MAU or $20M monthly revenue, and bans training competing models on outputs ([LICENSE](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE)). Llama 4 requires a "Built with Llama" notice, a "Llama" prefix on derivative names, and approval above 700M MAU ([licence](https://dev.meta.ai/llama/llama4/license/)).

---

## 3. What does NOT matter: hype and dead ends

| Thing | Why it does not matter |
|---|---|
| Vendor SWE-bench Verified scores (73-79 for 27-35B models) | Contaminated and saturated; they fall to 25-31% on fresh tasks. Use them only to rank models within one family. |
| Qwen3.8-27B "OSWorld-Verified 84.3" | Exceeds OSWorld's roughly 72% human baseline. Qwen's own larger Flash-Next reports 19.4% binary on OSWorld 2.0. Almost certainly a different protocol. Verify locally before believing it. |
| BFCL v3 overall for specialists (xLAM-2-8b 72.8%) | On BFCL v4 the same model scores 46.7% overall, with 6.5% on agentic web search and 14.0% on memory ([BFCL CSV](https://gorilla.cs.berkeley.edu/data_overall.csv)). The v3 categories are narrow. |
| Llama family | No open release since April 2025, a restrictive licence, and beaten by Apache/MIT models at small sizes. |
| Sub-5B general models as coding workers | Gemma 4 E4B scores 16.7% on SWE-bench with a tuned scaffold. Qwen3-0.6B scores 3.6% on BFCL multi-turn. Use these sizes only for routing, classification or grounding. |
| "Flash" and "Small" frontier MoEs (DeepSeek-V4-Flash 284B, Mistral Small 4 119B, GLM-5.3-Flash 320B) as local models | They do not fit owner hardware. They belong in the cloud tier, where DeepSeek-V4 is cheap. |
| Peak tok/s headlines ("196 tok/s on a 4090") | Single-stream, short-context, decode-only figures. Compare time to a verified patch instead. |
| N separate model processes for N agents | Each process loads its own weights and gets no batching. Use one batched server. |
| Long-CoT distillation (R1-distill style) for agents | Models of 3B and under learn worse from long reasoning traces ([arXiv 2502.12143](https://arxiv.org/abs/2502.12143)), and agent-trajectory distillation beats CoT distillation ([arXiv 2505.17612](https://arxiv.org/abs/2505.17612)). |
| A cloud chat API as a logit-level distillation teacher | The DeepSeek API returns at most top-20 logprobs, only for its own outputs, with a different tokenizer ([docs](https://api-docs.deepseek.com/api/create-chat-completion)). |
| Model merging as the main lever at 7-9B | Merging works best from strong, large bases ([arXiv 2410.03617](https://arxiv.org/abs/2410.03617)). It is a free add-on, not a strategy. |
| Attention-only LoRA with small default ranks | Underperforms. Apply LoRA to all layers with about a 10x learning rate ([LoRA Without Regret](https://thinkingmachines.ai/blog/lora/)). |
| Pretraining a custom small model | Distillation scaling laws favour post-training existing bases when a teacher exists ([arXiv 2502.08606](https://arxiv.org/abs/2502.08606)). |
| "SLMs are the future of agents" position papers | The direction is plausible, but they contain no evidence to route on. |
| OpenHands LM 32B, SWE-agent-LM-32B, Hammer, ToolACE-8B checkpoints | Superseded. Borrow their data techniques, not their weights. |
| Aider polyglot as a 2026 signal | Last updated November 2025 ([leaderboard](https://aider.chat/docs/leaderboards/)). |
| Datacenter serving tricks (expert parallelism, disaggregated prefill) | They solve multi-node communication. A single local box has no interconnect bottleneck. |

---

## 4. Key papers and resources

### Must-read
- **[SWE-rebench](https://swe-rebench.com/):** fresh-task pass@1, pass@5, $/problem and tokens/problem. The reality check on small models.
- **[R2E-Gym (arXiv 2504.07164)](https://arxiv.org/abs/2504.07164):** hybrid verifiers beat either verifier alone. A blueprint for the gate.
- **[ai-muninn failure modes](https://ai-muninn.com/en/blog/swe-bench-qwen36-failure-modes):** an independent same-scaffold comparison of Qwen3.6 and Gemma 4 with a failure taxonomy.
- **[On-Policy Distillation (Thinking Machines)](https://thinkingmachines.ai/blog/on-policy-distillation/):** the recipe and cost comparison, plus an experiment on recovering lost skills after fine-tuning.
- **[SERA / Open Coding Agents (AI2)](https://allenai.org/blog/open-coding-agents):** a cheap SFT recipe with soft verification, at 8-32B.
- **[Fara-7B (arXiv 2511.19663)](https://arxiv.org/abs/2511.19663):** an on-device computer-use agent trained on verified synthetic data. Closest to the AWOS end goal.
- **[MoE Inference Economics (Tensor Economics)](https://www.tensoreconomics.com/p/moe-inference-economics-from-first):** why MoE batching differs from dense.
- **[Apple MLX on M5](https://machinelearning.apple.com/research/exploring-llms-mlx-m5):** measured prefill vs decode on the chip AWOS runs on.

### Useful
- [Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B), [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B), [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B): the main local candidates, with a dense vs MoE table on the 27B card.
- [gpt-oss card (arXiv 2508.10925)](https://arxiv.org/html/2508.10925v1) and [gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b): reasoning effort as a cost dial (SWE-bench Verified 37.4 low, 53.2 medium, 60.7 high). Fits 16 GB.
- [GLM-4.7-Flash](https://huggingface.co/zai-org/GLM-4.7-Flash) (MIT 30B-A3B, 59.2 Verified, tau2 79.5), [Gemma 4 26B-A4B](https://huggingface.co/google/gemma-4-26b-a4b-it), [Devstral Small 2](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512) ([announcement](https://mistral.ai/news/devstral-2-vibe-cli)), [Qwen3.5-35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B).
- [APIGen-MT / xLAM-2](https://arxiv.org/html/2504.03601): a recipe for about 5K verified synthetic multi-turn trajectories.
- [SWE-Lego](https://arxiv.org/abs/2601.01426), [SWE-smith](https://arxiv.org/html/2504.21798), [SWE-Gym](https://arxiv.org/abs/2412.21139), [Skywork-SWE](https://arxiv.org/abs/2506.19290): data and RFT for SWE agents.
- [TSCG](https://arxiv.org/abs/2605.04107), [MCP-GRANITE](https://arxiv.org/abs/2609.24161), [Constrained decoding](https://arxiv.org/abs/2609.23742), [Quantization in agents](https://arxiv.org/abs/2607.27275): cheap levers for the local tier, and their limits.
- [Agent Memory Distillation](https://arxiv.org/abs/2608.07169), [Agent Distillation](https://arxiv.org/abs/2505.17612): passing teacher behaviour to small students.
- [LoRA Without Regret](https://thinkingmachines.ai/blog/lora/), [RL's Razor](https://arxiv.org/abs/2509.04259), [LoRA Learns Less and Forgets Less](https://arxiv.org/abs/2405.09673), [SDFT](https://arxiv.org/abs/2601.19897): continual adaptation with little forgetting.
- [MLX-LM LoRA guide](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md), [Tinker docs](https://tinker-docs.thinkingmachines.ai/): on-host and rented training paths.
- [llama.cpp gpt-oss guide](https://github.com/ggml-org/llama.cpp/discussions/15396): memory budgets and expert-offload costs.
- [OpenCUA](https://arxiv.org/abs/2508.09123), [Phi-Ground-Any](https://huggingface.co/microsoft/Phi-Ground-Any), [Qwen-AgentWorld](https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B): building blocks for computer use.
- [Nemotron Tool-N1](https://arxiv.org/abs/2505.00024), [CANOPY](https://arxiv.org/abs/2609.01245): binary-reward RL for tool use and SWE.

### Reference
- [BFCL v4 CSV](https://gorilla.cs.berkeley.edu/data_overall.csv), [tau2-bench](https://github.com/sierra-research/tau2-bench), [benchmark audit](https://arxiv.org/abs/2607.02577), [Aider leaderboard](https://aider.chat/docs/leaderboards/).
- Licences: [Qwen Community 1.0](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE), [Llama 4](https://dev.meta.ai/llama/llama4/license/).
- MoE systems: [Qwen3-Next-80B-A3B](https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct), [Kimi-Linear](https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct), [MoE scaling laws](https://arxiv.org/abs/2507.17702), [LLM in a Flash](https://arxiv.org/abs/2312.11514), [HOBBIT](https://arxiv.org/abs/2411.01433), [MoE offloading](https://arxiv.org/abs/2312.17238), [Fiddler](https://arxiv.org/abs/2402.07033), [KTransformers](https://github.com/kvcache-ai/ktransformers), [MoE-Lightning](https://arxiv.org/abs/2411.11217), [MoESD](https://arxiv.org/abs/2505.19645), [Strix Halo benchmarks](https://llm-tracker.info/_TOORG/Strix-Halo).
- Post-training extras: [OLMo 3](https://huggingface.co/allenai/Olmo-3-7B-Think), [ReST-EM](https://arxiv.org/abs/2312.06585), [LoRA Land](https://arxiv.org/abs/2405.00732), [Merging at scale](https://arxiv.org/abs/2410.03617), [Evolutionary merging](https://arxiv.org/abs/2403.13187), [s1](https://arxiv.org/abs/2501.19393), [Text-to-LoRA](https://arxiv.org/abs/2506.06105), [Distillation scaling laws](https://arxiv.org/abs/2502.08606), [Small models vs strong reasoners](https://arxiv.org/abs/2502.12143).

---

## 5. Implications for AWOS

Mapped to the Gatekeeper loop: verified replay → local attempt → verification gate → bounded repair → cloud escalation.

### Adopt now
1. **Route on our own held-out tasks, never on cards.** Re-run the 73-issue set (E1) with local candidates and report pass@1, pass@k, tokens, wall-clock and Wh per verified patch. The useful metric is verified work per (dollar x second x watt), not solve rate alone.
2. **Make the local tier sample k with a hybrid gate.** Use execution tests plus a model-based verifier. Spend local samples before escalating; extra samples cost only seconds and watts. Track the gate's precision as a first-class metric, because it is the binding constraint.
3. **Escalate wrong-logic failures, don't re-prompt them.** With 76% of failures being logic errors, bounded repair should be short for semantic failures, while T4 fuzzy-apply covers the 10-14% that are format or incomplete-patch failures.
4. **Turn on grammar-constrained tool calls for every local call.** Valid structure is free. Do not read 100% valid JSON as correct actions.
5. **Log every gate outcome as training data.** Store tools-format JSONL of verified trajectories, failure pairs included (soft-verified data counts too, per SERA). This feeds RFT, verifier training and future RL rewards at no extra build cost.
6. **Run local models behind one batched server.** Use llama-server with `-np N` and continuous batching, or an MLX batched server. Never run one process per agent. Combine with the stable prefix (T7b) and a q8 KV cache.
7. **Add a per-model licence gate.** Allow Apache/MIT weights by default. Flag the Qwen Community License ("AI Work Assistant" clause) and Llama for review.

### Test next (A/Bs)
- **Local model lineup by hardware:**
  - 16 GB (today): gpt-oss-20b at low/medium/high effort, mapped onto Gatekeeper tiers.
  - 48 GB and up: Qwen3.6-35B-A3B (speed and retries) against Qwen3.6-27B dense with MTP (quality). Compare pass@k under a fixed wall-clock and energy budget.
  - Also test: Qwen3.8-27B as a possible default, and GLM-4.7-Flash and Devstral Small 2 as licence-clean alternatives.
- **Q4 vs Q8,** counting process errors per channel (wrong tool name, bad arguments, retries), not just task success.
- **Tool surface:** TSCG-style compiled schemas plus 4-6 mid-granularity tools, against the current surface.
- **Memory distillation:** store cloud-escalation results as subtask exemplars and tool conventions, not only as whole-task routines (AMD shows subtask memory helps most).
- **Parallel-slot scaling:** measure aggregate and per-stream tok/s at 1/2/4/8 streams on one MoE server, and KV use with hybrid DeltaNet models on Metal.

### Hardware decision
The 16 GB M5 blocks the planned L0. The options, in rough order of predictability:
- a 48-64 GB Mac for 35B-A3B plus several slots;
- a 128 GB unified-memory box (Strix Halo / DGX Spark class) for 100B-class MoEs, which moves part of the hard-case tier local. Dense 32B runs at only about 6 tok/s there, while 235B-A22B runs at about 10.6 tok/s;
- SSD expert streaming, which is research-grade and unproven for agent workloads.

### Training roadmap (no deferrals, evidence-ordered)
1. After about 1-5K verified trajectories across many repos, run SFT/RFT with LoRA on all layers (Tinker or MLX). Prefer a ~30B student; a 7-9B student gains much less (SWE-smith: 15.2% for 7B vs 40.2% for 32B).
2. Run on-policy distillation from a same-family open-weight teacher (a larger Qwen) on rented GPUs. The DeepSeek API cannot serve as the teacher.
3. For continual owner adaptation, use on-policy methods (SDFT, or correction toward the frozen base) with a KL/forgetting probe as a promotion gate. Per-routine hot-swappable adapters fit "compiled routines".
4. Later, run binary-reward RL using the verification gate as the reward (Tool-N1, CANOPY).

### Local computer use
- **Architecture to test:** a planner/grounder split. A local planner (Qwen3.6/3.8-27B, or the cloud) pairs with a small grounder (Phi-Ground-Any-4B), with state probes as the gate.
- **Training data:** use the Fara-7B pipeline (propose, attempt, multi-verify, keep) on the owner's own apps to grow a local computer-use specialist. AWOS's computer-use postconditions (T10) would act as the filter.
- **World model:** Qwen-AgentWorld is worth watching for pre-checking risky actions.

### Watch
- SWE-rebench entries for Qwen3.8, GLM-4.7-Flash and gpt-oss-20b.
- llama.cpp and MLX support for DeltaNet and KDA layers.
- Qwen's licence direction.
- Text-to-LoRA maturity.

### Ignore
Vendor Verified scores, Llama, sub-5B coding workers, long-CoT distills for agents, and merging as a core strategy.

### On the user's "more than 8 agents" question
- **The cap is not physics.** Cloud-API agents can scale out. What actually bounds them is provider rate limits and shared quotas: this research run used up a session-wide 200-call WebSearch budget after about one search per scout. Raising the harness concurrency, raising `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, or writing fetch-only briefs would let more agents run usefully.
- **Locally, the levers are about memory:**
  - one batched server;
  - hybrid-attention MoE models;
  - a quantized KV cache;
  - shared prefixes;
  - more RAM.

  Expect throughput to grow sub-linearly with the number of agents.

---

## 6. Open questions worth exploring next

1. Does Qwen3.8-27B beat Qwen3.6-27B on the AWOS 73-issue set and on macOS GUI tasks, at equal $/s/Wh?
2. Under a fixed time and energy budget, do k MoE attempts beat one dense attempt through the gate? This needs a 48 GB or larger host.
3. How far below the uniform-routing estimate (about 6.5x expert bytes at 8 streams) does real MoE batching land on Apple silicon? What is the per-agent KV cost of DeltaNet hybrids in llama.cpp and MLX, and does prefix caching work with linear-attention layers?
4. Can gpt-oss-20b, or a Q3 35B-A3B, on the 16 GB M5 clear the gate often enough to beat calling the cloud directly once escalations are priced?
5. How many owner-specific verified trajectories does a SERA- or APIGen-MT-style fine-tune need to beat training-free memory distillation? xLAM-2 suggests about 5K.
6. Is QLoRA on 16-32K-token agent trajectories feasible on 16 GB with sequence splitting, or must training be off-box?
7. Does the Qwen Community License "AI Work Assistant" clause cover a self-hosted personal agent? Which teacher traces (DeepSeek, Claude, GPT) are licensed for training?
8. Can planner/grounder pairs beat a single VLM on macOS tasks, and are T10 postconditions good enough to filter computer-use training data?
9. Fill the missing data: independent Terminal-Bench and OSWorld numbers for open small models, and SWE-rebench-style numbers for Gemma 4, Devstral, Kimi and Nemotron Nano. Should AWOS keep its own small, fresh eval set?

---

## 7. Sources

- https://swe-rebench.com/
- https://huggingface.co/Qwen/Qwen3.6-27B
- https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- https://huggingface.co/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE
- https://huggingface.co/Qwen/Qwen3.5-35B-A3B
- https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct
- https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B
- https://huggingface.co/zai-org/GLM-4.7-Flash
- https://huggingface.co/google/gemma-4-26b-a4b-it
- https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512
- https://mistral.ai/news/devstral-2-vibe-cli
- https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct
- https://huggingface.co/allenai/Olmo-3-7B-Think
- https://huggingface.co/microsoft/Phi-Ground-Any
- https://huggingface.co/openai/gpt-oss-20b
- https://arxiv.org/html/2508.10925v1
- https://dev.meta.ai/llama/llama4/license/
- https://ai-muninn.com/en/blog/swe-bench-qwen36-failure-modes
- https://aider.chat/docs/leaderboards/
- https://gorilla.cs.berkeley.edu/data_overall.csv
- https://github.com/sierra-research/tau2-bench
- https://arxiv.org/html/2504.03601
- https://arxiv.org/abs/2605.04107
- https://arxiv.org/abs/2609.24161
- https://arxiv.org/abs/2607.27275
- https://arxiv.org/abs/2609.23742
- https://arxiv.org/abs/2608.07169
- https://allenai.org/blog/open-coding-agents
- https://arxiv.org/abs/2601.01426
- https://arxiv.org/abs/2504.07164
- https://arxiv.org/abs/2609.01245
- https://arxiv.org/abs/2505.00024
- https://arxiv.org/abs/2607.02577
- https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- https://www.tensoreconomics.com/p/moe-inference-economics-from-first
- https://github.com/ggml-org/llama.cpp/discussions/15396
- https://github.com/kvcache-ai/ktransformers
- https://arxiv.org/abs/2312.11514
- https://arxiv.org/abs/2411.01433
- https://arxiv.org/abs/2312.17238
- https://arxiv.org/abs/2402.07033
- https://llm-tracker.info/_TOORG/Strix-Halo
- https://arxiv.org/abs/2507.17702
- https://arxiv.org/abs/2505.19645
- https://arxiv.org/abs/2411.11217
- https://thinkingmachines.ai/blog/on-policy-distillation/
- https://arxiv.org/html/2505.09388
- https://api-docs.deepseek.com/api/create-chat-completion
- https://arxiv.org/html/2504.21798
- https://arxiv.org/abs/2412.21139
- https://arxiv.org/abs/2506.19290
- https://thinkingmachines.ai/blog/lora/
- https://arxiv.org/abs/2509.04259
- https://arxiv.org/abs/2405.09673
- https://arxiv.org/abs/2601.19897
- https://arxiv.org/abs/2511.19663
- https://arxiv.org/abs/2508.09123
- https://arxiv.org/abs/2505.17612
- https://arxiv.org/abs/2502.12143
- https://arxiv.org/abs/2312.06585
- https://arxiv.org/abs/2405.00732
- https://arxiv.org/abs/2410.03617
- https://arxiv.org/abs/2403.13187
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md
- https://tinker-docs.thinkingmachines.ai/
- https://arxiv.org/abs/2501.19393
- https://arxiv.org/abs/2506.06105
- https://arxiv.org/abs/2502.08606
