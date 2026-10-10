# Local and edge computing for AI

> Expedition chart 06 · compiled 2026-10-10 from four scout reports (on-device hardware, privacy/offline, distributed inference, economics).
> **Coverage caveat:** the session's shared WebSearch budget (200 calls across all agents) was exhausted before or very early in every scout's run. All findings come from direct fetches of primary sources the scouts already knew, plus a local read of the repo. Late-2026 developments that only search would surface are probably missing. Vendor and self-reported numbers are labelled.
> Builds on, does not repeat: `docs/research/local_first_architecture_2026-10.md`, `docs/research/trick_book_2026-10.md`.

---

## 1. Summary

- **Memory bandwidth limits local LLMs, not TOPS.** Decode speed tracks GB/s: 7B Q4_0 runs at 14 tok/s on M1 (68 GB/s), 83 on M4 Max (546 GB/s) and about 32 on the base M5 (about 153 GB/s). Prefill tracks compute. The owner's 16 GB M5 caps a 4-bit 8-9B model at roughly 25-30 tok/s of decode.
- **The M5 fixed the prefill problem for agents.** Apple's GPU Neural Accelerators give 3.3-4.1x faster time-to-first-token than M4. Decode improves only 1.19-1.27x. Agent loops are prefill-heavy (inputs 13-20x outputs in AWOS's own data), so this is the single biggest local hardware change of 2025-26, provided the MLX build actually uses it.
- **Concurrency comes from batching on one box, not from more devices.** Batched decode multiplies aggregate throughput: about 11x at batch 32 for a 7B model in llama.cpp batched-bench, and about 18x for an 8B model on DGX Spark (20.5 to 368 tok/s). Prefix sharing adds up to 6.4x on agent workloads. Sharding a model across devices adds capacity, not concurrency.
- **For local work, utilization decides the economics.** An idle H100 serving one request per second costs $7.60/M tokens, against $0.31 at saturation: a 17-36x penalty. Cached cloud input costs $0.003/M (DeepSeek flash, off-peak). Local cannot win on price per token. It wins on tokens the cloud never sees (replay, local retries), on privacy, on offline use, and on sunk-cost hardware kept busy.
- **Local is not greener per token.** An M4 Max is 1.4-2.3x less energy-efficient than a B200 on identical models. Energy savings come from routing small jobs to small models. Even a router that is only 60% accurate keeps about 45-48% of the oracle savings (Intelligence per Watt, single-turn chat).
- **In a local computer-use agent, privacy is a data-flow problem.** Redaction fails: LLMs infer personal attributes at 85% top-1 accuracy from redacted text. Prompting fails: agents leak 25-39% of the time despite privacy instructions. Architectural separation (CaMeL: 77% vs 84% utility, with provable security) and attested confidential endpoints (under 7% GPU overhead) are what work.
- **Small open computer-use models are now viable on edge hardware.** Fara-7B scores 73.5% on WebVoyager on a laptop NPU, at about 16 steps per task. It is still well behind cloud models on live-web benchmarks (34.1% vs 42.9% on Online-Mind2Web).
- **The owner's "max 8 agents" ceiling is not a hardware limit.** It comes from orchestration settings and shared budgets. In the AWOS repo, `dag_executor.py` sets `MAX_WORKERS = 4`, `swebench_lite.py` sets `max_workers=4` and `scripts/local_model.sh` sets `AWOS_LOCAL_SLOTS=1`. In this session, the shared web-search quota throttled all parallel scouts. The 8 itself most likely comes from the Claude Code / workflow harness subagent cap; nobody checked this directly.

---

## 2. What matters most (ranked)

### 1. Continuous batching plus a shared KV pool plus prefix caching on one local server
This is the evidence-backed answer to "how do we run more agents in parallel."
- Decode reads all weights once per step whether it serves 1 sequence or 32, so batching spreads that cost across sequences. Measured: llama.cpp batched-bench 41.6 to 465 tok/s at batch 1 vs 32 ([batched-bench](https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench)). DGX Spark 8B: 20.5 to 368 tok/s at batch 32 ([LMSYS](https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/)). Continuous batching gave 8x, and vLLM PagedAttention 23x, over naive batching ([Anyscale](https://www.anyscale.com/blog/continuous-batching-llm-inference)).
- RadixAttention prefix reuse: up to 6.4x on agent, multi-call and multi-turn workloads, self-reported ([SGLang](https://arxiv.org/abs/2312.07104)). This stacks on AWOS's T7b stable prompt prefix: N agents with the same prefix pay for its KV once.
- **The serving stack decides whether this works.** llama-server has `-np` slots, continuous batching on by default, `--kv-unified`, `--cache-reuse` and slot save/restore ([server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)). `mlx_lm.server` processes requests **one at a time when `--kv-bits` is set**, and its docs call it not production-grade ([mlx-lm SERVER.md](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)). vllm-mlx offers continuous batching, paged KV and prefix cache on Apple Silicon, but publishes no concurrency numbers ([vllm-mlx](https://github.com/waybarrios/vllm-mlx)).
- On the 16 GB M5, KV memory is the binding limit. Scout estimate, not measured: a 4B-class model needs about 147 KB of fp16 KV per token, which allows about 2 agents at 32k context or about 8 agents at 8k. A 9B Q4 model (about 5.5 GB of weights) leaves less room.

### 2. Memory bandwidth and capacity as the sizing metric; M5 accelerators for prefill
- Bandwidth-to-decode table across all M-series chips ([llama.cpp #4167](https://github.com/ggml-org/llama.cpp/discussions/4167)). Going from M3 Max to M3 Ultra (2x bandwidth) gave 1.4x faster decode.
- M5 vs M4 TTFT: Qwen3-8B 3.97x, Qwen3-14B 4.06x, gpt-oss-20B 3.33x, Qwen3-30B-A3B 3.52x. Decode improves 1.19-1.27x. Vendor benchmark ([Apple MLR](https://machinelearning.apple.com/research/exploring-llms-mlx-m5)).
- NVIDIA discrete GPUs prefill 3-7x faster than Apple. gpt-oss-20B: RTX 5090 9848 pp / 283 tg vs M4 Max 1277 / 92 ([llama.cpp #15396](https://github.com/ggml-org/llama.cpp/discussions/15396)).
- **MoE beats dense on bandwidth-limited boxes.** Llama 3.1 70B runs at 2.7 tok/s on DGX Spark. gpt-oss-20B runs at 49.7 tok/s ([LMSYS](https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/)).

### 3. Utilization-aware economics: dollars per verified solve, not per token
- Concurrency-aware cost model: underutilization penalty of 17.5-36.3x at 1 req/s and about 1.18x at 50 req/s. Single author, not peer reviewed ([arXiv 2606.11690](https://arxiv.org/html/2606.11690v1)).
- Pegatron coding-agent TCO over 28 days: API $9,774 at 99.3% cache hits and $0.57/M effective. On-prem shared cost $5,859 (40% less), on-prem dedicated $14,051 (44% more). The local model had 2.6-4.9x higher defect-repair odds. The authors recommend local-first with cloud escalation ([arXiv 2607.13080](https://arxiv.org/html/2607.13080v1)). n = one developer.
- Cloud floor: DeepSeek flash cache hit at $0.003-0.006/M, miss at $0.15-0.30/M, output at $0.60-1.20/M ([pricing](https://api-docs.deepseek.com/quick_start/pricing)). Price at constant capability falls 9-900x per year ([Epoch](https://epoch.ai/data-insights/llm-inference-price-trends)). So do not buy hardware for cost reasons.
- AWOS arithmetic (estimate): about 2.5 Wh per 6-minute M5 attempt at about 25 W and 18.31 c/kWh ([EIA](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a)) gives about $0.0005 per attempt, against $0.005-0.01 per Flash task. Dollars are trivial on both sides. **The seconds term and the gate's false-accept rate dominate the objective.**

### 4. Gate and router accuracy is the main energy and cost lever
- Intelligence per Watt: local models answer 88.7% of 1M real chat queries, and local coverage rose from 23.2% to 71.3% between 2023 and 2025. Oracle routing saves 80.4% energy, 77.3% compute and 73.8% cost. An 80%-accurate router saves 64.3/61.8/59.0%, a 60% router 48.4/46.7/44.5%. Local success is lowest in architecture and engineering (40.8%) ([arXiv 2511.07885](https://arxiv.org/abs/2511.07885)). Chat only, so agentic coverage will be lower.
- Hybrid LLM: a simple difficulty router makes 40% fewer large-model calls at no quality loss ([arXiv 2404.14618](https://arxiv.org/abs/2404.14618)). RouteLLM: more than 2x cost cut, transfers across model pairs ([arXiv 2406.18665](https://arxiv.org/abs/2406.18665)).

### 5. Privacy as data-flow architecture (the lethal trifecta)
- An agent with private data, untrusted input and an outbound channel can be made to exfiltrate ([Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)). CaMeL's planner/quarantined-parser split with capability tags: 77% vs 84% on AgentDojo, provably secure ([arXiv 2503.18813](https://arxiv.org/abs/2503.18813)).
- Redaction fails: 85% top-1 attribute inference ([Staab et al.](https://arxiv.org/abs/2310.07298)). Prompted privacy fails: GPT-4 leaks 25.68% and Llama-3-70B 38.69% ([PrivacyLens](https://arxiv.org/abs/2409.00138)). AgentDAM provides an end-to-end data-minimization eval for web agents ([arXiv 2503.09780](https://arxiv.org/abs/2503.09780)). PAPILLON reaches 85.5% quality at 7.5% leakage with a local prompt rewriter ([arXiv 2410.17127](https://arxiv.org/abs/2410.17127)).

### 6. Attested confidential cloud as a middle "private escalation" tier
- PCC pattern: stateless processing, no privileged access, OHTTP non-targetability, and encryption only to measurements logged in a transparency log ([Apple PCC](https://security.apple.com/blog/private-cloud-compute/)). VRE runs on a 16 GB Mac, bounties up to $1M ([PCC research](https://security.apple.com/blog/pcc-security-research/)).
- H100 CC overhead is under 7%, bounded by PCIe ([arXiv 2409.03992](https://arxiv.org/abs/2409.03992)). Secure Minions: 5-22% overhead for 3-8B models, under 1% at 32B+, plus 2-6 s of attestation per session. Open code ([Hazy blog](https://hazyresearch.stanford.edu/blog/2025-05-12-security), [repo](https://github.com/HazyResearch/minions)).
- Limit: TEE.fail forges TDX/SEV-SNP attestation with an interposer costing under $1k and physical access ([tee.fail](https://tee.fail/)). Private cloud is a middle tier, not equivalent to local.

### 7. Small local computer-use VLMs
- Fara-7B (Qwen2.5-VL-7B, pixel-only): 73.5% WebVoyager vs 66.4% for UI-TARS-1.5-7B, about 16 vs 41 steps, consent-gated "Critical Points", ships for Copilot+ NPUs. Self-reported ([Microsoft Research](https://www.microsoft.com/en-us/research/blog/fara-7b-an-efficient-agentic-model-for-computer-use/)).

### 8. Free OS-resident small model as a micro-task tier
- Apple's on-device model (about 3B, 2-bit QAT, 8-bit KV, 37.5% KV saving from sharing) offers guided `@Generable` decoding, tool calling and rank-32 LoRA, works offline and has no fee. Apple says it is "not designed to be a chatbot for general world knowledge" ([2025 updates](https://machinelearning.apple.com/research/apple-foundation-models-2025-updates), [tech report](https://machinelearning.apple.com/research/apple-foundation-models-tech-report-2025)). It is a candidate classifier, router, PII tagger or AX-tree summarizer that costs AWOS no RAM. Unmeasured.

### 9. Disaggregated prefill/decode across heterogeneous boxes
- DGX Spark prefill plus M3 Ultra decode: 2.32 s vs 6.42 s on M3 Ultra alone (2.8x), 8k prompt, Llama-3.1-8B. Pays off above about 10k tokens and needs 10 GbE. Self-reported ([EXO blog](https://blog.exolabs.net/nvidia-dgx-spark)). This is the only multi-device pattern that matches the prefill-heavy shape of agent workloads.

---

## 3. What does NOT matter / hype / dead ends

| Claim / idea | Why not |
|---|---|
| NPU TOPS figures (40+ TOPS "Copilot+") | Decode is bandwidth-bound. NPU LLM paths need static shapes and short contexts. GB/s and GB predict agent throughput far better. |
| Main agent LLM on the Apple Neural Engine | ANEMLL recommends 512-1024 tokens of context (4K max), FP16 only, and its LUT4 quality is "fairly low" ([ANEMLL](https://github.com/Anemll/Anemll)). Agent prompts run 30-60k tokens. Use the ANE for embedders, classifiers, OCR and wake detection only ([Apple ANE guide](https://machinelearning.apple.com/research/neural-engine-transformers)). |
| Sharding a model across devices to get more parallel agents | Each token still passes through every device. Memory goes to model slices, not KV for more agents. For N agents, use one model copy per box plus a router. |
| llama.cpp RPC for an always-on host | Its authors call it a "proof-of-concept… fragile and insecure" ([RPC README](https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md)). Geerling measured it getting slower with 2 nodes. |
| Mac Studio RDMA clusters for AWOS today | About 30 tok/s on a 1T model at under 250 W is real ([Geerling](https://www.jeffgeerling.com/blog/2025/15-tb-vram-on-mac-studio-rdma-over-thunderbolt-5), [exo](https://github.com/exo-explore/exo)), but it costs about $40k, is prerelease and crash-prone, and needs TB5 on M4 Pro/Max or M3 Ultra with identical macOS versions. A base M5 is not supported hardware. |
| Public Petals swarms, Raspberry Pi / Mac Mini cluster demos, distributed-llama | About 6 tok/s on 70B, prompts go to strangers' GPUs ([Petals](https://arxiv.org/abs/2312.08361)). Power-of-two node limits ([distributed-llama](https://github.com/b4rtaz/distributed-llama)). Poor work per dollar, second and watt. |
| Dense 70B on 128 GB AI boxes | 2.7 tok/s on DGX Spark. It fits in memory but is useless for interactive loops. Use MoE. |
| "Local is greener" | M4 Max is 1.4-2.3x less efficient than B200. Google reports 0.24 Wh per median prompt, down 33x in a year ([arXiv 2508.15734](https://arxiv.org/abs/2508.15734)). Long-context calls cost 2.5-40 Wh before caching ([Epoch](https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use)). Measure both sides. |
| Blog break-even calculators and raw $/token across harnesses | They ignore utilization (1/U error) and caching. One harness in Pegatron used 16.9x more tokens. Compare $ per verified solve. |
| Buying a 4090/5090/DGX Spark box to cut API spend | It would sit idle, so the utilization penalty applies, and cloud prices fall 9-900x per year. Only privacy, offline use or a measured local win justifies it. |
| PII scrubbing or redaction proxies as the privacy boundary | Defeated by attribute inference. Keep them only as defense in depth for secrets (keys, cards). |
| "Please keep this private" prompts and guardrail models | 25-39% leakage. Not a guarantee. |
| Treating TEE cloud as equal to local | TEE.fail. Google Private AI Compute cites no independent audit. |
| Applying the 88.7% chat headline to coding or computer use | Single-turn chat. Local success is lowest on technical reasoning, and multi-step errors compound. |
| Homomorphic encryption / MPC inference | Orders of magnitude too slow compared with TEEs (scout background knowledge, unverified this session). |
| EdgeShard-style WAN model partitioning, CRDT sync now | Modest gains against weak baselines ([arXiv 2405.14371](https://arxiv.org/abs/2405.14371)). CRDTs are only needed for multi-device state ([Ink & Switch](https://www.inkandswitch.com/essay/local-first/)). |

---

## 4. Key papers and resources

### Must-read
- **llama.cpp server README**: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md. The exact knobs (`-np`, `--kv-unified`, `--cache-reuse`, `/slots`) for serving N agents from one model.
- **mlx-lm SERVER.md**: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md. Quantized KV turns batching off. Check this against AWOS's T0 endpoint first.
- **Intelligence per Watt**: https://arxiv.org/abs/2511.07885. Template for reporting work per watt and dollar, with router-accuracy sensitivity curves.
- **Apple MLX on M5**: https://machinelearning.apple.com/research/exploring-llms-mlx-m5. First-party M5 prefill and decode numbers for the owner's host.
- **Concurrency-aware cost methodology**: https://arxiv.org/html/2606.11690v1. Cost as a function of request rate. Reusable for an AWOS local cost model.
- **Pegatron coding-agent TCO**: https://arxiv.org/html/2607.13080v1. The only real coding-agent cloud vs on-prem study, and it ends up recommending the Gatekeeper pattern.
- **CaMeL**: https://arxiv.org/abs/2503.18813 and **lethal trifecta**: https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/. The formal basis for the sink policy.
- **LMSYS DGX Spark review**: https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/. Batch-1 vs batch-32 numbers on unified memory.

### Useful
- llama.cpp Apple Silicon table: https://github.com/ggml-org/llama.cpp/discussions/4167. gpt-oss Apple vs NVIDIA: https://github.com/ggml-org/llama.cpp/discussions/15396
- llama.cpp batched-bench: https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench. Run it on the M5 to choose `AWOS_LOCAL_SLOTS`.
- vllm-mlx: https://github.com/waybarrios/vllm-mlx. Candidate multi-agent server for Apple Silicon. No independent benchmarks yet.
- SGLang / RadixAttention: https://arxiv.org/abs/2312.07104. Anyscale continuous batching: https://www.anyscale.com/blog/continuous-batching-llm-inference
- Fara-7B: https://www.microsoft.com/en-us/research/blog/fara-7b-an-efficient-agentic-model-for-computer-use/
- Apple Foundation Models 2025: https://machinelearning.apple.com/research/apple-foundation-models-2025-updates and https://machinelearning.apple.com/research/apple-foundation-models-tech-report-2025
- Minions paper: https://arxiv.org/abs/2502.15964, repo (MLX-LM backend): https://github.com/HazyResearch/minions, Secure Minions: https://hazyresearch.stanford.edu/blog/2025-05-12-security
- PrivacyLens: https://arxiv.org/abs/2409.00138, AgentDAM: https://arxiv.org/abs/2503.09780, PAPILLON: https://arxiv.org/abs/2410.17127, Beyond Memorization: https://arxiv.org/abs/2310.07298
- Apple PCC: https://security.apple.com/blog/private-cloud-compute/ and https://security.apple.com/blog/pcc-security-research/
- EXO disaggregated prefill/decode: https://blog.exolabs.net/nvidia-dgx-spark
- DeepSeek pricing: https://api-docs.deepseek.com/quick_start/pricing. Epoch price trends: https://epoch.ai/data-insights/llm-inference-price-trends
- Recall security redesign (hardening template for an always-on memory store): https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/

### Reference
- llm.npu (NPU prefill, 22.4x faster prefill, 30.7x energy savings): https://arxiv.org/abs/2407.05858. Mobile LLM benchmarking and thermals: https://arxiv.org/html/2410.03613
- ANEMLL: https://github.com/Anemll/Anemll. Apple ANE transformers: https://machinelearning.apple.com/research/neural-engine-transformers
- Foundry Local (OpenAI-compatible, auto NPU/GPU/CPU): https://github.com/microsoft/Foundry-Local
- exo: https://github.com/exo-explore/exo. MLX distributed: https://ml-explore.github.io/mlx/build/html/usage/distributed.html. Geerling RDMA test: https://www.jeffgeerling.com/blog/2025/15-tb-vram-on-mac-studio-rdma-over-thunderbolt-5
- llama.cpp RPC: https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md. prima.cpp: https://arxiv.org/abs/2504.08791. distributed-llama: https://github.com/b4rtaz/distributed-llama. Petals: https://arxiv.org/abs/2312.08361, https://github.com/bigscience-workshop/petals. EdgeShard: https://arxiv.org/abs/2405.14371
- H100 CC overhead: https://arxiv.org/abs/2409.03992. TEE.fail: https://tee.fail/
- Energy: Google 0.24 Wh/prompt https://arxiv.org/abs/2508.15734, Epoch https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use, ML.ENERGY https://arxiv.org/abs/2505.06371. EIA electricity prices https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a
- SGLang large-scale EP ($0.20/M output at saturation): https://lmsys.org/blog/2025-05-05-large-scale-ep/
- Routers: Hybrid LLM https://arxiv.org/abs/2404.14618, RouteLLM https://arxiv.org/abs/2406.18665
- SmolLM3-3B: https://huggingface.co/blog/smollm3. Strix Halo results: https://local-llm-benchmarks.dev/ (numbers not extracted)
- Local-first ideals: https://www.inkandswitch.com/essay/local-first/. DeepSeek privacy policy (PRC processing): https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html. Cisco's exposed-Ollama finding was cited without a URL and is not linked here.

---

## 5. Implications for AWOS

### Answer to the owner's question: running more than about 8 agents in parallel
There are three separate ceilings, each with its own fix:

1. **Harness / orchestration cap (most likely source of "8").** Find out where the 8 is set: the Claude Code / workflow subagent limit, or an AWOS constant. AWOS's own caps are lower and hard-coded: `scaffold/agent/dag_executor.py:23 MAX_WORKERS = 4`, `scaffold/agent/swebench_lite.py:390 max_workers=4`, `scripts/local_model.sh AWOS_LOCAL_SLOTS=1`. **Adopt:** make these env-configurable (`AWOS_MAX_WORKERS`, slots) and size them from the binding resource, not a constant.
2. **Cloud agents:** the limits are provider rate limits (RPM/TPM), budget, and *shared tool quotas*. This expedition was itself throttled by a 200-call WebSearch budget shared by every scout. More agents produced *less* coverage per agent. **Adopt:** a per-resource token bucket (API RPM, search calls, dollars) that the scheduler respects, and raise shared quotas (e.g. `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`) before adding agents.
3. **Local agents:** the limits are KV memory and serving mode. **Test:** llama-server `-np 1/2/4/8 --kv-unified --cache-reuse` with the T7b byte-stable prefix, using `batched-bench` and real AWOS prompts on the M5. Record aggregate tok/s, p90 latency per agent and peak memory. Confirm the T0 MLX server is not running with `--kv-bits`, which serializes requests. Evaluate vllm-mlx as an alternative. Expect about 2-4 useful agents at long context on 16 GB. Beyond that, the answer is a 48-128 GB box as a whole-model replica behind the router, not sharding.

### Mapping to the Gatekeeper
| Gatekeeper stage | Adopt / test |
|---|---|
| Verified-routine replay | Remains the largest $ and seconds win: tokens the cloud never sees. Store routines as plain owner-owned files (local-first ideals). Encrypt them at rest (Recall lesson). |
| Local small-model attempt | **Test** whether MLX on the M5 uses the Neural Accelerators (TTFT on a 50k-token AWOS prompt before and after an MLX upgrade; a 3-4x gain is expected if so). **Test** batched local pass@k (k = 2-4 on shared-prefix slots) behind the gate: batching turns idle local time into solve rate. Prefer MoE (Qwen3-30B-A3B class) once memory allows. Add a 3B arm (SmolLM3) for triage and extraction. |
| Micro-task tier (new, before the local attempt) | **Test** Apple's OS-resident 3B model via the Foundation Models framework (Swift bridge) as router, intent classifier, PII/secret tagger and AX-tree summarizer. Zero RAM cost, guided decoding. Accuracy vs the 9B model is unknown. |
| Verification gate | IPW shows savings track router and gate accuracy almost linearly, so false-accept and false-reject rates are the main economic lever. **Add** an AgentDAM/PrivacyLens-style leak rate as a tracked gate metric next to false-accept. |
| Bounded repair | Pegatron: local defects cost 2.6-4.9x more repair. Budget the repair loop in seconds, not attempts. |
| Cloud escalation | **Make the privacy flag three tiers mapped to providers:** `local_only` stays local (no "scrub and send"), `private` goes to an attested confidential endpoint (PCC / Secure Minions pattern), `cloud_ok` goes to the cheapest provider (DeepSeek flash, which processes data in the PRC). For private long context, **test** MinionS: the cloud decomposes, the local model reads the chunks (97.9% quality recovery, 5.7x cheaper, self-reported). Schedule non-urgent escalations in DeepSeek off-peak windows. |
| Memory from strong evidence | Filter secrets before memory writes. Bind the model server to localhost or put it behind auth. |
| Always-on host | Prefer a fanned Mac mini or Studio for sustained thermals (phone studies show CPU throttling of about 50%). Put embeddings, classifiers and screen-change detection on the ANE at low power. Keep the GPU for the main LLM. |

### Local computer use
- **Test** Fara-7B (4-bit via MLX-VLM) as the pixel rung of the E9 ladder: latency per step and success on the C10/C40 sets. Copy its Critical Points consent gate into the sink policy.
- **Adopt** CaMeL-style taint tags (private / untrusted, with allowed sinks) as the formal basis of T10. A local computer-use agent is the worst case for the lethal trifecta. **Test** how much the planner/quarantined-parser split costs a 9B model compared with the 7 pp CaMeL lost with frontier models.

### Reporting
- **Adopt** IPW-style accounting: accuracy per Wh measured with `powermetrics` per attempt, $ per verified solve, and a coverage curve for AWOS's own task mix. Do not quote chat coverage numbers.

### Watch
vllm-mlx concurrency benchmarks; exo 1.x RDMA stability; llama.cpp RPC over RDMA; Strix Halo (128 GB, about 256 GB/s, about $2k) as a Linux host (it would lose macOS computer use); confidential-GPU rental $/token.

### Ignore (for now)
Running the main model on NPUs or the ANE; device sharding for concurrency; Petals; buying hardware to cut API spend; HE/MPC; CRDTs.

---

## 6. Open questions worth exploring next

1. Where exactly is the 8-agent cap set (harness, AWOS constants, rate limits)? Which resource binds first at 16 or 32 agents?
2. Measured on the M5 16 GB: aggregate tok/s and p90 latency for llama-server `-np 1/2/4/8 --kv-unified` vs vllm-mlx vs mlx_lm.server, with and without the shared T7b prefix. No public Apple Silicon concurrency-scaling numbers were found.
3. Does AWOS's MLX path use the M5 Neural Accelerators? What is the TTFT for a 50k-token prompt?
4. What is AWOS's own local-coverage curve (IPW-style) on the 73-issue set and a computer-use set? How does it compare with 71-89% for chat?
5. Does batched local pass@k at k = 2-4 beat a single local attempt plus escalation on $·s·W?
6. How accurate is Apple's on-device 3B model as a router or PII tagger, and does it contend with MLX for the GPU or ANE?
7. Local prefix-cache hit rate across parallel agents on one repo: does it approach Pegatron's 86-99%?
8. What do confidential-GPU instances cost per token compared with DeepSeek flash? Do DeepSeek's *API* terms differ from its consumer terms on retention and location?
9. What is AWOS's leak rate on its own computer-use trajectories, local vs cloud model? Can the privacy flag be inferred automatically, and what is its false-negative rate?
10. Qualcomm Hexagon, Intel and AMD NPU context limits and decode speeds in 2026. Not verified because the search budget ran out.
11. Is a second box (64-128 GB Mac or DGX Spark) as a replica or prefill node worth buying at AWOS's measured escalation rate?

---

## 7. Sources

- https://github.com/ggml-org/llama.cpp/discussions/4167
- https://github.com/ggml-org/llama.cpp/discussions/15396
- https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
- https://ml-explore.github.io/mlx/build/html/usage/distributed.html
- https://github.com/waybarrios/vllm-mlx
- https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- https://machinelearning.apple.com/research/apple-foundation-models-2025-updates
- https://machinelearning.apple.com/research/apple-foundation-models-tech-report-2025
- https://machinelearning.apple.com/research/neural-engine-transformers
- https://github.com/Anemll/Anemll
- https://arxiv.org/abs/2407.05858
- https://arxiv.org/html/2410.03613
- https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/
- https://lmsys.org/blog/2025-05-05-large-scale-ep/
- https://www.microsoft.com/en-us/research/blog/fara-7b-an-efficient-agentic-model-for-computer-use/
- https://github.com/microsoft/Foundry-Local
- https://www.jeffgeerling.com/blog/2025/15-tb-vram-on-mac-studio-rdma-over-thunderbolt-5
- https://github.com/exo-explore/exo
- https://blog.exolabs.net/nvidia-dgx-spark
- https://huggingface.co/blog/smollm3
- https://local-llm-benchmarks.dev/
- https://www.anyscale.com/blog/continuous-batching-llm-inference
- https://arxiv.org/abs/2312.07104
- https://arxiv.org/abs/2504.08791
- https://github.com/b4rtaz/distributed-llama
- https://arxiv.org/abs/2312.08361
- https://github.com/bigscience-workshop/petals
- https://arxiv.org/abs/2405.14371
- https://arxiv.org/abs/2511.07885
- https://arxiv.org/html/2511.07885
- https://arxiv.org/abs/2310.07298
- https://arxiv.org/abs/2409.00138
- https://arxiv.org/abs/2503.09780
- https://arxiv.org/abs/2503.18813
- https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- https://arxiv.org/abs/2410.17127
- https://security.apple.com/blog/private-cloud-compute/
- https://security.apple.com/blog/pcc-security-research/
- https://hazyresearch.stanford.edu/blog/2025-05-12-security
- https://github.com/HazyResearch/minions
- https://arxiv.org/abs/2502.15964
- https://arxiv.org/abs/2409.03992
- https://tee.fail/
- https://arxiv.org/abs/2404.14618
- https://arxiv.org/abs/2406.18665
- https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- https://www.inkandswitch.com/essay/local-first/
- https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html
- https://api-docs.deepseek.com/quick_start/pricing
- https://arxiv.org/html/2606.11690v1
- https://arxiv.org/html/2607.13080v1
- https://arxiv.org/abs/2508.15734
- https://epoch.ai/data-insights/llm-inference-price-trends
- https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use
- https://arxiv.org/abs/2505.06371
- https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a
- Repo (local read): `scaffold/agent/dag_executor.py`, `scaffold/agent/swebench_lite.py`, `scaffold/agent/best_of_n.py`, `scripts/local_model.sh`
