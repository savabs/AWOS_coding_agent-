# Low-level optimisation for local inference

*Expedition chart 08 · 2026-10-10 · Sources: four scout reports (quantization; engines and kernels; decoding speedups; KV cache and memory). Every scout ran with the session WebSearch budget already spent. All findings come from fetching known primary URLs directly, mostly read at abstract or README depth. Numbers marked "self-reported" have not been independently reproduced, and none were measured on AWOS hardware.*

---

## 1. Summary

- **Local decode is limited by memory bandwidth, not compute.** Single-stream tokens/s follows GB/s: M1 Max at 400 GB/s gives 61 t/s and M2 Ultra at 800 GB/s gives 94 t/s on Llama-7B Q4_0. On the M5 (153 GB/s), decode is only 1.19-1.27x faster than M4, while prefill is 3.3-4.1x faster. One stream leaves most of the GPU idle.
- **That makes batching nearly free, and it answers the "why only ~8 agents" question.** llama.cpp batched-bench shows 41.6 t/s total at B=1, 139 at B=8 and 465 at B=32, so about 11x at 32 sequences. oMLX on M4 Max with Qwen3.6-35B-A3B goes from 108.8 to 271.2 t/s between batch 1 and batch 4. The real limit on parallel local agents is **KV and state memory per agent**: (RAM − weights − OS) ÷ per-agent context memory.
- **AWOS's own config is what serializes local agents today.** `scripts/local_model.sh` starts llama-server with `-np $SLOTS`, where `AWOS_LOCAL_SLOTS` defaults to 1 ("one slot owns the whole context", `-c 24576`). `scaffold/agent/dag_executor.py:23` hard-codes `MAX_WORKERS = 4`. I verified both in the repo. The 8-agent cap the user sees in Claude Code workflow runs is a separate harness limit, and inference physics does not cause it.
- **Quantization is settled enough to set rules.** FP8 (W8A8) is effectively lossless, and W4A16 is close to lossless on standard and reasoning benchmarks. But 4-bit weights lose **10-15% on real-world agentic tasks**, compared with 1-3% on tool use (ACBench). They lose **up to 59% at 64K+ context**, against about 0.8% for 8-bit. Small, heavily trained models (1-9B) lose the most.
- **Native low-bit checkpoints now beat post-hoc quants.** Examples are Gemma 3 QAT, gpt-oss in MXFP4 and Kimi-K2 INT4 QAT. Calibrated mixed-precision PTQ (Unsloth Dynamic, MLX DWQ) is the best practical option for GGUF and MLX.
- **Speculative decoding and batching compete with each other.** EAGLE-3 gives 6.5x at batch 1 but only 1.38x at batch 64, and vLLM says MTP "degrades text throughput under high concurrency". On quantized Metal, speculation is fragile: llama.cpp MTP on M1 Max ran 11-28% slower. The new leading drafters are block-diffusion models (DFlash), and suffix/n-gram speculation suits agent loops.
- **Prefix sharing is the biggest multiplier for parallel agents.** Hydragen reports up to 32x throughput with a shared prefix, and RadixAttention up to 6.4x. A KV pool that is aware of tool-call pauses (Continuum: >8x job completion time; KVFlow: 2.19x with many concurrent workflows) decides whether many agents help or just thrash.
- **Architecture beats compression for KV.** Hybrid linear-attention models (Qwen3-Next keeps a growing KV in only 12 of 48 layers) and local/global sliding-window models (Gemma 3) cut per-agent memory more than any KV quantization that is safe to use. Eviction methods that drop tokens fail on multi-turn agent workloads (SCBench).

---

## 2. What matters most (ranked)

**1. One batched server with N slots and a unified KV pool, instead of one slot or N processes.**
Batched decode reads the weights once per step for every sequence. llama.cpp batched-bench gives 3.2x total throughput at B=8 and 11x at B=32. oMLX gives 1.72x at batch 2 and 2.49x at batch 4 (vendor page). llama-server already supports `-np N`, continuous batching (on by default), `--kv-unified` and `--kv-unified-per-slot`. For AWOS this is a configuration change, with no new code required.
Sources: https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench · https://omlx.ai/benchmarks/fqabp6rx · https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

**2. Memory budget per agent: context length, KV precision and model architecture.**
This is my own sizing calculation, and it needs a check against config.json. Qwen3-8B with GQA (36 layers, 8 KV heads, head_dim 128) uses about 144 KiB of KV per token at fp16. That is 4.5 GiB per agent at 32k, about 2.4 GiB with q8_0, and about 1.3 GiB with q4_0. With about 20 GB free for KV, that gives roughly 4 agents at 32k fp16, 8 at 32k q8_0, and 16 at 16k q8_0. Halving context per agent doubles the agent count. q8_0 KV is near-lossless (PPL 6.232 → 6.234, KLD 0.00098, llama.cpp PR 7412). "The K cache seems to be much more sensitive to quantization than the V cache", so K8/V4 (PPL 6.254) is the next test arm. 4-bit KV for both K and V is not: the repo trick book already recorded −58.9% at 7B.
Sources: https://github.com/ggml-org/llama.cpp/pull/7412 · https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/

**3. Shared-prefix KV reuse across agents.**
Hydragen reports up to 32x end-to-end throughput on CodeLlama-13b. When the prefix grows from 1k to 16k tokens, its throughput drops by under 15%, against more than 90% for the baseline. vLLM's PagedAttention gives 2-4x, with the largest gains for parallel sampling. In a paged or unified pool, a 10k-token shared prefix across 16 agents costs about 1.4 GiB once, against about 22 GiB if each agent holds a private copy. AWOS's T7/T7b stable-prefix work is the precondition. It is still unverified whether llama-server actually shares prefix KV across slots in the unified pool.
Sources: https://arxiv.org/abs/2402.05099 · https://arxiv.org/abs/2309.06180 · https://arxiv.org/abs/2312.07104 · https://docs.vllm.ai/en/latest/design/prefix_caching.html

**4. Quant level per tier, chosen by model scale and context length.**
W4A16 is roughly at parity with 8-bit on standard evals (500k+ evals in "Give me BF16 or give me death"; "Quantization Hurts Reasoning?" covers LiveCodeBench). At 4-bit, the real-world agentic drop is 10-15% (ACBench), and long-context damage at 64K+ reaches up to 59%, varying a lot between model and method. Low-bit quantization is easier on large or under-trained models ("Low-Bit Quantization Favors Undertrained LLMs"; Scaling Laws for Precision). This explains why a 3-bit 600B MoE can work while a 3-bit 9B does not. On speed, the llama.cpp table for an 8B model gives Q4_K_M 71.9 t/s, IQ4_XS 77.5, Q3_K_M 71.7 and Q8_0 50.9. Q3 is therefore no faster than Q4 and strictly worse.
Sources: https://arxiv.org/abs/2411.02355 · https://arxiv.org/abs/2504.04823 · https://arxiv.org/abs/2505.19433 · https://arxiv.org/abs/2505.20276 · https://arxiv.org/abs/2411.17691 · https://arxiv.org/abs/2411.04330 · https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md

**5. KV retention that knows about agents during tool-call pauses.**
Agents pause for tests and builds, and LRU eviction drops their KV while they wait. Continuum keeps KV for a time-to-live and reports >8x better average job completion time. KVFlow schedules eviction from the agent step graph and gets 1.83x for a single workflow and 2.19x across concurrent ones. Once there are more agents than fit in KV, this policy determines whether the system gains throughput or thrashes.
Sources: https://arxiv.org/abs/2511.02230 · https://arxiv.org/abs/2507.07400

**6. KLD and flip rate as a cheap acceptance screen for quants and models.**
Compressed models "often" behave "significantly different" from the original even when accuracy matches (Dutta et al.). KL divergence and flip rate track each other, and llama.cpp's perplexity tool reports KLD and top-token agreement directly. Measuring on AWOS's own transcripts is cheap and private.
Source: https://arxiv.org/abs/2407.09141

**7. Native low-bit checkpoints over community PTQ.**
Gemma 3 QAT (about 5k distillation steps) cuts the Q4_0 perplexity drop by 54% and shrinks the 27B from 54 GB to 14.1 GB. gpt-oss-20B fits in about 16 GB with MXFP4. Kimi-K2-Thinking reports all of its benchmarks at INT4. Unsloth Dynamic 2.0 claims Gemma-3-27B Q4_K_XL scores 71.47% MMLU against 70.64% for Google's QAT, which is self-reported.
Sources: https://developers.googleblog.com/en/gemma-3-quantized-aware-trained-state-of-the-art-ai-to-consumer-gpus/ · https://huggingface.co/blog/faster-transformers · https://huggingface.co/moonshotai/Kimi-K2-Thinking · https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs · https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LEARNED_QUANTS.md

**8. Hybrid and sliding-window architectures for running many agents.**
Qwen3-Next-80B-A3B uses the layout 12 × (3 Gated DeltaNet + 1 Gated Attention), so only a quarter of its layers keep a growing KV. The vendor claims 10x the throughput of Qwen3-32B above 32k. Gemma 3 raised its ratio of local to global attention layers specifically to reduce KV. The trade-off is that recurrent and SWA state makes prefix reuse harder (llama.cpp needs `--swa-full`), and that cost has not been measured.
Sources: https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct · https://arxiv.org/abs/2503.19786

**9. Speculation as a per-mode latency lever that adapts to queue depth.**
SuffixDecoding reaches up to 5.3x on agentic SWE-Bench without training. llama.cpp's `ngram-mod` pool is shared across server slots. DFlash claims more than 6x on Qwen3-8B, up to 2.5x beyond EAGLE-3, and ships in llama.cpp, MLX, vLLM and SGLang. MTPLX (native MTP in MLX) self-reports 2.24x on M5 Max and 1.6x on a 16 GB M4 mini. All of these gains shrink as batch size grows, so control them with a goodput signal (TurboSpec) or vLLM's dynamic speculation.
Sources: https://arxiv.org/abs/2411.04975 · https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md · https://z-lab.ai/projects/dflash/ · https://github.com/z-lab/dflash · https://github.com/youssofal/MTPLX · https://arxiv.org/abs/2406.14066 · https://docs.vllm.ai/en/latest/features/speculative_decoding/

**10. Choice of serving backend on Apple Silicon.**
`mlx_lm.server` "processes requests one at a time when you set --kv-bits" and is "not recommended for production". MLX leads sustained throughput on M2 Ultra, llama.cpp is strong for a single stream, and Ollama lags (arXiv 2511.05502). vllm-metal (v0.2.0) and vllm-mlx add paged KV, continuous batching and prefix caching, but neither publishes concurrency sweeps. M5 Neural Accelerators speed up prefill 3.3-4.1x, but only in engines that support them: MLX does and vllm-metal ("NAX") does, and it is unverified whether llama.cpp Metal does.
Sources: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md · https://arxiv.org/abs/2511.05502 · https://github.com/vllm-project/vllm-metal · https://github.com/waybarrios/vllm-mlx · https://machinelearning.apple.com/research/exploring-llms-mlx-m5

---

## 3. What does NOT matter / hype / dead ends

| Thing | Why it doesn't matter (for AWOS now) |
|---|---|
| **Speculative decoding as the way to run more agents** | Speculation lowers per-agent latency but costs total throughput under concurrency (EAGLE-3 drops from 6.5x to 1.38x at batch 64). More agents come from batching, shared KV and memory headroom. |
| **FlashAttention-3/4 on Macs** | FA3's 740 TFLOPs/s depends on Hopper-only TMA and warp-specialization. ggml-Metal already ships fused FA (`-fa on`). The bottleneck is bandwidth and KV memory. |
| **llama.cpp MTP on Metal; separate draft models on unified memory** | MTP measured 11-28% slower, and the issue was closed as "not a bug". In "Lossless but Not Free", 3 of 5 configs ran slower because the quantized Metal backend verifies serially. Draft and target models compete for the same bandwidth (at most 1.7x, and only on structured text). |
| **Medusa, Lookahead (Jacobi) decoding** | EAGLE-3 and DFlash have superseded Medusa. Lookahead spends extra FLOPs per step, which hurts most when requests are batched. |
| **Query-aware token eviction (H2O, SnapKV, PyramidKV) as a default** | SCBench shows that methods using sub-O(n) memory fail on multi-turn, shared-context workloads. Reasoning degrades far more than retrieval ("Semantic Integrity Matters"). |
| **StreamingLLM / attention sinks for coding agents** | They keep generation stable but do not let the model recall anything outside the window. Their only use is a long-lived monitor stream. |
| **4-bit KV (both K and V) to fit more slots** | It measured −58.9% at 7B in the trick book, and `mlx_lm.server` turns batching off when KV is quantized. |
| **3-bit quants of small dense models; 1-bit/binary PTQ** | Q3_K_M decodes no faster than Q4_K_M on 8B. Below 2 bits, representations "change drastically" (ParetoQ, https://arxiv.org/abs/2502.02631). |
| **Picking the "best" 4-bit quantizer (GPTQ vs AWQ vs HQQ vs AutoRound)** | At 4 bits with calibration, the differences are small next to the choice of model, bit width and context length. |
| **FP4 speed-ups on Apple or pre-Blackwell GPUs** | NVFP4 runs only on Blackwell, and MXFP4 gets dequantized elsewhere. You get the memory footprint but not the vendor's throughput or energy figures. |
| **Running N model processes for N agents** | Each process holds its own copy of the weights and its own KV, which wastes most of the RAM. |
| **128k+ context per agent** | One such agent uses the memory of 4-8 agents at 16-32k, and 4-bit weights lose up to 59% at 64K+. |
| **Multi-Mac tensor parallelism (exo); CPU/flash offload (KTransformers, PowerInfer-2, LLM in a Flash)** | These add capacity for one large model at low t/s. They do nothing for concurrency. |
| **Headline 5-13x speedups and "dynamic 3-bit beats SOTA" claims** | They come from batch 1 on GPUs, a single benchmark or huge MoE models, and they do not carry over to quantized Metal or the 9-35B tier. |
| **Single-stream t/s leaderboards; perplexity/MMLU deltas** | They measure the wrong thing. What matters for AWOS is total throughput at N agents, p95 TTFT under load, and pass rate after the verification gate. |

---

## 4. Key papers and resources

### Must-read
- **llama.cpp server README**: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md. Documents `-np`, `--kv-unified`, `--kv-unified-per-slot`, `--cache-reuse`, slot save/restore and `--swa-full`, which are the exact controls for many local agents.
- **llama.cpp batched-bench**: https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench. A one-command concurrency sweep from B=1 to 32, with a `-pps` shared-prompt mode.
- **Hydragen**: https://arxiv.org/abs/2402.05099. Shows how much cheaper k agents become when they share a prefix.
- **ACBench, "Can Compressed LLMs Truly Act?"**: https://arxiv.org/abs/2505.19433. The only benchmark of how compression affects agentic ability.
- **SCBench**: https://arxiv.org/abs/2412.10319. Shows which KV methods survive multi-turn, shared-context workloads.
- **Continuum**: https://arxiv.org/abs/2511.02230. KV time-to-live across tool-call pauses.
- **Give Me BF16 or Give Me Death**: https://arxiv.org/abs/2411.02355. The largest quantization study, with recommendations by serving mode.
- **Apple ML Research, MLX on M5**: https://machinelearning.apple.com/research/exploring-llms-mlx-m5. Primary numbers for the owner's chip.

### Useful
- llama.cpp PR 7412 (KV-type perplexity/KLD table): https://github.com/ggml-org/llama.cpp/pull/7412
- llama.cpp Apple Silicon perf discussion: https://github.com/ggml-org/llama.cpp/discussions/4167
- llama.cpp quantize README (bpw and t/s per type): https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md
- llama.cpp speculative docs (draft, eagle3, dflash, dspark, mtp, n-gram modes): https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
- Long-context quantization study: https://arxiv.org/abs/2505.20276
- Accuracy is Not All You Need (KLD/flips): https://arxiv.org/abs/2407.09141
- Quantization Hurts Reasoning?: https://arxiv.org/abs/2504.04823
- Low-Bit Quantization Favors Undertrained LLMs: https://arxiv.org/abs/2411.17691
- KVFlow: https://arxiv.org/abs/2507.07400
- SGLang/RadixAttention: https://arxiv.org/abs/2312.07104
- PagedAttention/vLLM: https://arxiv.org/abs/2309.06180
- SuffixDecoding: https://arxiv.org/abs/2411.04975
- DFlash (project and repo): https://z-lab.ai/projects/dflash/ · https://github.com/z-lab/dflash. DFlash 2 blog (self-reported): https://inco.ai/blog/dflash2/
- TurboSpec: https://arxiv.org/abs/2406.14066
- mlx-lm SERVER.md and LEARNED_QUANTS.md: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md · https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LEARNED_QUANTS.md
- MLX-LM generate.py (BatchGenerator, kv-bits): https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/generate.py
- vllm-metal: https://github.com/vllm-project/vllm-metal · vllm-mlx: https://github.com/waybarrios/vllm-mlx · MTPLX: https://github.com/youssofal/MTPLX
- oMLX batch benchmark: https://omlx.ai/benchmarks/fqabp6rx
- Unsloth Dynamic 2.0: https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs
- Gemma 3 QAT: https://developers.googleblog.com/en/gemma-3-quantized-aware-trained-state-of-the-art-ai-to-consumer-gpus/
- Qwen3-Next model card: https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct
- Production-grade local inference on Apple Silicon: https://arxiv.org/abs/2511.05502
- llguidance (about 50 µs per mask): https://github.com/guidance-ai/llguidance

### Reference
- Quantization formats: QTIP https://arxiv.org/abs/2406.11235 · ExLlamaV3 https://github.com/turboderp-org/exllamav3 · ik_llama.cpp https://github.com/ikawrakow/ik_llama.cpp · PV-Tuning https://arxiv.org/abs/2405.14852 · AutoRound https://github.com/intel/auto-round · NVFP4 https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/ · ParetoQ https://arxiv.org/abs/2502.02631 · Scaling Laws for Precision https://arxiv.org/abs/2411.04330 · Kimi-K2-Thinking https://huggingface.co/moonshotai/Kimi-K2-Thinking · HF faster-transformers (MXFP4) https://huggingface.co/blog/faster-transformers · llama.cpp discussions on AQLM vs IQ2 and on imatrix data https://github.com/ggml-org/llama.cpp/discussions/5063 · https://github.com/ggml-org/llama.cpp/discussions/5962
- KV compression: TurboQuant https://arxiv.org/abs/2504.19874 · KIVI https://arxiv.org/abs/2402.02750 · KVQuant https://arxiv.org/abs/2401.18079 · DuoAttention https://arxiv.org/abs/2410.10819 · KVzip https://arxiv.org/abs/2505.23416 · StreamingLLM https://arxiv.org/abs/2309.17453 · CacheBlend https://arxiv.org/abs/2405.16444 · Semantic Integrity Matters https://arxiv.org/abs/2502.01941 · Gemma 3 report https://arxiv.org/abs/2503.19786
- Speculation: EAGLE-3 https://arxiv.org/abs/2503.01840 · MagicDec https://arxiv.org/abs/2408.11049 · llama.cpp MTP-on-Metal issue https://github.com/ggml-org/llama.cpp/issues/23752 · Lossless but Not Free https://arxiv.org/abs/2607.17283 · vLLM Qwen3.5 recipe https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html
- Kernels and capacity: FlashAttention-3 https://arxiv.org/abs/2407.08608 · KTransformers https://github.com/kvcache-ai/ktransformers · LLM in a Flash https://arxiv.org/abs/2312.11514 · exo https://github.com/exo-explore/exo

---

## 5. Implications for AWOS

### Direct answer to "why can't we run more than ~8 agents in parallel?"
Three different caps get mixed up here, and each has its own fix:
1. **Claude Code workflow/subagent harness (the "8" seen in these runs).** This is an orchestration setting and has nothing to do with inference. The scouts also hit a second shared limit: the session-wide WebSearch budget of 200 calls, refilling about 100 per hour, was empty before any of the four scouts started. Adding agents beyond 8 would only have made more agents compete for that budget. Before scaling fan-out, raise the shared tool budgets (scouts named `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, which I have not verified) or give each agent a narrower brief that needs fewer tool calls.
2. **AWOS orchestrator.** `dag_executor.py` sets `MAX_WORKERS = 4`, so a DAG wave never runs more than 4 calls at once. Make it configurable and, on the local tier, tie it to the slot count.
3. **AWOS local inference.** `local_model.sh` runs with `-np 1`, so every local agent queues behind a single sequence. Nothing in the hardware forces this. Batched decode scales well past 8.

### Adopt now (configuration, low risk; local-tier step of the Gatekeeper)
- **Multi-slot llama-server.** Set `AWOS_LOCAL_SLOTS` to 4, 8 or 16 with `--kv-unified`, scaling `-c` so each slot gets 8-16k (not 24k). Keep `-fa on`. On the 16 GB M5, the ceiling is roughly (GPU-wired budget − about 5.5 GB of weights) ÷ per-slot KV. Measure it, don't guess.
- **q8_0 KV by default on the local tier** (about 2x more slots for about 0.002 PPL).
- **Keep the T7b stable prefix byte-identical** across concurrent agents, so the shared prefix is prefilled once.
- **Scale-aware quant rule, replacing the blanket "no 3-bit":** for models of about 9B or smaller on 16-24 GB, run Q5-Q8 if memory allows and Q4_K_M/IQ4_XS as the floor. Q4 only up to about 32k context. Sub-4-bit only for large MoE, and only after testing. Prefer vendor QAT or native-4-bit checkpoints when they exist.
- **Never put `--kv-bits` on `mlx_lm.server` when concurrency is needed.** Check whether the T0 MLX path serializes requests.

### Test (pre-registered A/Bs, scored as solved tasks ÷ (dollar · second · watt) through the verification gate)
1. **Concurrency sweep on the owner's M5:** batched-bench (B=1..32, with `-pps`), then a real agent replay at `-np` 1/2/4/8/16, recording total t/s, p95 TTFT, watts and solved tasks. This decides how many local agents to run, and where the trade-off with cloud escalation flips.
2. **Quant as an arm in L0:** Q4 vs Q6 vs Q8 on the 73-issue set, plus long-context slices. Check whether Q8's higher first-pass rate outweighs its roughly 30% slower decode once repair loops are counted.
3. **KLD pre-screen:** run `llama-perplexity --kl-divergence` and top-1 agreement on held-out AWOS transcripts (code, tool-call JSON, SEARCH blocks) before any new model or quant reaches the issue bench.
4. **Calibration on the owner's own traces** (imatrix or DWQ on AWOS transcripts vs generic data). This could make the local model "better at the owner's work" with no training, and no public study exists.
5. **K8/V4 KV** vs q8_0 on the E1 edit-format error rate.
6. **Speculation in single-agent latency mode:** shared-slot `ngram-mod`/suffix decoding on the edit and repair loop, with DFlash or MTPLX as the dense-model arm. Require byte-identical output at temperature 0, and turn speculation off when queue depth exceeds a threshold.
7. **Retaining KV across tool-call pauses** (slot pinning or save/restore while tests run), as a cheap first approximation of Continuum.

### Watch
vllm-metal and vllm-mlx (paged KV and prefix cache on Metal, once independent sweeps exist); llama.cpp Metal support for the M5 Neural Accelerators; TurboQuant-class 3-4-bit rotated KV arriving in llama.cpp or MLX; vendor QAT checkpoints for Qwen, GLM and DeepSeek-lite; hybrid models (Qwen3-Next class) for a 64 GB+ box running many agents.

### Ignore for now
FA3/FA4, multi-Mac tensor parallelism, CPU/flash offload for concurrency, Medusa and Lookahead, eviction-based KV compression, FP4 on non-Blackwell hardware, and choosing between quantizers at 4 bits.

### Local computer use
Computer use is prefill-heavy (screenshots, accessibility trees) and pauses on environment actions just as coding pauses on tests. The same levers carry over: M5 prefill acceleration (MLX/NAX), a shared prefix for the stable system prompt and App Card, and KV retention across action waits. On an always-on host, the GPU-wired memory limit (`iogpu.wired_limit_mb`) has to leave headroom for the desktop. That is a stability risk to measure before raising it.

---

## 6. Open questions worth exploring next

1. Where does total throughput saturate on the 16 GB M5 for Qwen3.5-9B Q4_K_M and on a larger box for Qwen3.6-35B-A3B: B=8, 16 or 32? No public Apple data above B=4 was found.
2. Does llama-server's unified pool actually share prefix KV across slots, or does each slot prefill again?
3. How much per-slot state does llama.cpp or MLX allocate for hybrid (Gated DeltaNet) models, and does SWA or recurrent state cancel the prefix-cache gains?
4. Is 4-bit quantization damage concentrated in exact-copy behaviour (SEARCH blocks, JSON arguments)? If so, the copy-constrained grammar (T4, AWOS_EDIT_GRAMMAR) might recover most of it.
5. Can MoE models take expert-only low-bit quantization (Q3 experts, Q6/Q8 attention) at the 30B scale, as Kimi-K2's MoE-only INT4 suggests?
6. Does DFlash on Metal escape the serial-verification penalty, and does any speculation stay net-positive at 8 or more agents?
7. Where does useful work per (dollar · second · watt) peak: many slow parallel local agents, or fewer agents plus cloud escalation? Answering this needs wattage measured under batch load.
8. Unverified numbers to re-check when search is available: the exact Qwen3-8B dimensions, Gemma 3's local:global ratio and window size, exo throughput figures, and the `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` setting name.

---

## 7. Sources

- https://arxiv.org/abs/2411.02355
- https://arxiv.org/abs/2504.04823
- https://arxiv.org/abs/2505.19433
- https://arxiv.org/abs/2505.20276
- https://arxiv.org/abs/2407.09141
- https://developers.googleblog.com/en/gemma-3-quantized-aware-trained-state-of-the-art-ai-to-consumer-gpus/
- https://huggingface.co/blog/faster-transformers
- https://huggingface.co/moonshotai/Kimi-K2-Thinking
- https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LEARNED_QUANTS.md
- https://arxiv.org/abs/2411.17691
- https://arxiv.org/abs/2411.04330
- https://arxiv.org/abs/2502.02631
- https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md
- https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/
- https://arxiv.org/abs/2406.11235
- https://github.com/turboderp-org/exllamav3
- https://github.com/ikawrakow/ik_llama.cpp
- https://arxiv.org/abs/2405.14852
- https://github.com/ggml-org/llama.cpp/discussions/5063
- https://github.com/ggml-org/llama.cpp/discussions/5962
- https://arxiv.org/abs/2511.05502
- https://github.com/intel/auto-round
- https://github.com/ggml-org/llama.cpp/discussions/4167
- https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/tree/master/tools/server
- https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- https://arxiv.org/abs/2402.05099
- https://github.com/vllm-project/vllm-metal
- https://github.com/waybarrios/vllm-mlx
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
- https://arxiv.org/abs/2407.08608
- https://github.com/kvcache-ai/ktransformers
- https://github.com/exo-explore/exo
- https://arxiv.org/abs/2309.06180
- https://omlx.ai/benchmarks/fqabp6rx
- https://arxiv.org/abs/2312.11514
- https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html
- https://github.com/ggml-org/llama.cpp/issues/23752
- https://arxiv.org/abs/2607.17283
- https://github.com/youssofal/MTPLX
- https://z-lab.ai/projects/dflash/
- https://github.com/z-lab/dflash
- https://inco.ai/blog/dflash2/
- https://arxiv.org/abs/2411.04975
- https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
- https://github.com/guidance-ai/llguidance
- https://docs.vllm.ai/en/latest/features/speculative_decoding/
- https://arxiv.org/abs/2406.14066
- https://arxiv.org/abs/2408.11049
- https://arxiv.org/abs/2503.01840
- https://github.com/ggml-org/llama.cpp/pull/7412
- https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/
- https://arxiv.org/abs/2412.10319
- https://arxiv.org/abs/2502.01941
- https://arxiv.org/abs/2312.07104
- https://docs.vllm.ai/en/latest/design/prefix_caching.html
- https://arxiv.org/abs/2511.02230
- https://arxiv.org/abs/2507.07400
- https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct
- https://arxiv.org/abs/2503.19786
- https://arxiv.org/abs/2504.19874
- https://arxiv.org/abs/2402.02750
- https://arxiv.org/abs/2401.18079
- https://arxiv.org/abs/2410.10819
- https://arxiv.org/abs/2505.23416
- https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/generate.py
- https://arxiv.org/abs/2309.17453
- https://arxiv.org/abs/2405.16444

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method: GitHub, Hacker News and Hugging Face papers-search APIs, plus abstract pages and issue threads fetched directly. The arXiv listing API returned empty results this session, so arXiv coverage comes through HF search. Nothing here was measured on AWOS hardware.

### New since the chart
1. **SiliconBench, 2026-09-12** (https://arxiv.org/abs/2609.19169). It benchmarks nine Apple Silicon serving engines at concurrency 1-16 on chat and agent workloads, with quality checks. vllm-metal "more than doubled" throughput on Qwen3-0.6B. Two stacks "complete every request while memory use approaches physical capacity and throughput declines". This is the independent concurrency sweep the chart's Watch list and open question 1 asked for (up to 16, small model only). It also warns that a memory budget is not headroom, which matters for the per-agent KV sizing and the wired-limit risk.
2. **llama.cpp PR 29869, closed 2026-10-02** (https://github.com/ggml-org/llama.cpp/pull/29869). It adds Metal mat-mul kernels for 2-16 rows. The PR text says that on M1-M4 "DFlash2 decoding of Qwen3.8-27B is slower than serial decoding on master" because small batches fall back to mat-vec. A maintainer comment says the MMA path also helps M5. This bears on the batching and speculation claims. Multi-slot batched decode (2-16 rows) on Metal was under-optimised, so the chart's batched-bench numbers may improve once this lands in your build. It also partly explains the "serial verification" penalty on Metal.
3. **llama.cpp issue 27148, open, 2026-08-15** (https://github.com/ggml-org/llama.cpp/issues/27148). The RAM prompt cache (`--cache-ram` / `--cache-idle-slots`) can restore an unrelated conversation into a fresh slot under concurrent load. It reports `cached_tokens: 0`, so it looks like hallucination. The reporter's mitigation is `--no-cache-idle-slots --cache-ram 0`. This is a correctness risk for the chart's "adopt now" multi-slot advice. Disable these flags and check for cross-slot contamination in the concurrency sweep.
4. **TraceLab, 2026-06-29** (https://arxiv.org/abs/2606.30560). It releases about 4,300 real coding-agent sessions (about 350k LLM steps, 430k tool calls). Contexts are long and outputs short, and prefix-cache hit rates are "high but incomplete". It gives real-trace evidence for prefill-heavy, prefix-shared agents, and AWOS can replay it before building its own traces.
5. **AgentSpec, 2026-08-25, EMNLP 2026** (https://arxiv.org/abs/2608.24004). It states that existing speculative decoding methods "exhibit substantial speed degradation under large batch sizes", and proposes agent-aware drafting. This supports the chart's speculation-vs-batching claim. It is validated on vLLM, so it is not yet usable locally.
6. **HYPIC, 2026-07-01** (https://arxiv.org/abs/2607.01299). It adds position-independent caching for hybrid-attention (linear + full) models: 3.25x average TTFT, 1.66x QPS over prefix caching. It speaks to open question 3 (prefix reuse on Gated DeltaNet models). It is a server-side system and not in llama.cpp or MLX.
7. **PolyKV, 2026-04-27** (https://arxiv.org/abs/2604.24971). It shares one compressed KV pool across concurrent agents: 15 agents on Llama-3-8B at 4K context, 19.8 GB down to 0.45 GB, +0.57% perplexity. It was tested only on small models and short contexts. SAW-INT4 (https://arxiv.org/abs/2604.19157, 2026-04-21) reports that 4-bit KV with block-Hadamard rotation recovers nearly all accuracy lost to naive INT4. Both weaken the chart's "4-bit KV is a dead end" row. The dead-end verdict holds for plain q4_0 KV, but rotated 4-bit KV is now a credible arm. Several llama.cpp TurboQuant forks exist (for example https://github.com/AmesianX/TurboQuant, 93 stars), none upstream.
8. **Mix-Quant, 2026-05-19** (https://arxiv.org/abs/2605.20315). It quantizes prefill to FP4 and keeps decode in BF16, for up to 3x faster prefill on agentic workloads. This is GPU/FP4 oriented and not applicable on Apple hardware, but it indicates that quant damage differs by phase.
9. **DFlash and DSpark, 2026-06.** The SGLang blog (https://www.lmsys.org/blog/2026-06-15-next-generation-speculative-decoding-dflash-v2/) claims more than 4.3x on Qwen 3.5 397B at concurrency 1 and 1.5x over MTP, with gains kept through concurrency 32 (datacenter B200 scale, vendor claim). DSpark (https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf, HN 2026-06-27, 797 points) is a DeepSeek speculative decoding paper, and llama.cpp now has `draft-dspark`/`draft-dflash` modes. An open issue (https://github.com/ggml-org/llama.cpp/issues/25618) reports greedy output diverging from vanilla on quantized targets. Chart test 6 requires byte-identical output at temperature 0, so this is a direct risk.
10. **BaseRT, 2026-07-01** (https://arxiv.org/abs/2607.00501). A native Metal runtime claiming up to 1.56x decode over llama.cpp and 1.35x over MLX, on M3/M4 Pro with models up to 30B. It is a single-group claim. Treat as a watch item only.
11. **Release cadence.** vllm-metal is now at v0.30.0 (2026-09-23) (https://github.com/vllm-project/vllm-metal/releases). llama.cpp is at b11541 (2026-10-10). mlx-lm's last release is v0.31.3 (2026-04-22), which suggests MLX server development is slower than the llama.cpp and vllm-metal paths.

### Corrections
- Chart: "vllm-metal (v0.2.0)". The repo README still says v0.2.0 (2026/04), but GitHub releases list v0.29.0 (2026-09-11) and v0.30.0 (2026-09-23). The version is stale and the project is moving fast. Source: https://github.com/vllm-project/vllm-metal/releases
- Chart: "neither publishes concurrency sweeps" (vllm-metal / vllm-mlx). SiliconBench (https://arxiv.org/abs/2609.19169) now covers both-class engines at concurrency 1-16, but on a small model.
- Chart: llama.cpp MTP-on-Metal issue "closed as 'not a bug'". The issue (https://github.com/ggml-org/llama.cpp/issues/23752) was closed as completed on 2026-05-27. The "not a bug" framing is a maintainer comment, who also said multiple users, including Georgi, report sizeable Mac speed-ups and that MTP is optional. Another user reported slowdowns on M1 Max with Gemma 4 26B on 2026-06-11. The evidence is mixed and not a settled negative.
- Chart: "3 of 5 configs ran slower" and Lossless but Not Free. Confirmed, but the abstract also reports a best case of 1.61x at K=6 (https://arxiv.org/abs/2607.17283). The chart mentions only the negative side.

### Confirmed claims
- Apple MLX-on-M5: 153 GB/s, decode 19-27% faster than M4 (chart: 1.19-1.27x), prefill/TTFT 3.33-4.06x (chart: 3.3-4.1x). Source: https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- ACBench: 4-bit costs 1-3% on workflow/tool use and 10-15% on real-world application accuracy (https://arxiv.org/abs/2505.19433).
- Long-context quantization: 8-bit about 0.8% drop, 4-bit drops "of up to 59%" at 64K+, strongly dependent on model and method (https://arxiv.org/abs/2505.20276).
- Hydragen: up to 32x on CodeLlama-13b; 1K to 16K prefix costs under 15% versus over 90% for baselines (https://arxiv.org/abs/2402.05099).
- Continuum: more than 8x average job completion time (https://arxiv.org/abs/2511.02230). KVFlow: 1.83x single, 2.19x concurrent workflows, measured against SGLang's hierarchical radix cache (https://arxiv.org/abs/2507.07400).

### Still unverified
- Whether llama-server's unified pool shares prefix KV across slots (open question 2). Issue 27148 shows the cache layer is still buggy under concurrency, so test it before relying on it.
- Throughput saturation above B=4 on a 16 GB M5 for the 9B tier. SiliconBench stops at 16 and uses small models.
- Per-slot memory for hybrid models in llama.cpp or MLX, and the SWA/recurrent-state cost.
- Qwen3-8B KV dimensions, Gemma 3's local:global ratio, exo figures, the `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` name, and AWOS's own `-np`, `MAX_WORKERS` code facts (not rechecked here).
- Unsloth, oMLX and MTPLX figures remain self-reported. The DFlash and DSpark gains are datacenter-GPU vendor claims and not Metal numbers.
