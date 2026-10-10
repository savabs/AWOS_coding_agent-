# Hardware-software integration for AI

*Expedition chart 15. Compiled 2026-10-10 from four scout reports covering inference physics, accelerators, integrated AI products, and power/thermal. All four scouts started after the shared WebSearch budget (200 calls) was used up, so the evidence comes from direct fetches of known primary sources plus live checks on the owner's host. Coverage of 2026 papers, Etched, Qualcomm Hexagon, and independent Strix Halo reviews is therefore thin. Vendor-reported and self-reported numbers are marked as such.*

Host under study: **Apple M5 MacBook (Mac17,3), 16 GB unified memory, 153 GB/s.** On 2026-10-10 it was running on battery at 38% and discharging, with Low Power Mode on and network wake off.

---

## 1. Summary

- **Decode runs at memory bandwidth; prefill runs at compute.** For a single stream, tokens/s is roughly bandwidth divided by the bytes read per token (weights plus that sequence's KV cache). Apple's M5 gets 3.3-4.1x faster time-to-first-token (TTFT) than M4 but only 1.19-1.27x faster generation, which matches its 28% bandwidth gain (153 vs 120 GB/s).
- **Batching parallel requests costs almost nothing until the critical batch size.** On DGX Spark (273 GB/s), Llama-3.1-8B FP8 decodes at 20.5 tok/s at batch 1 and 368 tok/s aggregate at batch 32 (18x). Hardware does not limit a local box to about 8 agents. Physics favours more concurrent streams on one shared batching server.
- **On a local box, the real limit on concurrency is KV-cache memory capacity.** Long agent contexts mean each agent's KV cache can be as large as the model weights. On the 16 GB M5 with an 8B 4-bit model, the estimate is about 8 agents at 4k context but only 1-2 at 32k.
- **The "8 parallel agents" ceiling the user hit is a software setting, not hardware.** No 8-agent constant exists in AWOS Python code. AWOS executors use `ThreadPoolExecutor(max_workers=4)` waves, and the cap appears to be the orchestration harness's subagent concurrency limit (inferred, not confirmed). For cloud-backed agents, the binding limits are provider RPM/TPM limits, dollars, and shared per-session quotas. Every scout in this expedition ran into one of those quotas: the 200-call search cap.
- **The hardware roadmap targets memory and interconnect, not FLOPs** (Ma and Patterson, 2026). Unified-memory boxes (Mac, DGX Spark, Strix Halo) are bought for capacity at 150-800 GB/s. Their sweet spot is sparse MoE with about 3B active parameters plus batching.
- **Platform vendors have turned the local model into a shared OS service.** Apple Foundation Models, Android AICore, Chrome built-in AI, and Windows Phi Silica/Aion all follow the same pattern: a ready-state API, consent-gated downloads, task APIs, constrained output, and cloud fallback. None of them ships a verification gate, verified-routine replay, or evidence-gated memory.
- **The cloud escalation tier now runs on SRAM-based inference.** Cerebras serves gpt-oss-120b at about 3000 tok/s, and Groq serves it at about 500 tok/s for $0.15/$0.60 per M tokens (vendor-stated). That changes the "seconds" term of the objective.
- **Watts can be measured today without sudo**, through ioreg battery counters and IOReport via Zeus or macmon. Whole-system accounting matters: an accelerator-only boundary undercounts energy per request by about 2.4x (Google).

---

## 2. What matters most (ranked)

### 2.1 Batch concurrent agents on one shared local server (highest leverage, already available)
Each decode step reads the weights once for every sequence in the batch, so aggregate throughput grows close to linearly up to the critical batch size. That size is about 240 tokens for bf16 on TPU v5e and about 280 on H100, and it is far above typical local agent counts.
- Evidence: Spark at batch 1 vs 32 gives 20.5 vs 368 tok/s, with prefill flat at about 7,950 tok/s ([LMSYS](https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/)). llama.cpp batched-bench on Spark exceeds 1500 tok/s aggregate at batch 32 ([llama.cpp #16578](https://github.com/ggml-org/llama.cpp/discussions/16578)). The step-time formula is `(batch x KV bytes + param bytes) / bandwidth` ([Scaling Book](https://jax-ml.github.io/scaling-book/inference/)).
- The AWOS stack already supports this. `mlx_lm/server.py` builds `BatchGenerator(model, completion_batch_size=decode_concurrency, prefill_batch_size=prompt_concurrency, ...)` and batches compatible queued requests ([mlx-lm server.py](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py)). If concurrency is left at its default, concurrent agents queue instead of batching. Check the exact flag names against the installed version.
- Caveat: large batches stay DRAM-bound and can leave compute idle, so the right batch size for each server has to be measured ([Mind the Memory Gap](https://arxiv.org/abs/2503.08311)).

### 2.2 KV-cache memory is the concurrency budget on a local box
Agents per box ≈ (RAM − weights − OS) / (KV bytes per token × context).
- Scaling Book: one 8k-token LLaMA-2-13B sequence needs 6.7 GB of KV cache, so 4 concurrent requests outweigh the parameters.
- Our own arithmetic, not from a source: Qwen3-8B needs about 147 KB per token in fp16 (36 layers × 8 KV heads × 128 dim × 2 × 2 bytes). That is about 4.7 GB per agent at 32k context and about 0.6 GB at 4k.
- This makes context discipline (skeleton views, observation masking, shorter tool output) a concurrency lever, not just a cost or quality lever.

### 2.3 KV compression and model shape multiply concurrency at fixed RAM
- KIVI (2-bit KV, tuning-free): 2.6x less peak memory, up to 4x larger batches, and 2.35-3.47x throughput (self-reported) ([arXiv 2402.02750](https://arxiv.org/abs/2402.02750)).
- Fewer KV heads (GQA/MQA) have the same effect. In the Scaling Book, cutting KV heads 5x moves batch from 16 to 64.
- Sparse MoE models with few active parameters (gpt-oss-120b, Qwen3-30B-A3B) get 15-20x more decode per byte of bandwidth than dense 70B models. Dense Llama-70B FP8 decodes at 2.7 tok/s on Spark. Unverified: under heavy batching, each step touches more experts, which erodes this advantage.

### 2.4 Agent workloads are mostly prefill, so chunked prefill and prefix caching matter
Prior repo research measured agent inputs at 13-20x the size of outputs. Long prefills stall other agents' decode unless the server chunks them. Sarathi-Serve reports 2.6x serving capacity for Mistral-7B on A100 and 5.6x for Falcon-180B versus vLLM (self-reported) ([arXiv 2403.02310](https://arxiv.org/abs/2403.02310)). The M5 is relatively strong at prefill thanks to its GPU neural accelerators ([Apple ML Research](https://machinelearning.apple.com/research/exploring-llms-mlx-m5)). Stable-prefix KV reuse, already in AWOS as T7b, saves dollars, seconds, and joules at once.

### 2.5 Measure watts per task with a whole-system boundary
- Google's full-stack figure for a median Gemini prompt is 0.24 Wh: accelerators 58%, host CPU+DRAM 25%, idle capacity 10%, overhead 8%. An accelerator-only boundary would report about 0.10 Wh ([arXiv 2508.15734](https://arxiv.org/abs/2508.15734)).
- FLOP-based estimates significantly underestimate real energy, and serving configuration alone moves energy by up to 73% ([Fernandez et al.](https://arxiv.org/abs/2504.17674)) or more than 40% ([ML.ENERGY Benchmark](https://arxiv.org/abs/2505.06371)).
- Tools: Zeus `begin_window`/`end_window` reads IOReport on Apple Silicon at 1 mJ resolution without sudo. Its docs warn that these values are model-based estimates ([Zeus docs](https://ml.energy/zeus/measure/)). On the owner's M5, `ioreg -rn AppleSmartBattery` exposes `SystemLoad` (about 5.6 W at near-idle) and `AccumulatedSystemEnergyConsumed`. These keys are undocumented, update less often than every 10 s, and are good enough for task-level joules ([macmon](https://github.com/vladkens/macmon)).

### 2.6 Fix the host before benchmarking anything
Low Power Mode throttles sustained work ([Apple](https://support.apple.com/en-us/101613)), and a laptop discharging on battery is not always-on. Local seconds and watts numbers taken in the current state are biased. ML.ENERGY (2026) also warns that thermal drift biases energy comparisons ([blog index](https://ml.energy/blog/)). Interleaved A/B arms run over hours on a laptop, such as E1 on 73 issues, need randomised order plus logged power source, Low Power Mode state, and thermal state. Apple wall-plug idle figures: Mac mini M4 4 W idle / 65 W max, M4 Pro 5 / 140 W, Mac Studio M4 Max 6 / 145 W, M3 Ultra 9 / 270 W ([Mac mini](https://support.apple.com/en-us/103253), [Mac Studio](https://support.apple.com/en-us/102027)).

### 2.7 The fast cloud tier changes the seconds term
Vendor-stated speeds: Cerebras gpt-oss-120b at about 3000 tok/s ([docs](https://inference-docs.cerebras.ai/models/overview)) and Cerebras Code with Qwen3-Coder-480B at up to 2000 tok/s, at $50/month for 24M tokens/day ([blog](https://www.cerebras.ai/blog/introducing-cerebras-code)). Groq serves gpt-oss-120b at about 500 tps for $0.15/$0.60 per M and gpt-oss-20b at about 1000 tps for $0.075/$0.30 ([docs](https://console.groq.com/docs/models)). At these speeds a whole N-round repair loop finishes in seconds. A fast open-weight cloud model can therefore beat local inference on work per (dollar × second), so routing must weigh all three terms, not just privacy.

### 2.8 Trust architecture for an always-on agent computer
- Windows agent workspace runs each agent in its own account and separate session (lighter than a VM), with per-folder Allow/Ask/Never grants, an OS MCP proxy with per-tool consent, and the whole feature off by default ([support doc](https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3), [MCP security blog](https://blogs.windows.com/windowsexperience/2025/05/19/securing-the-model-context-protocol-building-a-safer-agentic-future-on-windows/)).
- Recall's redesign made it opt-in, with VBS enclaves, TPM-bound keys, and Windows Hello per query session ([blog](https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/)).
- Apple Private Cloud Compute (PCC) sets the bar for private escalation: stateless processing, no general logging, attestation checked before encryption, and a public transparency log ([Apple Security](https://security.apple.com/blog/private-cloud-compute/)).

### 2.9 Small resident base, per-task adapters, constrained output
Apple's on-device model is about 3B parameters with rank-16 LoRA adapters of tens of MB each, swapped dynamically. It used 3.7 bpw in 2024 and 2-bit QAT plus KV sharing in 2025. On iPhone 15 Pro it measured about 30 tok/s and 0.6 ms per prompt token. Guided generation and constrained tool calling are first-class APIs ([2024](https://machinelearning.apple.com/research/introducing-apple-foundation-models), [2025 report](https://machinelearning.apple.com/research/apple-foundation-models-tech-report-2025)). Microsoft is replacing Phi Silica with Aion Instruct: Insider rollout in November 2026, Phi Silica removed in January 2027, and developers must retrain their LoRAs ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica)). Anything tied to specific weights is disposable.

---

## 3. What does NOT matter (hype, dead ends)

| Thing | Why it does not matter for AWOS |
|---|---|
| NPU TOPS and peak FP4 PFLOP marketing | Decode is bandwidth-bound. Spark's "1 PFLOP" still decodes 70B FP8 at 2.7 tok/s. NPUs share the same DRAM bus and mostly run only the vendor's model. Phi Silica's GPU path lacks speculative decoding and prompt compression. |
| ANE or Hexagon as the main LLM engine | They need static shapes and short contexts: ANEMLL supports 512-2K, with 4K verified ([ANEMLL](https://github.com/Anemll/Anemll), [Apple ANE](https://machinelearning.apple.com/research/neural-engine-transformers)). Qualcomm needs precompiled context binaries ([Genie](https://github.com/quic/ai-hub-apps/tree/main/tutorials/llm_on_genie)). They suit small always-on helpers only. |
| Buying more compute for a faster single agent | M5 vs M4: about 4x faster TTFT but about 1.2x faster decode. Decode speed comes from bandwidth (Max/Ultra chips, GDDR7). |
| Dense 70B+ models on one local box | 2.7 tok/s on Spark. MoE-A3B models plus batching are the practical regime. |
| Clustering consumer boxes for huge models | Four Framework nodes ran 405B at 0.7 tok/s while drawing about 600 W ([Geerling](https://www.jeffgeerling.com/blog/2025/i-clustered-four-framework-mainboards-test-huge-llms)). That loses on dollars, seconds, and watts compared with escalating to cloud. RDMA-over-Thunderbolt exo (1.8x on 2 devices, 3.2x on 4, self-reported) is the one exception worth watching. |
| Stacking speculative decoding on large multi-agent batches | Both consume the same spare compute. EAGLE3 gives about 2x at small batch on Spark. The repo trick book found draft speculation on Apple quantized Metal slower in 3 of 5 configs. |
| Flash/SSD offload in multi-agent mode | LLM in a Flash runs models up to 2x DRAM ([arXiv](https://arxiv.org/abs/2312.11514)), and PowerInfer-2 runs 47B at 11.68 tok/s on a phone ([arXiv](https://arxiv.org/abs/2406.06282)). Both give up the RAM that batching needs. Treat them as a separate single-model mode. |
| Owning Groq, Cerebras, Etched, or Tenstorrent hardware | The SRAM designs need racks of chips. Etched has no fetchable measured benchmarks. Tenstorrent gets 15-22 tok/s per user on 8-chip systems ([tt-metal](https://github.com/tenstorrent/tt-metal)). Use them as API tiers only. |
| Full-stack ownership as a quality guarantee | Apple paused notification summaries after fabrications, slipped Siri by about 2 years, and signed a Gemini deal in 2026 ([Wikipedia](https://en.wikipedia.org/wiki/Apple_Intelligence)). Verification mattered more than integration. |
| Cloud-tethered single-purpose AI gadgets | Humane was bricked after its IP sale to HP for $116M. Rabbit had about 5k concurrent users out of about 130k sold (press-reported; [Humane](https://en.wikipedia.org/wiki/Humane_Inc.), [Rabbit](https://en.wikipedia.org/wiki/Rabbit_r1)). |
| Per-token H100/B200 energy leaderboards as a local hardware guide | They measure datacenter GPUs at large batch. At batch 1-8 locally, idle and host power dominate. Use them for methodology only. |
| Optimising Mac sleep/wake for the host | Idle is already 4-6 W, and sleep breaks the job host. Stay awake on AC power and cut wasted attempts instead. |

---

## 4. Key papers and resources

### Must-read
- **How To Scale Your Model, Inference chapter**: https://jax-ml.github.io/scaling-book/inference/. Roofline, critical batch, and KV cost derivations. Use it to build an AWOS concurrency calculator.
- **LMSYS DGX Spark review**: https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/. The best public evidence that batching parallel agents costs almost nothing (20.5 vs 368 tok/s), plus EAGLE3 results.
- **mlx-lm server.py**: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py. The continuous-batching knobs in AWOS's own serving stack.
- **Apple: Exploring LLMs with MLX and M5 neural accelerators**: https://machinelearning.apple.com/research/exploring-llms-mlx-m5. Prefill/decode split on the owner's chip (vendor-run).
- **KIVI 2-bit KV cache**: https://arxiv.org/abs/2402.02750. The main lever for concurrency at fixed RAM.
- **Zeus measurement docs**: https://ml.energy/zeus/measure/. Per-attempt joules on Apple, NVIDIA, and Jetson.
- **Google full-stack energy accounting**: https://arxiv.org/abs/2508.15734. Why the system boundary matters (2.4x).

### Useful
- llama.cpp Apple Silicon table: https://github.com/ggml-org/llama.cpp/discussions/4167. Generation speed tracks bandwidth; prompt speed tracks GPU cores.
- llama.cpp DGX Spark thread: https://github.com/ggml-org/llama.cpp/discussions/16578. Batched-bench methodology to reproduce on M5. gpt-oss-120b drops from 60.6 to 40.6 tok/s at 32k depth.
- Sarathi-Serve: https://arxiv.org/abs/2403.02310. Chunked prefill and stall-free scheduling.
- Mind the Memory Gap: https://arxiv.org/abs/2503.08311. Right-sized batches plus replicas.
- Ma and Patterson, LLM inference hardware: https://arxiv.org/abs/2601.05047. Memory and interconnect are the bottleneck; covers HBF, PNM, and 3D stacking.
- Fernandez et al., energy of inference optimisations: https://arxiv.org/abs/2504.17674
- ML.ENERGY Benchmark: https://arxiv.org/abs/2505.06371 · blog: https://ml.energy/blog/ · Mac profiling example (about 130 J for one 3B completion): https://ml.energy/blog/energy/measurement/profiling-llm-energy-consumption-on-macs/
- Zeus repo: https://github.com/ml-energy/zeus · macmon: https://github.com/vladkens/macmon
- From Words to Watts (power caps): https://arxiv.org/abs/2310.03003. A 30% cap gives −23% energy for +6.7% time.
- EXO disaggregated prefill/decode: https://blog.exolabs.net/nvidia-dgx-spark (2.8x, vendor) · exo repo: https://github.com/exo-explore/exo
- Hazy Research megakernels: https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles. 1.5x over SGLang at batch 1.
- Apple Foundation Models 2024: https://machinelearning.apple.com/research/introducing-apple-foundation-models · 2025: https://machinelearning.apple.com/research/apple-foundation-models-tech-report-2025
- Apple PCC: https://security.apple.com/blog/private-cloud-compute/
- Windows agent workspace: https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3 · MCP security: https://blogs.windows.com/windowsexperience/2025/05/19/securing-the-model-context-protocol-building-a-safer-agentic-future-on-windows/ · Recall redesign: https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- Phi Silica / Aion lifecycle: https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
- Home Assistant deterministic-first cascade: https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/

### Reference
- LLM-Viewer roofline survey: https://arxiv.org/abs/2402.16363
- LLM in a Flash: https://arxiv.org/abs/2312.11514 · PowerInfer-2: https://arxiv.org/abs/2406.06282
- Cerebras models: https://inference-docs.cerebras.ai/models/overview · Cerebras Code: https://www.cerebras.ai/blog/introducing-cerebras-code
- Groq models/pricing: https://console.groq.com/docs/models · LPU explained: https://groq.com/blog/the-groq-lpu-explained · newsroom (Nvidia licensing, 2025-12-24): https://groq.com/newsroom
- tt-metal: https://github.com/tenstorrent/tt-metal · ANEMLL: https://github.com/Anemll/Anemll · Apple ANE transformers: https://machinelearning.apple.com/research/neural-engine-transformers · Qualcomm Genie: https://github.com/quic/ai-hub-apps/tree/main/tutorials/llm_on_genie
- Android AICore: https://developer.android.com/ai/gemini-nano · Chrome built-in AI: https://developer.chrome.com/docs/ai/built-in
- Framework Desktop: https://frame.work/desktop · Geerling cluster test: https://www.jeffgeerling.com/blog/2025/i-clustered-four-framework-mainboards-test-huge-llms
- Jetson Orin power guide: https://docs.nvidia.com/jetson/archives/r36.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html
- MLPerf Power: https://arxiv.org/abs/2410.12032 · How Hungry is AI? (estimates only): https://arxiv.org/abs/2505.09598
- Apple Low Power Mode: https://support.apple.com/en-us/101613 · Mac mini power: https://support.apple.com/en-us/103253 · Mac Studio power: https://support.apple.com/en-us/102027

---

## 5. Implications for AWOS

### Answer to "why can't we run more than 8 agents in parallel?"
Three separate limits apply. Fix whichever one binds.
1. **Harness or orchestrator concurrency setting.** This is software and configurable. The 8 cap is most likely the workflow harness's subagent limit, and AWOS's own DAG waves use `max_workers=4`. Confirm where the 8 comes from before changing anything. Also watch shared per-session quotas: the 200-search budget starved all four scouts here, so adding agents does not help when they share a quota.
2. **Cloud agents.** Provider RPM/TPM limits and budget. These add almost no local energy or compute.
3. **Local-model agents.** KV-cache RAM. On the 16 GB M5, roughly 8 agents fit at about 4k context and 1-2 at 32k. Bandwidth and compute are not the limit, because batching makes extra streams nearly free.

### Adopt now
- **Turn on mlx-lm continuous batching** for the T0 endpoint by raising decode and prompt concurrency (try 8-16). Never run one model process per agent.
- **Fix the host:** AC power, Low Power Mode off on adapter, and network wake on. Otherwise move to a Mac mini M4 Pro (5 W idle, 140 W max) as the always-on box.
- **Log joules in the reward/span ledger** next to dollars and seconds. Diff ioreg `AccumulatedSystemEnergyConsumed` at task start and end, and use Zeus IOReport windows for per-rung breakdowns (replay, local attempt, gate, repair, escalation). Count pytest and tool execution, not just decode.
- **Record bench conditions** with every run: power source, Low Power Mode, thermal state, and randomised arm order.
- **Copy the platform API contract** for the local tier: a ready-state probe, a not-supported fallback, and consent before large model downloads.

### Test (pre-registered A/Bs)
- **Concurrency sweep on M5:** aggregate tok/s and per-agent latency at 1/2/4/8/16 concurrent agents and 4k/8k/32k contexts. Find where KV RAM runs out. Reproduce llama.cpp `batched-bench` methodology.
- **KV quantization (8-bit and 4-bit) in MLX:** does the gate pass rate on the 73-issue set hold while concurrency rises 2-4x?
- **Batched pass@k for the local-attempt rung:** k samples sharing one prefix in a single batched request. Under the 16 GB limit, find the optimal k.
- **Prefill:decode token ratio per task** from AWOS traces. This decides whether to optimise for TTFT (chunked prefill, prefix cache) or for decode.
- **Fast cloud tier vs local:** Cerebras/Groq gpt-oss-120b against local on work per (dollar × second × watt), counting box depreciation and electricity.
- **Prompt-lookup or repo n-gram drafting** for edit generation, at small batch only.
- **Low-power scheduling** for deadline-free overnight work (practice, routine compilation): check quality against joules. The literature predicts −20-35% energy.
- **Apple on-device Foundation Model** (macOS 26+) as a free zero-download rung for classification, extraction, and summarisation under guided generation, admitted through the gate.

### Map to the Gatekeeper
| Rung | Hardware-software implication |
|---|---|
| Verified-routine replay | Lowest joules and seconds by far. Home Assistant shows deterministic-first cascades work in a consumer product. Routines must be model-agnostic and re-validated through the gate on every model swap (Phi Silica → Aion lesson). |
| Local small-model attempt | Use one batched server and models with few KV heads or MoE-A3B. Run pass@k as a batch, keep context short to fit more agents, and use grammar-constrained edits (AWOS_EDIT_GRAMMAR follows Apple's guided-generation pattern). |
| Verification gate | CPU-heavy (tests). Count it in joules. It can run in parallel with GPU decode of other agents. |
| Bounded repair | Shares a prefix with the attempt, so stable-prefix KV reuse plus chunked prefill matter more than raw decode speed. |
| Cloud escalation | Add the seconds term (SRAM tiers at 500-3000 tok/s). Apply PCC-style hygiene: redact and minimise context, send no persistent identifiers, keep a per-request audit, prefer zero-retention providers, and label the privacy class. |
| Memory and always-on host | Use Recall-style controls: encryption at rest, re-authentication for bulk reads, exclusions, and visible pause and delete. |
| Local computer use | Use Windows agent-workspace isolation as the template: a separate macOS user or session, per-folder Allow/Ask/Never grants, and a consenting tool proxy. NPUs could later run the always-on screen-state classifier, embeddings, and router at low power. |

### Watch
- exo RDMA-over-Thunderbolt Mac clustering and EXO prefill/decode disaggregation (vendor numbers).
- High Bandwidth Flash, processing-near-memory, and 3D stacking reaching consumer boxes.
- Whether Groq-style SRAM inference reaches Nvidia workstation parts after the licensing deal (unverified).

### Ignore
TOPS-based hardware decisions, dense 70B locally, multi-box clusters for huge models, NPU-first LLM serving, owning accelerator hardware, and building a system-wide model-hosting layer that competes with Apple, Google, or Microsoft.

### Hardware purchase guidance (if any)
Buy on GB/s per dollar and GB of RAM. Going from 16 GB to 48-64 GB mainly buys concurrency through KV headroom. Run the concurrency sweep before buying.

---

## 6. Open questions worth exploring next

1. What is the measured M5 curve of aggregate tok/s against concurrent agents (1-16) at 8-32k context with mlx-lm batching, and does RAM or latency give out first?
2. Does 4-bit or 8-bit KV quantization keep the gate pass rate on the 73-issue set?
3. What is AWOS's real prefill:decode ratio per rung, and how much does chunked prefill help under multi-agent load?
4. How fast does batching erode the MoE active-parameter advantage on unified memory?
5. Where does the 8-agent cap actually come from (harness subagent setting, provider rate limit, or AWOS executor), and which shared quotas cap fan-out?
6. What are the joules per solved task for each Gatekeeper rung, measured whole-system? No public benchmark reports energy per verified agent task, so AWOS could define one.
7. How accurate are the ioreg energy counters against a wall meter, how often do they update, and do they exist on battery-less Mac mini or Studio?
8. How much do Low Power Mode and thermal state bias earlier local-model benchmark seconds?
9. Does concurrency lower joules per task by sharing the idle and host floor? This is an inference, not yet tested.
10. Is a separate macOS user or session enough isolation for local computer use, or does it need a VM? What are the overhead and threat coverage of each?
11. What fraction of stored routines survive a local-model swap?
12. Gaps left by the search outage: Snapdragon X2 Hexagon LLM throughput, independent Strix Halo vs Spark vs M5 Max numbers at 32k+, Etched benchmarks, the magnitude of ML.ENERGY's thermal-bias result, and the Nvidia-Groq deal terms.

---

## 7. Sources

- https://jax-ml.github.io/scaling-book/inference/
- https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/
- https://github.com/ggml-org/llama.cpp/discussions/4167
- https://github.com/ggml-org/llama.cpp/discussions/16578
- https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py
- https://arxiv.org/abs/2403.02310
- https://arxiv.org/abs/2503.08311
- https://arxiv.org/abs/2402.02750
- https://arxiv.org/abs/2601.05047
- https://arxiv.org/abs/2402.16363
- https://arxiv.org/abs/2312.11514
- https://arxiv.org/abs/2406.06282
- https://blog.exolabs.net/nvidia-dgx-spark
- https://github.com/exo-explore/exo
- https://inference-docs.cerebras.ai/models/overview
- https://www.cerebras.ai/blog/introducing-cerebras-code
- https://groq.com/newsroom
- https://groq.com/blog/the-groq-lpu-explained
- https://console.groq.com/docs/models
- https://github.com/Anemll/Anemll
- https://machinelearning.apple.com/research/neural-engine-transformers
- https://github.com/quic/ai-hub-apps/tree/main/tutorials/llm_on_genie
- https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles
- https://github.com/tenstorrent/tt-metal
- https://developer.android.com/ai/gemini-nano
- https://developer.chrome.com/docs/ai/built-in
- https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
- https://machinelearning.apple.com/research/introducing-apple-foundation-models
- https://machinelearning.apple.com/research/apple-foundation-models-tech-report-2025
- https://security.apple.com/blog/private-cloud-compute/
- https://frame.work/desktop
- https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3
- https://blogs.windows.com/windowsexperience/2025/05/19/securing-the-model-context-protocol-building-a-safer-agentic-future-on-windows/
- https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- https://en.wikipedia.org/wiki/Humane_Inc.
- https://en.wikipedia.org/wiki/Rabbit_r1
- https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/
- https://en.wikipedia.org/wiki/Apple_Intelligence
- https://support.apple.com/en-us/101613
- https://support.apple.com/en-us/103253
- https://support.apple.com/en-us/102027
- https://www.jeffgeerling.com/blog/2025/i-clustered-four-framework-mainboards-test-huge-llms
- https://arxiv.org/abs/2508.15734
- https://github.com/vladkens/macmon
- https://ml.energy/zeus/measure/
- https://github.com/ml-energy/zeus
- https://ml.energy/blog/
- https://ml.energy/blog/energy/measurement/profiling-llm-energy-consumption-on-macs/
- https://arxiv.org/abs/2504.17674
- https://arxiv.org/abs/2505.06371
- https://arxiv.org/html/2310.03003v1 (abstract: https://arxiv.org/abs/2310.03003)
- https://docs.nvidia.com/jetson/archives/r36.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html
- https://arxiv.org/abs/2505.09598
- https://arxiv.org/abs/2410.12032
- Local repo: tasks/active/AGENTIC_AMPLIFICATION_TASK.md and scaffold/agent/dag_executor.py (ThreadPoolExecutor wave sizes)

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method: arXiv (one query batch worked, then rate-limited with HTTP 429), GitHub releases, Hacker News Algolia, and page fetches. Page summaries came from a fetch tool that summarises, so exact wording is paraphrased. No WebSearch was used.

### New since the chart (dated, with URLs; most important first)

1. **KV-cache offloading for concurrent coding agents, 2026-09-27.** EfficientAgent reports a 93% cut in recomputed prompt tokens and 39% lower end-to-end time on SWE-bench Verified agents. It argues the metric that matters is the "reuse working set" of all agents in the pool, and that offload pays off when the GPU has little compute per byte of host bandwidth. https://arxiv.org/abs/2609.33762. Why it matters: it supports chart 2.2 (KV memory is the concurrency budget) and gives a sizing model for the 8-agent question. On unified memory there is no separate host tier, so the finding only transfers partly.
2. **HEAR protocol, 2026-10-05.** A bidirectional harness-to-engine protocol. The authors report 1.61x batch speedup, 2.23x lower median TTFT on SCBench, and 1.23x / 2.45x end-to-end speedups on BrowseComp-Plus and DeepResearchBench, with no quality loss, under concurrent memory-constrained serving. https://arxiv.org/abs/2610.06597. Why it matters: AWOS owns both the harness and the local server, so it can pass workflow hints (stable prefix, wave boundaries) to mlx-lm. Self-reported.
3. **Nvidia Groq 3 LPX in full production, 2026-08-24.** The Register reports 3,400 tok/s on Gemma 4 31B at 100k input, with Cerebras at 882 tok/s under the same conditions (Nvidia's claim, favourable case). Racks hold up to 256 LPUs with 128 GB of SRAM, and Nebius is a first operator. https://www.theregister.com/systems/2026/08/24/what-nvidias-first-groq-3-lpu-benchmarks-do-and-dont-tell-us-about-its-20b-gamble/5291880 and https://nvidianews.nvidia.com/news/nvidia-groq-3-lpx-now-in-full-production-with-world-class-speed-for-agentic-ai. Why it matters: it answers the chart's "Watch" item. SRAM inference is now a Nvidia product line and will likely appear as cloud API tiers, strengthening the "seconds" argument in 2.7. Dollar price per token is not yet known.
4. **Nvidia-Groq deal is a $20B purchase under regulatory and legal pressure.** Reported as a stockholder lawsuit (2026-10-05, https://www.cnbc.com/2026/10/05/nvidia-groq-deal-stockholder-lawsuit.html; page returned 403 to the fetcher, headline only) and a DOJ look (https://www.theregister.com/systems/2026/09/12/nvidias-groq-acquihire-is-on-the-dojs-radar-but-its-already-too-late/5295986, headline only). Why it matters: Groq's independent API pricing and roadmap (chart 2.7 uses $0.15/$0.60 per M) may change; do not hard-code it.
5. **Cerebras listings changed.** The models page now lists Qwen 3.8 27B at about 1850 tok/s alongside gpt-oss-120b at about 3000. A Cerebras and AMD inference tie-up was announced 2026-07-24, and Cerebras listed publicly (S-1 April 2026, IPO priced mid-May). https://inference-docs.cerebras.ai/models/overview, https://www.cerebras.ai/press-release/amd-and-cerebras-announce-industry-leading-ultra-low-latency-and-high-throughput-ai-inference. Why it matters: the fast tier now includes a small dense model suited to coding loops; AWOS should price-test it.
6. **Apple Foundation Models third generation, 2026-06-08.** AFM 3 Core is a 3B dense model, and "Core Advanced" is a 20B sparse model activating 1-4B parameters. Server models run on PCC, extended to Nvidia GPUs in Google Cloud. https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models. HN also lists WWDC26 session "Foundation Models can use third-party LLMs (Claude, Gemini)" (https://developer.apple.com/videos/play/wwdc2026/241/, headline only, not fetched). Why it matters: the chart's 2.9 describes the 2024/2025 generation (3B, rank-16 LoRA); the macOS 26+ zero-download rung is now a bigger, sparser model, so benchmark it fresh before admitting it through the gate.
7. **Local M5 Max results on practical tasks, 2026-08-12.** On a 128 GB M5 Max, a 132-run suite scored Opus 5 94%, Muse Glimmer 30B 93%, GPT-OSS-120B 92%, DeepSeek V4 Flash 2-bit 92%, Qwen3.5-35B 89%, Qwen3-Coder-30B 86%. https://blog.gitbutler.com/local-llm-gauntlet. Why it matters: independent evidence that large-RAM local MoE models are near cloud accuracy on small tasks. It is a 128 GB machine, not the owner's 16 GB host, and the tasks are not repo-level.
8. **DGX Spark 64GB variant, 2026-10-02.** $4,999 from partner OEMs; two clustered units gave up to 1.7x on Qwen 3.8 27B (Nvidia's claim). https://blogs.nvidia.com/blog/local-ai-dgx-spark-64gb-sync/. Why it matters: the Spark is now sold explicitly for "multiple agents working at once". It does not change the bandwidth argument (273 GB/s).
9. **Windows Phi Silica to Aion Instruct dates re-confirmed on the live page (updated 2026-10-02).** Early October 2026 sideload package, November 2026 Insider rollout, January 2027 retail and removal of Phi Silica. LAF tokens are no longer needed for Aion. https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica. Why it matters: the "weights are disposable" lesson stands; the sideload package already exists.
10. **Agent-KV engineering is crowded.** See Yandex "The KV cache as an agent runtime" (2026-09-05, https://research.yandex.com/blog/the-kv-cache-as-an-agent-runtime, fetch failed, headline only), KVCMAS for shared-context multi-agent correction (https://arxiv.org/abs/2609.34060) and PReCache for multi-LoRA agents (https://arxiv.org/abs/2609.34054), both titles only. Why it matters: prefix reuse across agents (T7b) is now a mainstream technique, not a differentiator.
11. **Strix Halo and MTP.** HN reports Strix Halo MTP (multi-token prediction) tuning that moves with context length (https://thefrontierlab.ai/full-context-vulkan-only-strix-halo/, headline only). Why it matters: partly fills the chart's gap on independent Strix Halo numbers, but nothing was verified here.

### Corrections (chart claim -> source)

- Chart 1 / 2.4 "M5 gets 3.3-4.1x faster TTFT than M4 ... 1.19-1.27x faster generation" -> Apple's page text as fetched says "up to 4x" TTFT and a "19-27%" generation gain, with 153 vs 120 GB/s. The generation range and bandwidth match; the 3.3x lower bound was not visible in the fetched text. https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- Chart 2.7 / Watch "Nvidia licensing deal (2025-12-24) ... terms unverified" -> press now describes a $20B purchase/acquihire in late December 2025 (The Register, CNBC headlines). "Licensing" understates it. Terms beyond the $20B figure remain unconfirmed.
- Chart 2.9 describes Apple's 3B on-device model as current -> AFM 3 (June 2026) adds a 20B sparse on-device-class option. Not a contradiction of the 2025 numbers, but the section is dated.
- Others: none found.

### Confirmed claims

- DGX Spark Llama-3.1-8B FP8: 20.5 tok/s at batch 1, 368 tok/s at batch 32, prefill about 7,991 vs 7,949 tok/s (flat); Llama-3.1-70B FP8 decode 2.7 tok/s. https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/
- Cerebras gpt-oss-120b "~3000" tok/s on its docs page (vendor-stated).
- Apple M5 bandwidth 153 GB/s vs M4 120 GB/s (28%) and the prefill/decode split.
- Phi Silica/Aion timeline, GPU path lacking speculative decoding and prompt compression, and the consent-before-download pattern.
- mlx-lm latest release is v0.31.3 (2026-04-22); no newer release found, so the "check flag names against installed version" advice stands. exo latest is v1.0.71 (2026-04-23).

### Still unverified

- The 3.3x lower TTFT bound, the Sarathi/KIVI figures, Google 0.24 Wh and the 2.4x boundary effect, and the ML.ENERGY thermal-bias magnitude were not re-checked.
- arXiv rate-limited after the first query batch, so searches on Apple Silicon inference, energy per agent task, and KV quantization on agents were not completed. A joules-per-verified-task benchmark may now exist.
- Whether HEAR, EfficientAgent, or mlx-lm continuous batching actually help on a 16 GB unified-memory Mac is untested.
- Groq 3 LPX API pricing, Snapdragon X2 Hexagon, Etched, and Strix Halo vs Spark vs M5 Max at 32k+ remain open.
- Where the "8 agent" cap comes from is still not answered by any source.
