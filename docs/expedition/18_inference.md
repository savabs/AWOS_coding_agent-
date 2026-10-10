# Inference stacks and serving

*AWOS research expedition, territory 18. Compiled 2026-10-10 from four scout reports (serving engines, serving techniques, local model management, hybrid routing/gateways). Method caveat: the session's shared WebSearch budget was exhausted before the scouts started, so every claim below comes from directly fetched primary pages (repos, official docs, arXiv abstracts) plus one live benchmark on the owner's M5 16 GB. Recent releases may be missing. Vendor numbers are marked "self-reported".*

Prior repo research this builds on (not repeated here): `docs/research/local_first_architecture_2026-10.md`, `docs/research/trick_book_2026-10.md`.

---

## 1. Summary

- **Running more agents in parallel on a local model is a configuration problem, not a hardware problem.** Every serious engine (llama.cpp, mlx-lm, vLLM, SGLang, oMLX) now does continuous batching. AWOS today runs one local agent at a time because `scripts/local_model.sh` starts llama-server with `-np $SLOTS` and `AWOS_LOCAL_SLOTS` defaults to **1** (verified in the repo, line 30). The "max 8 agents" cap the owner hit is a separate limit set by the Claude Code workflow harness. It is not an AWOS or engine limit.
- **Decode batches almost for free; prefill does not.** On Apple Silicon, decode is bandwidth-bound (M5 generation only 1.19–1.27x faster than M4), while prefill is compute-bound (M5 TTFT 3.3–4.1x faster). A live run on the M5 showed total decode throughput rising 3.6x from 1 to 16 sequences, with prefill flat at about 200 tok/s. With many agents, prefill is the cost, and prefix caching is the lever.
- **KV memory, not weights, sets the agent ceiling**, and the hybrid Qwen3.5-9B makes it unusually cheap: about 32 KB/token, so about 0.8 GB per 24k slot (scout arithmetic from the model card). The catch is that its recurrent state can only be restored at checkpoints (default every 8192 tokens), which may silently defeat AWOS's byte-stable prefix work.
- **Many local servers silently run requests one at a time:** Ollama `NUM_PARALLEL=1` by default, mlx-lm with `--kv-bits` or a draft model, Foundry Local by design. "More agents" against such a server just makes a longer queue.
- **Agent-aware KV scheduling is the 2025–26 research frontier.** Continuum (TTL pinning during tool calls, >8x job completion time), Autellix (4–15x), and KVFlow (1.83–2.19x) all beat request-level LRU on agent traces. All are self-reported on datacenter GPUs.
- **Model management is now commodity.** llama.cpp router mode, `--fit`, host-RAM prompt cache and slot save/restore, plus oMLX (multi-model LRU, SSD-tiered KV cache that survives restarts), cover what AWOS would otherwise build.
- **On hybrid routing, theory backs Gatekeeper:** a cascade with a strong quality estimator beats pre-routing, and deterministic tests are about the strongest estimator there is. Commercial auto-routers rank poorly. LiteLLM had a PyPI credential-stealer incident in March 2026. For the cloud tier, the concurrency limit is OpenRouter's in-flight credit pre-authorization, not RPM.

---

## 2. What matters most (ranked)

1. **Turn on parallel slots in the engine AWOS already runs.** llama-server already has continuous batching (`--cont-batching` default on). Only `-np` is holding it back. llama.cpp's own batched-bench shows 41.6 → 465 tok/s going from B=1 to B=32 (about 11x total, LLaMA-7B, hardware not stated). Live on the M5 16 GB with Qwen3.5-9B Q4_K_M, total generation tok/s by number of parallel sequences: 1 → 6.6, 4 → 15.9, 8 → 17.5, **16 → 23.6** (box heavily contended: load average 40, 7.6 GB swap, so read only the shape). Sources: [batched-bench](https://github.com/ggml-org/llama.cpp/blob/master/tools/batched-bench/README.md), [server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

2. **Prefix/radix caching is the biggest single win for agent workloads.** SGLang RadixAttention: up to 6.4x on agent, JSON and multi-turn workloads ([2312.07104](https://arxiv.org/abs/2312.07104)). Hydragen: up to 32x with shared prefixes, and under 15% throughput loss going from 1K to 16K shared context vs more than 90% for baselines ([2402.05099](https://arxiv.org/abs/2402.05099)). On the M5, prefill is the bottleneck when many agents run, so prefix reuse matters more than slot count ([Apple ML Research](https://machinelearning.apple.com/research/exploring-llms-mlx-m5)).

3. **Hybrid-model checkpoint trap (urgent to measure).** Qwen3.5-9B uses Gated DeltaNet in 3 of every 4 layers ([model card](https://huggingface.co/Qwen/Qwen3.5-9B)). Recurrent state cannot be truncated to an arbitrary token, so prefix reuse only works at context checkpoints: `--ctx-checkpoints` defaults to 32 per slot and `--checkpoint-min-step` to 8192 tokens. Checkpoints reportedly cut "several minutes" of reprocessing to "a few seconds" ([PR 15293](https://github.com/ggml-org/llama.cpp/pull/15293)). If the T7b stable prefix does not end on a checkpoint, forks for pass@k, repair and the arbiter may re-prefill everything. Check `timings.cache_n` before trusting any caching gain.

4. **Host-RAM prompt cache lets one box serve more agents than it has slots.** `--cache-ram` (default 8 GiB) parks idle slot prompts in host RAM and swaps the best match back in, for dense, MoE, SWA and SSM models. With it, "a single server slot" can serve the llama.vscode agent "without trashing the prompt cache" ([PR 16391](https://github.com/ggml-org/llama.cpp/pull/16391)). Agents sit idle for seconds to minutes during tests, so this matters.

5. **Agent-aware KV scheduling.** Continuum keeps an agent's KV pinned for a time-to-live while it waits on a tool call: average job completion time improves by more than 8x on SWE-Bench and OpenHands traces ([2511.02230](https://arxiv.org/abs/2511.02230)). Autellix (program-level scheduling) gives 4–15x vs vLLM ([2502.13965](https://arxiv.org/abs/2502.13965)). KVFlow uses a step graph to evict and prefetch: 1.83x on one workflow, 2.19x on concurrent workflows vs SGLang HiCache ([2507.07400](https://arxiv.org/abs/2507.07400)). All self-reported. AWOS's orchestrator already knows which worker is waiting on tests, so most of this can be done with sticky `id_slot` plus pinning, without forking an engine.

6. **Avoid settings that silently disable batching.** Ollama: `OLLAMA_NUM_PARALLEL` defaults to 1, and RAM scales as NUM_PARALLEL x CONTEXT_LENGTH ([FAQ](https://docs.ollama.com/faq)). mlx-lm: "A quantized KV cache does not support batching"; `--kv-bits` or a draft model puts the server back to serial ([SERVER.md](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md), [server.py](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py), defaults: decode concurrency 32, prompt concurrency 8). LM Studio only gained batching in 0.4.0, with 4 concurrent requests and llama.cpp engine only ([blog](https://lmstudio.ai/blog/0.4.0)).

7. **Mac-native servers with SSD-tiered KV that survives restarts.** oMLX: continuous batching via BatchGenerator, hot (RAM) and cold (SSD, safetensors) KV tiers, multi-model LRU with pinning, memory cap (default RAM minus 8 GB), Anthropic `/v1/messages` ([repo](https://github.com/jundot/omlx)). Self-reported on M4 Max with Qwen3.6-35B-A3B 4-bit: 108.8 tok/s single stream, 1.72x total at batch 2, 2.49x at batch 4 ([benchmark](https://omlx.ai/benchmarks/fqabp6rx)). vllm-mlx is similar ([repo](https://github.com/waybarrios/vllm-mlx)). These are the only engines here offering restart-surviving prefix KV for an always-on host.

8. **Cascade with a strong gate beats routing.** Dekoninck et al. (ICLR 2025) show cascade routing beats either cascading or routing alone "by a large margin", and that the quality estimator decides the outcome ([2410.10347](https://arxiv.org/abs/2410.10347)). RouteLLM's savings at 95% GPT-4 quality are 85% on MT-Bench but only 45% on MMLU and 35% on GSM8K, and they depend on about 1,500 in-domain labels ([LMSYS](https://lmsys.org/blog/2024-07-01-routellm/)).

9. **Use provider-reported cost, and know the real cloud concurrency limit.** OpenRouter returns `usage.cost`, cache read/write tokens and reasoning tokens on every response ([usage accounting](https://openrouter.ai/docs/use-cases/usage-accounting)). Paid-model concurrency is limited by an "in-flight spending budget" that reserves input + max_tokens per request, and returns 402 `weight_exceeds_budget` even with a positive balance ([limits](https://openrouter.ai/docs/api-reference/limits)). Oversized max_tokens throttles parallel bench fan-out.

10. **Chunked prefill keeps parallel agents responsive.** Sarathi-Serve: 2.6x serving capacity under tail-latency limits ([2403.02310](https://arxiv.org/abs/2403.02310)). AWOS inputs are 13–20x larger than outputs, so a new agent's 20–30k-token prompt otherwise stalls every running decode. Tune `-b`/`-ub` (llama.cpp) or `--prompt-concurrency` (mlx).

---

## 3. What does NOT matter / hype / dead ends

| Item | Why not |
|---|---|
| **Prefill/decode disaggregation on one box** | vLLM's own docs: "Disaggregated prefill DOES NOT improve throughput" ([docs](https://docs.vllm.ai/en/latest/features/disagg_prefill.html)). DistServe-style gains need separate devices. Only relevant once AWOS has two or more inference machines. |
| **SGLang / TensorRT-LLM on the Mac** | No Metal or MLX support ([SGLang docs](https://docs.sglang.io/)). Borrow the radix-cache idea and use them only on a rented CUDA tier. |
| **Quantized KV cache to fit more agents** | It disables batching in mlx-lm. The trick book records large reasoning losses at 4-bit KV. With hybrid Qwen3.5, KV per slot is already small. |
| **Raising agent count without changing the server** | On a serial server or a swapping 16 GB box, more agents means a longer queue or worse. |
| **Self-reported "Nx" headlines** | vllm-metal's "83x TTFT" is measured against its own v0.1.0 ([repo](https://github.com/vllm-project/vllm-metal)). Bifrost "50x faster than LiteLLM" (11 µs) and Portkey "<1 ms" are irrelevant against LLM calls that take seconds ([bifrost](https://github.com/maximhq/bifrost)). |
| **Grammar-engine speed tuning** | Solved: llguidance about 50 µs/mask for a 128k vocabulary ([repo](https://github.com/guidance-ai/llguidance)); XGrammar-2 near-zero overhead ([2601.04426](https://arxiv.org/abs/2601.04426)). The open question is *what* to constrain. Format constraints hurt reasoning ([2408.02442](https://arxiv.org/abs/2408.02442)). |
| **Commercial auto-routers as the escalation decision** | RouterArena: NotDiamond 29th, Azure Model Router 19th, below specialized routers ([RouterArena](https://github.com/RouteWorks/RouterArena)). OpenRouter Auto picks by 7-day market spend share ([docs](https://openrouter.ai/docs/features/model-routing)), not correctness. |
| **Full LiteLLM proxy on the owner's box** | v1.82.7/8 shipped credential-stealing malware on 2026-03-24 ([postmortem](https://docs.litellm.ai/blog/security-update-march-2026)), plus 2026 critical auth advisories. Wrong trade for a private host holding every key. Copy its policy ideas instead ([routing docs](https://docs.litellm.ai/docs/routing)). |
| **Semantic response caching** | For code edits and UI actions, a similar request rarely has the same correct answer, and stale hits are silent errors. Exact prefix KV caching plus verified replay is the safe version. |
| **Ollama as the long-term engine; Apple FM adapters** | Ollama lags upstream and has agent-hostile defaults (5-minute keep_alive, NUM_PARALLEL=1). Apple's adapter toolkit 26.0.0 is the last release and is incompatible with OS 27 ([Apple](https://developer.apple.com/apple-intelligence/foundation-models-adapter/)). |
| **Building a custom model registry or downloader** | HF `-hf`, OCI artifacts ([Docker Model Runner](https://docs.docker.com/ai/model-runner/)) and llama.cpp router auto-discovery already cover it. |

---

## 4. Key papers and resources

### Must-read
- **llama.cpp server README**: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md. Every local parallelism lever: `-np`, `--kv-unified`, `--cache-ram`, `--ctx-checkpoints`, `--spec-type`, `/slots` save/restore, router mode, `--fit`, metrics.
- **llama-batched-bench**: https://github.com/ggml-org/llama.cpp/blob/master/tools/batched-bench/README.md. Already installed. One command gives the throughput-vs-N curve that sets the agent fan-out.
- **Continuum (KV TTL for multi-turn agents)**: https://arxiv.org/abs/2511.02230. Closest match to AWOS's pause-for-tests pattern.
- **llama.cpp PR 15293 (context checkpoints)**: https://github.com/ggml-org/llama.cpp/pull/15293, and **PR 16391 (host-RAM prompt cache)**: https://github.com/ggml-org/llama.cpp/pull/16391.
- **Apple ML Research, LLMs with MLX on M5**: https://machinelearning.apple.com/research/exploring-llms-mlx-m5. Separates the prefill gain from the decode gain.
- **Unified Routing and Cascading**: https://arxiv.org/abs/2410.10347. Formal basis for Gatekeeper.
- **oMLX**: https://github.com/jundot/omlx. Strongest Mac always-on host candidate.

### Useful
- SGLang RadixAttention https://arxiv.org/abs/2312.07104 · Hydragen https://arxiv.org/abs/2402.05099 · KVFlow https://arxiv.org/abs/2507.07400 · Autellix https://arxiv.org/abs/2502.13965 · Sarathi-Serve https://arxiv.org/abs/2403.02310
- mlx-lm server.py / SERVER.md / releases: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py, https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md, https://github.com/ml-explore/mlx-lm/releases (logits processors now work in batch generation, relevant to the T4 grammar).
- Apple Silicon runtime comparison (MLX best sustained throughput, MLC lowest TTFT, Ollama behind): https://arxiv.org/abs/2511.05502
- llama.cpp model management blog (router: process per model, LRU default 4): https://huggingface.co/blog/ggml-org/model-management-in-llamacpp
- Ollama scheduling, measure instead of estimate (self-reported 52 → 85.5 tok/s): https://ollama.com/blog/new-model-scheduling
- RouteLLM https://lmsys.org/blog/2024-07-01-routellm/ · RouterArena https://github.com/RouteWorks/RouterArena · UniRoute https://arxiv.org/abs/2502.08773
- OpenRouter prompt caching / usage / limits / provider routing: https://openrouter.ai/docs/features/prompt-caching, https://openrouter.ai/docs/use-cases/usage-accounting, https://openrouter.ai/docs/api-reference/limits, https://openrouter.ai/docs/features/provider-routing
- DeepSeek pricing (cache hit vs miss of about 50x; off-peak 50% cheaper): https://api-docs.deepseek.com/quick_start/pricing
- "Give Me BF16 or Give Me Death" (W4A16 best for single stream, W8A8 under batching): https://arxiv.org/abs/2411.02355
- vllm-metal https://github.com/vllm-project/vllm-metal · vllm-mlx https://github.com/waybarrios/vllm-mlx
- llguidance https://github.com/guidance-ai/llguidance · XGrammar-2 https://arxiv.org/abs/2601.04426 · JSONSchemaBench https://arxiv.org/abs/2501.10868 · Let Me Speak Freely https://arxiv.org/abs/2408.02442

### Reference
- SGLang docs https://docs.sglang.io/ · SGLang HiCache https://lmsys.org/blog/2025-09-10-sglang-hicache/ · vLLM disagg prefill https://docs.vllm.ai/en/latest/features/disagg_prefill.html · vLLM sleep mode https://docs.vllm.ai/en/latest/features/sleep_mode.html · ServerlessLLM https://www.usenix.org/conference/osdi24/presentation/fu
- llama-swap https://github.com/mostlygeek/llama-swap · LocalAI https://github.com/mudler/LocalAI · Lemonade https://github.com/lemonade-sdk/lemonade · Foundry Local https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-local/what-is-foundry-local · Docker Model Runner https://docs.docker.com/ai/model-runner/
- llmfit https://github.com/AlexsJones/llmfit · gguf-parser-go https://github.com/gpustack/gguf-parser-go · LM Studio TTL https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict
- RouterEval https://arxiv.org/abs/2503.10657 · RouterBench https://arxiv.org/abs/2403.12031 · FrugalGPT https://arxiv.org/abs/2305.05176 · Hybrid LLM https://arxiv.org/abs/2404.14618 · Router-R1 https://arxiv.org/abs/2506.09033 · vLLM Semantic Router https://github.com/vllm-project/semantic-router · LiteLLM postmortem https://docs.litellm.ai/blog/security-update-march-2026

---

## 5. Implications for AWOS

### The direct answer to "why only 8 agents, can we run more?"
There are three separate caps, and they need different fixes:
1. **The 8-subagent cap is a Claude Code workflow-harness setting.** It is outside AWOS code, and the research cannot raise it. A related limit is shared per-session quotas: the 200-search WebSearch budget ran out across all agents before this expedition's scouts could search. A higher agent cap would have hit that wall sooner.
2. **AWOS's local tier runs 1 agent at a time** because `AWOS_LOCAL_SLOTS` defaults to 1. This is the cheap win.
3. **AWOS's cloud tier fan-out** is limited by OpenRouter in-flight credit pre-authorization (input + max_tokens per request), not by agent count.

### Adopt now (Gatekeeper "local attempt" and host)
- **Raise `AWOS_LOCAL_SLOTS` to 4–8** and add `--kv-unified`, `--cache-ram`, q8_0 or f16 KV, and 16–24k context per slot. Pick N from a clean (idle-host) batched-bench run at `-npl 1,2,4,8,16,32` with realistic 8–16k prefixes. On 16 GB, expect the knee around 8. The contended run already swapped. Larger hosts go higher.
- **Sticky slot per worker** (`id_slot`), plus Continuum-style pinning while a worker waits on the verification gate (tests run). Gatekeeper's gate time is exactly the idle window these papers exploit.
- **Run pass@k and repair forks off one cached prefix in parallel** instead of as sequential retries. The gate picks the winner, which turns idle decode bandwidth into solve rate.
- **Fix the cost axis:** make provider-reported `usage.cost` (with `/generation` reconciliation) the single source of truth in `budget_ledger.py`, and give local calls a non-zero cost per second (amortized hardware plus measured watts), following LiteLLM's `input_cost_per_second` convention. Without this, the objective (work per $·s·W) is not measured honestly.
- **Cloud escalation hygiene:** set realistic per-tier max_tokens, give each bench worker its own OpenRouter sub-key, filter out fp4/int4 hosts with OpenRouter's `quantizations`, set `zdr`/`data_collection: deny` for privacy, and send `session_id` for cache stickiness (lost after 10 minutes idle).

### Test next
- **Hybrid checkpoint alignment:** measure `timings.cache_n` on a fork right after the T7b prefix, with default `--checkpoint-min-step 8192` and with smaller values or `--swa-full`. This may decide whether T7b's caching gains are real for Qwen3.5.
- **Head-to-head engines on the M5** under the same many-agent coding load: llama-server `-np N` vs oMLX vs vllm-metal, measured as solves per watt-second, not tok/s.
- **oMLX SSD-tiered KV** for verified-routine replay: restore a warm prefix per routine after a restart. Also measure SSD write volume. llama-server `/slots` save/restore is the fallback.
- **Speculation vs batching per mode:** llama-server `ngram-mod` (draft tokens copied from the prompt) suits SEARCH/REPLACE editing. Its gains shrink as batch size grows, and mlx-lm disables batching with a draft model. Hypothesis: use speculation for one urgent worker and batching for many.
- **Grammar under batching:** check that mlx-lm logits-processor batching keeps the batch speedup with the T4 copy-constrained grammar.
- **Off-peak queueing:** schedule non-urgent cloud work (idle practice, regressions, distillation data) into DeepSeek off-peak windows for about 2x work per dollar.

### Watch
- vllm-metal maturity (one engine for the Mac and a rented GPU). KVFlow-style schedule hints exposed by engines. XGrammar-2 for dynamic tool-call grammars. UniRoute-style correctness vectors for adding new models to the escalation ladder. Shadow mode (mirroring cloud-solved tasks to the local model when idle) to collect paired labels for a cheap kNN "try local first?" prior.

### Ignore
- P/D disaggregation (until there are two or more boxes), heavyweight gateways, commercial auto-routers, semantic caching, quantized KV for concurrency, neural pre-routers for now, and Apple FM adapters.

### Local computer use
Computer-use agents have long, slowly changing prefixes (system prompt, app card, screen history) and long idle gaps (UI waits). That is the same shape as coding but more extreme. Prefix KV that survives restarts (oMLX SSD tier or slot save/restore), TTL pinning during waits, and per-model residency (pin L0, give the VLM and embedder TTLs through router mode or oMLX) carry over directly. Store a model manifest (digest + quant + runtime flags) with every verified routine so replay runs on the exact model it was verified with.

---

## 6. Open questions worth exploring next

1. On an idle M5 16 GB, where is the throughput knee for Qwen3.5-9B and the 35B-A3B MoE, and at what N does per-agent latency become unacceptable for the gate loop? The live run was contaminated by load from the other agents.
2. Does llama.cpp actually reuse the hybrid model's prefix when a fork diverges right after the stable prefix? (Unmeasured; critical for T7b.)
3. Is local parallel pass@k better than sequential retries on solves per watt-second?
4. Is engine-level `--cache-ram` enough, or does AWOS need orchestrator-level TTL pinning?
5. Does oMLX's SSD KV tier cut TTFT measurably across restarts, and what is the SSD write cost for an always-on host?
6. Does 8-bit weight quantization beat 4-bit under heavy batching on bandwidth-bound Apple Silicon, as 2411.02355 suggests for GPUs?
7. Can `--fit` or gguf-parser estimates (reported error about 100 MiB) be trusted to admit or deny agent slots automatically?
8. Unchecked because the search budget ran out: 2026 papers on local-vs-cloud routing specifically for agentic or tool-use tasks; Unsloth Dynamic 2.0 quant claims (page returned 403); independent gateway security comparisons.

---

## 7. Sources

- https://github.com/ggml-org/llama.cpp/tree/master/tools/batched-bench
- https://github.com/ggml-org/llama.cpp/blob/master/tools/batched-bench/README.md
- https://github.com/ggml-org/llama.cpp/tree/master/tools/server
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/pull/15293
- https://github.com/ggml-org/llama.cpp/pull/16391
- https://huggingface.co/blog/ggml-org/model-management-in-llamacpp
- https://huggingface.co/Qwen/Qwen3.5-9B
- https://docs.ollama.com/faq
- https://raw.githubusercontent.com/ollama/ollama/main/docs/faq.mdx
- https://ollama.com/blog/new-model-scheduling
- https://lmstudio.ai/blog/0.4.0
- https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
- https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py
- https://github.com/ml-explore/mlx-lm/releases
- https://github.com/jundot/omlx
- https://omlx.ai/benchmarks/fqabp6rx
- https://github.com/vllm-project/vllm-metal
- https://github.com/waybarrios/vllm-mlx
- https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- https://arxiv.org/abs/2511.05502
- https://docs.sglang.io/
- https://github.com/mudler/LocalAI
- https://arxiv.org/abs/2507.07400
- https://arxiv.org/abs/2511.02230
- https://arxiv.org/abs/2502.13965
- https://arxiv.org/abs/2312.07104
- https://arxiv.org/abs/2402.05099
- https://arxiv.org/abs/2403.02310
- https://docs.vllm.ai/en/latest/features/disagg_prefill.html
- https://docs.vllm.ai/en/latest/features/sleep_mode.html
- https://github.com/guidance-ai/llguidance
- https://arxiv.org/abs/2601.04426
- https://arxiv.org/abs/2408.02442
- https://arxiv.org/abs/2501.10868
- https://lmsys.org/blog/2025-09-10-sglang-hicache/
- https://github.com/AlexsJones/llmfit
- https://github.com/gpustack/gguf-parser-go
- https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-local/what-is-foundry-local
- https://github.com/lemonade-sdk/lemonade
- https://docs.docker.com/ai/model-runner/
- https://github.com/mostlygeek/llama-swap
- https://www.usenix.org/conference/osdi24/presentation/fu
- https://arxiv.org/abs/2411.02355
- https://developer.apple.com/apple-intelligence/foundation-models-adapter/
- https://arxiv.org/abs/2410.10347
- https://lmsys.org/blog/2024-07-01-routellm/
- https://github.com/RouteWorks/RouterArena
- https://arxiv.org/abs/2503.10657
- https://arxiv.org/abs/2403.12031
- https://arxiv.org/abs/2502.08773
- https://arxiv.org/abs/2305.05176
- https://arxiv.org/abs/2404.14618
- https://arxiv.org/abs/2506.09033
- https://openrouter.ai/docs/features/prompt-caching
- https://openrouter.ai/docs/use-cases/usage-accounting
- https://openrouter.ai/docs/api-reference/limits
- https://openrouter.ai/docs/features/provider-routing
- https://openrouter.ai/docs/features/model-routing
- https://docs.litellm.ai/blog/security-update-march-2026
- https://docs.litellm.ai/docs/routing
- https://github.com/maximhq/bifrost
- https://github.com/vllm-project/semantic-router
- https://api-docs.deepseek.com/quick_start/pricing
