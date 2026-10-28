# The sweet-spot model for everyday tasks

*AWOS research expedition, territory 09. Compiled 2026-10-10 from four scout reports: capability sufficiency, complexity routing, 3-14B reliability, and OS-vendor on-device choices.*

> **Method caveat.** The session-wide WebSearch budget ran out before any scout made a search. Every source below was found by fetching known primary URLs directly (arXiv, leaderboards, vendor docs and blogs). So coverage of independent replications and user studies is thin. Most numbers are self-reported by the authors or vendors, and some arrived through an automatic page summarizer. They are marked where it matters. Several 2026 arXiv papers cited here (for example 2607.x to 2610.x) have not been replicated by anyone.

---

## 1. Summary

- **There is no single "enough" model size. It depends on the kind of step.** On the independent BFCL V4 leaderboard, single tool calls are saturated at about 80-90% from 1.5B upward. Multi-turn accuracy collapses below about 8B (Qwen3-1.7B 11.0, Qwen3-0.6B 3.6, Gemma-3-4B 0.4), and web-search and memory work collapses for nearly all small models.
- **Three OS vendors converged on about 3B at 2-4 bits** as the always-resident model: Apple's on-device model (~3B, 2-bit QAT), Gemini Nano (1.8B/3.25B, 4-bit) and Phi Silica (~3.3B, 4-bit). All three limit it to bounded text transforms over supplied context: summarize, extract, rewrite. All three wrap it in constrained output and short sessions of 2-4K tokens.
- **Specialization beats size on a closed tool set.** xLAM-2-8b scores 70.0 on BFCL multi-turn, against 68.4 for Claude Opus 4.5. TinyAgent-1.1B reaches 80.1% on 16 macOS assistant functions, against 79.1% for GPT-4-Turbo, with about $500 of synthetic data. Both fall apart on open-world tasks (xLAM-2 web search is 6-15).
- **Reliability, not pass@1, is the real bar, and no size delivers it alone.** On tau-bench, GPT-4o drops from under 50% (pass^1) to under 25% (pass^8). Multi-turn underspecification costs about 39% on average, mostly as unreliability. Scaffolding (decomposition, voting, red-flagging, external verifiers) is what makes cheap models reliable. MAKER ran 1M steps with zero errors.
- **Local computer use is the weakest area.** Fara-7B handles structured web tasks (WebVoyager 73.5 self-reported, 62% in Browserbase's human-annotated check). On macOSWorld, open lightweight models score under 5%, against over 30% for proprietary agents.
- **Simple routing wins.** LLMRouterBench (400K instances, 33 models) found most routers, commercial ones included, fail to reliably beat simple baselines. kNN over past outcomes is a strong default. The one rigorous self-audit of a step-level router cut a claimed 23.9% saving to 4.3%.
- **The sweet spot keeps moving.** Capability density is reported to double about every 3 months (Densing Law). Vendors swap system models silently: Apple in 26.4 and again in 27, Microsoft from Phi Silica to Aion Instruct in Jan 2027. Re-qualifying models continuously matters more than any one pick.

---

## 2. What matters most (ranked)

**1. Route by step type and tool-set closure, not by topic.** Single calls into a closed tool set work at 1-4B. Multi-turn stateful work needs roughly 8B fine-tuned or a 30B-class model. Open-world search, memory and native desktop use need the cloud or a replayed routine. Evidence: BFCL V4 single-call column (Hammer2.1-1.5B 83.0, Llama-3.1-8B 84.0, Opus 4.5 about 88) against multi-turn (Qwen3-8B 41.8, Haiku 4.5 53.6). The xLAM-2 tau-bench curve: 1B 21.8, 3B 38.2, 8B 46.7, 32B 54.6, 70B 56.2, GPT-4o 52.9. The curve flattens past about 32B. [BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html) · [data CSV](https://gorilla.cs.berkeley.edu/data_overall.csv) · [APIGen-MT](https://apigen-mt.github.io/)

**2. Measure pass^k on the owner's repeated tasks.** Small models fail mainly through variance, not lack of average capability. tau-bench defines pass^k. WorkBench (690 workplace tasks) found GPT-4 at 43% and documented wrong-recipient emails. Temperature 0 is not deterministic on servers: one test gave 80 unique outputs in 1,000 runs, and a 7B model's accuracy moved by up to 9% with batch size or GPU. [tau-bench](https://arxiv.org/abs/2406.12045) · [WorkBench](https://arxiv.org/abs/2405.00823) · [Thinking Machines](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/) · [LayerCast](https://arxiv.org/abs/2506.09501)

**3. External verification and scaffolding carry reliability.** MAKER uses one step per micro-agent and first-to-ahead-by-k voting. With k=3, gpt-4.1-mini at about 0.22% per-step error finished 1,048,575 steps with zero errors, at a cost of Theta(s ln s). Red-flagging means discarding any answer that breaks format or runs past about 700 tokens and resampling instead of repairing; it cuts correlated errors. Intrinsic self-correction without an external signal does not reliably help. Weak models' self-validation collapsed from 100% (Sonnet) to 0% (Gemini Flash). [MAKER](https://arxiv.org/abs/2511.09030) · [Huang et al.](https://arxiv.org/abs/2310.01798) · [Capability Equalizer](https://arxiv.org/abs/2608.21747)

**4. Constrain every small-model output.** In JSONSchemaBench, constrained decoding lifted Llama-3.1-8B on GSM8K from 80.1% to 83.8% (Guidance), and Guidance decoded faster: 6-9 ms per token against 15-16 ms. Engines differ widely in schema coverage (Outlines and XGrammar fell to 0.03-0.07 on hard schemas). The "format hurts reasoning" result largely came from mismatched prompts. All three OS vendors ship constrained decoding as a core feature. Ministral-8B's 0.0 single-call score on BFCL is a format failure, not a capability floor. [JSONSchemaBench](https://arxiv.org/abs/2501.10868) · [dottxt rebuttal](https://blog.dottxt.ai/say-what-you-mean.html) · [Chrome Prompt API](https://developer.chrome.com/docs/ai/prompt-api)

**5. Specialize on the owner's tools (fine-tune, LoRA, RL).** Evidence: xLAM-2 (synthetic multi-turn data), TinyAgent (80k examples for about $500; 4-bit cost nothing: 80.4% against 80.1%), LOOP (RL on a 32B model beat o1 by 9 points on AppWorld), and IF-RLVR (Tulu-3-8B went from 28.9 to 45.9 on IFBench's unseen constraints). Counter-evidence: Apple discontinued per-version system-model adapters for OS 27 because they had to be retrained for every model update. Pin fine-tunes to a base model you control. [TinyAgent](https://arxiv.org/abs/2409.00608) · [LOOP](https://arxiv.org/abs/2502.01600) · [IFBench](https://arxiv.org/abs/2507.02833) · [Apple adapter toolkit](https://developer.apple.com/apple-intelligence/foundation-models-adapter/)

**6. Hard-gate irreversible actions, because "when not to act" is the small model's weakest skill.** BFCL irrelevance detection: Llama-3.1-8B 42.7%, xLAM-2-8b 63.3%, Gemini-2.5-Flash 93.7%. In a real Home Assistant study, Gemma4 E2B actuated on 87.2% of requests for devices the owner did not have. A 4B model did worse than its 2B predecessor at grounded actuation. ToolSandbox and When2Call confirm the weakness, and When2Call finds preference optimization beats plain fine-tuning for fixing it. [Per-call-site study](https://arxiv.org/abs/2610.09021) · [ToolSandbox](https://arxiv.org/abs/2408.04682) · [When2Call](https://arxiv.org/abs/2504.18851)

**7. Keep context short and fresh.** Apple's on-device model has a 4,096-token session window, and Apple's guidance is to decompose the work, open a new session per step and keep instructions to 1-3 paragraphs. Context rot: reliability falls with input length and distractors across 18 models. IFScale: even frontier models reach only 68% at 500 instructions, with a bias toward earlier ones. Self-conditioning: models err more once their own mistakes are in context. For repair, restart from a consolidated prompt. [TN3193](https://developer.apple.com/documentation/technotes/tn3193-managing-the-on-device-foundation-model-s-context-window) · [Context Rot](https://www.trychroma.com/research/context-rot) · [IFScale](https://arxiv.org/abs/2507.11538) · [Lost in Multi-Turn](https://arxiv.org/abs/2505.06120) · [Long-horizon execution](https://arxiv.org/abs/2509.09677)

**8. Escalate at attempt boundaries.** Verbal confidence at completion reaches AUROC 0.85. No signal tops 0.60 at 50% progress, because trajectories switch paths. Switching models mid-session also throws away the prompt cache. [Last Step Matters](https://arxiv.org/abs/2608.29685)

**9. Latency and watts: thinking off, prefill-optimized, MoE.** BFCL latencies: Qwen3-32B with function calling 169.9 s, xLAM-2-8b 22.7 s, Haiku 4.5 1.7 s. TinyAgent-7B at 4-bit took 13.1 s on a 2024 Mac, against 3.9 s for GPT-4-Turbo. Vendor engineering focuses on prefill: Gemini nano-v3 prefills at 940 tok/s, and Apple's KV sharing cuts TTFT by about 37.5%. [Gemini Nano blog](https://android-developers.googleblog.com/2025/08/the-latest-gemini-nano-with-on-device-ml-kit-genai-apis.html) · [Apple tech report](https://arxiv.org/html/2507.13575)

**10. A tiny distilled model fits a fixed OS action space.** Microsoft's Windows Settings agent uses Mu, a 330M encoder-decoder trained on 3.6M synthetic samples. It scores 0.738, against 0.815 for a fine-tuned Phi-3.5-mini, at one-tenth the size and under 500 ms. Short queries go to a lexical/semantic search fallback. [Mu](https://blogs.windows.com/windowsexperience/2025/06/23/introducing-mu-language-model-and-how-it-enabled-the-agent-in-windows-settings/)

---

## 3. What does NOT matter: hype and dead ends

| Thing | Why it doesn't matter |
|---|---|
| MMLU, GPQA and IFEval for picking an agent model | Apple ships a 3B that trails Qwen-3-4B by about 7 MMLU points. Tuned 8B models score 88-92 on IFEval but 45-54 on IFBench's unseen constraints. |
| Single-call function-calling accuracy | Saturated at 80-90% from 1.5B upward, so it doesn't separate models. |
| Vendor model-card agent scores (Qwen3.5-4B TAU2 79.9, OSWorld-Verified 35.6; 9B OSWorld 41.8) | Self-reported. Earlier generations dropped sharply on re-test (the repo saw Qwen3.6 SWE-bench fall from 77 to 31; UI-TARS-1.5-7B scored about 27 against a claimed 42.5). Treat them as hypotheses to test. |
| Sub-1B general models as agent workers | Multi-turn scores near zero (Qwen3-0.6B 3.6). Only useful as narrow routers or classifiers (Mu, TinyAgent). |
| Learned or commercial routers, and bigger model pools | LLMRouterBench: they don't beat simple baselines, and adding models gives diminishing returns. |
| Headline step-level routing savings (72%, 78%, 74-82%) | Self-reported and unreplicated. The one self-audit cut a claimed saving by more than 5x, and zero-shot routing models scored at chance. |
| NVIDIA's "40-70% of calls are replaceable" | A position paper with author estimates. Measured routing (Hybrid LLM) gives about 40% fewer large-model calls. |
| Mid-trajectory "stuck" signals for long episodes | AUROC ≤0.60 mid-run, and switching models loses the cache. |
| Self-critique loops without external signal | No reliable gain, sometimes harmful. |
| Temperature 0 as a determinism guarantee | Server kernels aren't batch-invariant. Up to 15% swings between runs have been reported. |
| Sub-4-bit quantization of 3-8B models for agent work | Degradation grows as bits, model size and task difficulty interact. W4A16 is near-lossless, and FP8 is lossless. ([Kurtic et al.](https://arxiv.org/abs/2411.02355)) Apple's 2-bit only works with QAT plus LoRA recovery. |
| Training Apple system-model adapters, or targeting Phi Silica | Adapters are discontinued for OS 27. Phi Silica is Windows-only and scheduled for removal in Jan 2027. |
| Raw parameter count as the metric | Gemma 3n has 5B/8B raw parameters but keeps only about 2B/4B in accelerator memory. Resident memory, prefill tok/s and joules per task are what count. |
| Grounded-summarization hallucination boards as a proxy for agent reliability | 4-14B models already score about 4-6% on Vectara HHEM (numbers need hand re-checking). Agent failures come from compounding errors and closed-book guessing instead. |

---

## 4. Key papers and resources

### Must-read
- **BFCL V4 leaderboard plus raw CSV**: https://gorilla.cs.berkeley.edu/leaderboard.html, https://gorilla.cs.berkeley.edu/data_overall.csv. Independent, with separate columns for single-call, multi-turn, web, memory, irrelevance and latency. The best map of where small models break.
- **MAKER (million-step zero-error)**: https://arxiv.org/abs/2511.09030. A recipe for reliability from cheap models: decomposition, voting, red-flags, plus a cost model.
- **tau-bench / tau2-bench**: https://arxiv.org/abs/2406.12045, https://arxiv.org/abs/2506.07982. The pass^k metric and dual control.
- **Apple Intelligence Foundation Models 2025 report**: https://arxiv.org/html/2507.13575 (abs: https://arxiv.org/abs/2507.13575). The most detailed disclosure of a vendor's sweet-spot model.
- **TinyAgent**: https://arxiv.org/abs/2409.00608 and blog https://bair.berkeley.edu/blog/2024/05/29/tiny-agent/. The closest published match to "routine tasks on the owner's Mac".
- **LLMRouterBench**: https://arxiv.org/abs/2601.07206, plus kNN routing: https://arxiv.org/abs/2505.12601. Justification for keeping routing simple.
- **Per-call-site SLM evaluation (home automation)**: https://arxiv.org/abs/2610.09021. A methodology template for local computer use, including the safety failure axis.

### Useful
- APIGen-MT / xLAM-2: https://apigen-mt.github.io/, https://arxiv.org/abs/2504.03601. Size-scaling curve and synthetic multi-turn data recipe.
- JSONSchemaBench: https://arxiv.org/abs/2501.10868. Use it to choose a grammar engine. Also the dottxt rebuttal: https://blog.dottxt.ai/say-what-you-mean.html
- IFBench (IF-RLVR recipe): https://arxiv.org/abs/2507.02833. Training against a verifier-as-reward.
- Last Step Matters: https://arxiv.org/abs/2608.29685. When to escalate.
- Fast Models, Slow Evidence: https://arxiv.org/abs/2610.02267. A model of honest router evaluation.
- RSI-Router (DeepSeek Flash plus Qwen3.5-9B): https://arxiv.org/abs/2609.34712. Nearly the AWOS model pair. Self-reported.
- LLMs Encode Their Failures (activation probes): https://arxiv.org/abs/2602.09924
- Conformal Cascade: https://arxiv.org/abs/2607.25018 and Confident or Seek Stronger: https://arxiv.org/abs/2502.04428
- Fara-7B: https://www.microsoft.com/en-us/research/blog/fara-7b-an-efficient-agentic-model-for-computer-use/; GUI-Owl: https://arxiv.org/abs/2508.15144; macOSWorld: https://arxiv.org/abs/2506.04135; OSWorld-Verified: https://xlang.ai/blog/osworld-verified
- Adaptive VLM Routing for computer use: https://arxiv.org/abs/2603.12823
- Mu / Windows Settings agent: https://blogs.windows.com/windowsexperience/2025/06/23/introducing-mu-language-model-and-how-it-enabled-the-agent-in-windows-settings/
- apple/python-apple-fm-sdk: https://github.com/apple/python-apple-fm-sdk. Gives Python access to the macOS system model today.
- Apple Foundation Models updates (OS 27 LanguageModel protocol, PCC): https://developer.apple.com/documentation/updates/foundationmodels and TN3193: https://developer.apple.com/documentation/technotes/tn3193-managing-the-on-device-foundation-model-s-context-window
- Defeating Nondeterminism: https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/; LayerCast: https://arxiv.org/abs/2506.09501
- Lost in Multi-Turn: https://arxiv.org/abs/2505.06120; Long-horizon execution: https://arxiv.org/abs/2509.09677; Context Rot: https://www.trychroma.com/research/context-rot; IFScale: https://arxiv.org/abs/2507.11538
- Quantization trade-offs: https://arxiv.org/abs/2411.02355
- AppWorld: https://arxiv.org/abs/2407.18901 and LOOP: https://arxiv.org/abs/2502.01600; WorkBench: https://arxiv.org/abs/2405.00823; ToolSandbox: https://arxiv.org/abs/2408.04682; When2Call: https://arxiv.org/abs/2504.18851

### Reference
- Gemma 3n: https://developers.googleblog.com/en/introducing-gemma-3n/, https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/. Gemini report: https://arxiv.org/abs/2312.11805. Gemini Nano: https://developer.android.com/ai/gemini-nano, https://android-developers.googleblog.com/2025/08/the-latest-gemini-nano-with-on-device-ml-kit-genai-apis.html
- Apple 2024/2025 posts: https://machinelearning.apple.com/research/introducing-apple-foundation-models, https://machinelearning.apple.com/research/apple-foundation-models-2025-updates
- Phi Silica / Aion: https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica, https://blogs.windows.com/windowsexperience/2024/12/06/phi-silica-small-but-mighty-on-device-slm/, https://blogs.windows.com/msedgedev/2026/06/02/expanding-on-device-ai-in-microsoft-edge-new-models-and-apis-for-the-web/
- Chrome Prompt API: https://developer.chrome.com/docs/ai/prompt-api
- Qwen3.5 model cards (self-reported): https://huggingface.co/Qwen/Qwen3.5-4B, https://huggingface.co/Qwen/Qwen3.5-9B
- Routing classics: Hybrid LLM https://arxiv.org/abs/2404.14618, RouteLLM https://arxiv.org/abs/2406.18665, UniRoute https://arxiv.org/abs/2502.08773. Step-level: AgentRouter https://arxiv.org/abs/2609.22951, TwinRouterBench https://arxiv.org/abs/2605.18859, Planner-as-Router https://arxiv.org/abs/2609.32917, HM-ROUTER https://arxiv.org/abs/2609.32213, SALE https://arxiv.org/abs/2602.02751, CodeRescue (withdrawn) https://arxiv.org/abs/2607.19338
- MCP-Universe https://arxiv.org/abs/2508.14704; TheAgentCompany https://arxiv.org/abs/2412.14161
- Framing only: NVIDIA SLM position paper https://arxiv.org/abs/2506.02153; Densing Law https://arxiv.org/abs/2412.04315; usage mix NBER w34255 https://www.nber.org/papers/w34255
- Other: FormatSpread https://arxiv.org/abs/2310.11324; Vectara HHEM https://github.com/vectara/hallucination-leaderboard

---

## 5. Implications for AWOS

Mapped onto Gatekeeper: **verified-routine replay → local attempt → verification gate → bounded repair → cloud escalation.**

### Adopt now
1. **Model tiers keyed by step type.**
   - *Tier 0:* the macOS system model through `python-apple-fm-sdk`. It is free, OS-managed and needs no extra RAM. Use it for sub-4K extraction, summarization, classification and commit messages.
   - *Tier 1:* the strongest open 4-9B model or a 30B-A3B MoE under MLX, at Q4/W4A16 with thinking off. Use it for closed-tool multi-turn steps.
   - *Tier 2:* cloud (Flash-class or Haiku-class and up) for open-world search, knowledge lookups, native desktop GUI and anything that fails the gate.

   AWOS controls its own host, so tier 1 can sit above the vendor ~3B sweet spot.
2. **Constrain every local call.** Grammar-constrain the action wrapper and tool schema, and keep free code and reasoning bodies unconstrained. This extends T4's SEARCH grammar. Treat an unconstrained small-model call as a defect.
3. **Red-flag before verifying.** If the output breaks format or runs too long, resample at temperature above 0 instead of repairing. A few cheap samples plus a mechanical test or vote come first. Cloud escalation comes after.
4. **Repair from external signals only, with a fresh prompt.** Each repair turn gets a consolidated, fully specified prompt (task, failing output, minimal context) and drops the transcript of failed attempts.
5. **Use pass^k as the promotion rule.** A routine or bucket gets "local-OK" status only after pass^k (for example k=5 on fresh state) is measured, not after one success. Use paired statistics and several seeds, because noise between runs alone is several points.
6. **Hard-gate irreversible actions.** Send, pay, delete or actuate requires an argument check and a state probe (the WorkBench-style outcome-state check). "Ask the owner" and "escalate" are explicit actions.

### Test next (pre-register, paired A/B)
- **Tier-0 A/B:** Apple's system model against Qwen3.5-4B and Gemma-3n-E4B on AWOS sub-steps, measuring quality, latency, joules and memory contention on the 16 GB M5.
- **Routing table key:** (model, harness config, call site or bucket), using kNN on verified outcomes against a static table. Find the number of labels per bucket at which routing beats always-try-local.
- **Pre-generation activation probe** on the local MLX model as a cheap "skip local" signal. Target AUROC above 0.75 on hidden-test pass.
- **Conformal risk control** on the gate's false-accept rate per bucket, to turn admission into a bound.
- **A separate repair-vs-escalate decision** learned from failure features (empty patch, syntax error, test-failure type).
- **Per-owner LoRA or IF-RLVR** on AWOS-verified trajectories, using the gate as the reward. Start logging training-ready trajectories with pass/fail labels now. Pin the fine-tune to a base model AWOS controls.
- **Local computer use:** Fara-7B-class browser worker with critical-point pausing for web forms. Accessibility-tree action space against pixels on macOS. A Mu-style ~300M distilled selector for high-frequency verified routines, with a lexical fallback.

### Watch
- Independent re-tests of Qwen3.5-4B/9B and Aion Instruct (open release announced).
- Apple OS 27 `PrivateCloudComputeLanguageModel`: context size, quota, and whether a CLI process can use it.
- RSI-Router and AgentRouter replications on real repo issues.

### Operational requirements
- **Quarterly re-qualification** of new small models against the routine suite and the gate (Densing Law).
- **A canary suite on model-version change.** OS updates silently swap the system model, so re-verify routines whenever the model ID changes. This is what "memory only from strong evidence" demands in practice.
- **Keep learned behavior model-independent.** Store routines, prompts and compiled tools so they survive a model swap. Apple's abandoned adapters are the warning.

### Ignore
Learned or commercial routers, mid-trajectory escalation for coding, self-critique loops, vendor model-card scores taken as fact, sub-1B general workers, sub-4-bit quantization without QAT, and MMLU or IFEval for model selection.

### Strategic note
Apple, Google and Microsoft now ship local-then-cloud fallback as the default developer path. Routing is becoming a commodity. Their escalation triggers are capability, timeout and context overflow, never verification evidence. AWOS's advantage lies in what vendors leave out: **escalation decided by verification evidence, replay of verified routines, and memory built only from strong evidence.**

---

## 6. Open questions worth exploring next

1. What is the measured pass^5 of Qwen3.5-4B/9B and a 30B-A3B model (thinking off, constrained decoding) on AWOS's own email, file, calendar, form and code-edit routines?
2. How many owner trajectories does a LoRA need before it beats a Haiku-class cloud model on that owner's fixed tool set? Does general capability erode?
3. Do MAKER-style voting and red-flag thresholds carry over to SEARCH/REPLACE edits and GUI actions? How precise is "malformed means wrong" for local models?
4. On macOS, does an accessibility-tree action space close the under-5% macOSWorld gap for 7B models?
5. How deterministic is batch-1 MLX or llama.cpp across restarts and quant levels? Is it deterministic enough for exact-replay verification?
6. Do state probes catch wrong-target actuation (the 87% Gemma E2B failure)? Or does preference training (When2Call) need to come first?
7. What per-step latency will owners accept for interactive versus background work? That threshold decides between 3B, 7B dense and 30B-A3B.
8. How much of the routing saving does cache loss on model switches eat, at DeepSeek's roughly 50x cache hit/miss price ratio?
9. Is there any user study of perceived quality of local small-model assistants (as opposed to benchmarks)? None was found here, though search was unavailable.

---

## 7. Sources

- https://gorilla.cs.berkeley.edu/leaderboard.html
- https://gorilla.cs.berkeley.edu/data_overall.csv
- https://apigen-mt.github.io/
- https://arxiv.org/abs/2504.03601
- https://arxiv.org/abs/2406.12045
- https://arxiv.org/abs/2405.00823
- https://arxiv.org/abs/2506.07982
- https://arxiv.org/abs/2409.00608
- https://bair.berkeley.edu/blog/2024/05/29/tiny-agent/
- https://arxiv.org/html/2507.13575
- https://arxiv.org/abs/2507.13575
- https://machinelearning.apple.com/research/apple-foundation-models-2025-updates
- https://machinelearning.apple.com/research/introducing-apple-foundation-models
- https://developer.apple.com/documentation/updates/foundationmodels
- https://developer.apple.com/documentation/technotes/tn3193-managing-the-on-device-foundation-model-s-context-window
- https://developer.apple.com/apple-intelligence/foundation-models-adapter/
- https://github.com/apple/python-apple-fm-sdk
- https://www.microsoft.com/en-us/research/blog/fara-7b-an-efficient-agentic-model-for-computer-use/
- https://arxiv.org/abs/2508.15144
- https://arxiv.org/abs/2506.04135
- https://xlang.ai/blog/osworld-verified
- https://huggingface.co/Qwen/Qwen3.5-4B
- https://huggingface.co/Qwen/Qwen3.5-9B
- https://arxiv.org/abs/2408.04682
- https://arxiv.org/abs/2504.18851
- https://arxiv.org/abs/2404.14618
- https://arxiv.org/abs/2406.18665
- https://arxiv.org/abs/2506.02153
- https://www.nber.org/papers/w34255
- https://arxiv.org/abs/2407.18901
- https://arxiv.org/abs/2502.01600
- https://arxiv.org/abs/2508.14704
- https://arxiv.org/abs/2412.14161
- https://arxiv.org/abs/2412.04315
- https://arxiv.org/abs/2601.07206
- https://arxiv.org/abs/2505.12601
- https://arxiv.org/abs/2602.09924
- https://arxiv.org/abs/2610.09021
- https://arxiv.org/abs/2608.29685
- https://arxiv.org/abs/2610.02267
- https://arxiv.org/abs/2609.34712
- https://arxiv.org/abs/2607.19338
- https://arxiv.org/abs/2607.25018
- https://arxiv.org/abs/2502.04428
- https://arxiv.org/abs/2603.12823
- https://arxiv.org/abs/2608.21747
- https://arxiv.org/abs/2609.32917
- https://arxiv.org/abs/2609.32213
- https://arxiv.org/abs/2602.02751
- https://arxiv.org/abs/2502.08773
- https://arxiv.org/abs/2609.22951
- https://arxiv.org/abs/2605.18859
- https://arxiv.org/abs/2511.09030
- https://arxiv.org/html/2511.09030
- https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- https://arxiv.org/abs/2506.09501
- https://arxiv.org/abs/2501.10868
- https://arxiv.org/html/2501.10868
- https://blog.dottxt.ai/say-what-you-mean.html
- https://arxiv.org/abs/2507.02833
- https://arxiv.org/abs/2507.11538
- https://arxiv.org/abs/2505.06120
- https://arxiv.org/abs/2509.09677
- https://arxiv.org/abs/2310.01798
- https://arxiv.org/abs/2310.11324
- https://arxiv.org/abs/2411.02355
- https://github.com/vectara/hallucination-leaderboard
- https://www.trychroma.com/research/context-rot
- https://arxiv.org/abs/2312.11805
- https://developers.googleblog.com/en/introducing-gemma-3n/
- https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/
- https://android-developers.googleblog.com/2025/08/the-latest-gemini-nano-with-on-device-ml-kit-genai-apis.html
- https://developer.android.com/ai/gemini-nano
- https://developer.chrome.com/docs/ai/prompt-api
- https://blogs.windows.com/windowsexperience/2024/12/06/phi-silica-small-but-mighty-on-device-slm/
- https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
- https://blogs.windows.com/windowsexperience/2025/06/23/introducing-mu-language-model-and-how-it-enabled-the-agent-in-windows-settings/
- https://blogs.windows.com/msedgedev/2026/06/02/expanding-on-device-ai-in-microsoft-edge-new-models-and-apis-for-the-web/

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

*Method note: the arXiv API returned HTTP 429 for most queries, so paper discovery went mainly through the Hugging Face papers search, Hacker News (Algolia) and one GitHub query. Abstract pages were fetched to confirm. Coverage of arXiv is therefore thinner than planned. Model-card and vendor claims below remain self-reported unless stated.*

### New since the chart (dated, with URLs; most important first)

1. **Apple's third-generation foundation models (WWDC, 2026-06-08).** Apple describes AFM 3 Core (3B, on-device, quantization-aware training) and AFM 3 Core Advanced (20B total, "activating just 1 to 4 billion parameters at a time"). The tech note reports preference wins only (45.6% vs 23.3% against the 2025 baseline); no agentic or tool-call numbers. Why it matters: Tier 0 in section 5 is no longer a fixed "~3B" target, and the Core Advanced MoE may be reachable through the same API on OS 27. The A/B must name the exact model ID. https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models
2. **Apple's `LanguageModel` protocol now takes server-side and third-party models (OS 27 betas).** Anthropic's docs describe a Swift package that plugs Claude into `LanguageModelSession`. Apps choose per session between the on-device model and Claude; the recommended pattern is to catch `.rateLimited` and fall back to `SystemLanguageModel`. Why it matters: it confirms the chart's strategic note (vendor routing is capability, timeout and overflow based, not verification based). It also gives AWOS a uniform Tier 0/Tier 2 session API on macOS 27. https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/apple-foundation-models
3. **Manifest deprecated its production LLM router (HN, 2026-07-31).** After four months and about 7,000 cloud users, Manifest reported that task difficulty cannot be read from the prompt alone, that cache reads are "between 75% and 90% cheaper", and that model switching hurts consistency. Vendor blog, anecdotal, no controlled data. Why it matters: it independently supports "simple routing wins" and the cache-loss open question 8. Escalate at attempt boundaries only. https://manifest.build/blog/why-we-deprecated-our-llm-router/
4. **SWE-Router (2026-06-30) and Agent-as-a-Router / ACRouter (2026-06-22).** SWE-Router argues that task-description-only routing has a Bayes-error floor and that reading the partial cheap trajectory before escalating never hurts, in theory. ACRouter reports a 15.3% relative gain from adding per-dimension performance statistics to a vanilla LLM router, on a 10,000-instance coding benchmark across 8 frontier models. Both are unreplicated. Why it matters: they partly contradict the chart's "ignore mid-trajectory escalation for coding" and support its kNN-over-verified-outcomes default (ACRouter's gain comes from outcome statistics). They are worth adding to the routing A/B. https://arxiv.org/abs/2607.00053 · https://arxiv.org/abs/2606.22902
5. **Gemma 4 (2026-04-02) and Gemma 4 12B (2026-06-03).** Google lists E2B, E4B, 12B, 26B and 31B variants with native function calling. The 31B reports 86.4% on tau2-bench retail (self-reported). Why it matters: the open 4-9B Tier 1 candidate list needs Gemma 4 E4B and 12B. The chart's own 87.2% E2B actuation failure (item 8 below) shows the danger of trusting the headline. https://deepmind.google/models/gemma/gemma-4/ · HN thread https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b/
6. **Qwen3.6 family (HN, April 2026): 35B-A3B (04-16), 27B dense (04-22).** Vendor blogs claim agentic-coding strength. A hands-on report ran the 35B-A3B at Q4_K_S (20.9 GB GGUF) on an M5 MacBook Pro via LM Studio, and its author warns the demo does not show general usefulness. Why it matters: these are the realistic "30B-A3B MoE under MLX" Tier 1 candidates for a 16 GB-class Mac, though a 21 GB quant exceeds 16 GB RAM. Memory fit must be tested. https://qwen.ai/blog?id=qwen3.6-35b-a3b · https://simonwillison.net/2026/Apr/16/qwen-beats-opus/
7. **Microsoft Aion Instruct timeline firmed up (docs updated 2026-10-02).** "Early October 2026" sideloadable package for testing and LoRA re-training; November 2026 Insider rollout; January 2027 retail rollout and Phi Silica removed. LAF tokens no longer needed. Why it matters: it confirms the chart's dates. The LoRA re-training requirement is a second vendor data point for "keep learned behavior model-independent". https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
8. **Per-call-site SLM study published 2026-10-06 (arXiv 2610.09021).** The abstract states that Gemma4 E2B "actuates on 87.2% of requests for devices the site does not own" and evaluates nine models. Why it matters: this is the chart's key safety number and it checks out against the primary source. https://arxiv.org/abs/2610.09021
9. **Cactus Needle 3 (HN, 2026-09-18, 236 points).** Vendor claims 8-29 MB multi-depth models with 2-bit quantization that, after fine-tuning on their platform, pass DeepSeek V4 Flash on tool-calling and extraction datasets (including BFCL v4 subsets). Self-reported, vendor-chosen evaluation. Why it matters: it is a second data point for the Mu-style "tiny specialist for a closed action space" path in section 2 item 10, with the usual fine-tune-on-your-own-distribution caveat. https://cactuscompute.com/needle
10. **"Rethinking Scale" (2026-04-21).** For sub-10B models, single-agent plus tools gives the best performance/cost balance; multi-agent setups add overhead with limited gains. Why it matters: it supports staying single-worker at Stage 1. https://arxiv.org/abs/2604.19299
11. **Single-prompt audit (2026-05-03).** Ten instruction-tuned models; parameter count correlated only weakly (-0.24 to 0.47) with prompt-perturbation spread, and verbal confidence overstated accuracy. Why it matters: it adds support to pass^k over pass@1, and a caution on verbal-confidence escalation signals. https://arxiv.org/abs/2605.02038
12. **python-apple-fm-sdk is active:** v0.2.0 (2026-06-08, image attachments) and v0.2.1 (2026-06-29), 1,326 stars, last push 2026-10-07. Why it matters: Tier 0 plumbing is maintained; pin the version. https://github.com/apple/python-apple-fm-sdk

### Corrections

- Chart: "Aion Instruct (open release announced)". The Microsoft docs describe a sideloadable package and rollout through Windows, and say nothing about open weights. Treat "open" as unverified. https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
- Chart: "Three OS vendors converged on about 3B ... always-resident model". Still true for Apple's AFM 3 Core, but Apple now also ships a 20B-total MoE with 1-4B active parameters on-device, so "about 3B" understates the range. https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models
- Chart: "Apple in 26.4 and again in 27" swapped system models. I could not confirm the 26.4 step from a primary source; the AFM 3 announcement (June 2026) is the confirmed change.
- Otherwise none found among the numbers I re-checked.

### Confirmed claims

- BFCL V4 CSV (https://gorilla.cs.berkeley.edu/data_overall.csv, fetched today) matches the chart: multi-turn Qwen3-8B FC 41.75, Qwen3-1.7B FC 11.00, Qwen3-0.6B FC 3.62, Gemma-3-4b (Prompt) 0.38, xLAM-2-8b 70.00, Opus 4.5 FC 68.38, Haiku 4.5 FC 53.62, Hammer2.1-1.5b 15.62.
- Irrelevance: Llama-3.1-8B 42.70, xLAM-2-8b 63.28 match. Latency means: Qwen3-32B FC 169.87 s, xLAM-2-8b 22.65 s, Haiku 4.5 FC 1.68 s match.
- Apple adapters: the toolkit page says 26.0.0 "is the last release ... not compatible with ... 27 and later". https://developer.apple.com/apple-intelligence/foundation-models-adapter/
- Phi Silica removal in January 2027 and replacement by Aion Instruct: confirmed in the Microsoft docs above.
- The 87.2% Gemma4 E2B actuation figure: confirmed in the abstract of 2610.09021.

### Still unverified

- The CSV I fetched contains no Qwen3.5, Qwen3.6 or Gemma 4 rows (it does include Qwen3-4B/14B/30B-A3B-2507), so the BFCL picture is stale for the newest small models. Any claim about them rests on vendor cards.
- Qwen3.5-4B/9B TAU2 and OSWorld numbers, Fara-7B and macOSWorld figures, MAKER, LLMRouterBench and the router "self-audit" 23.9% to 4.3% were not re-fetched this pass.
- The Qwen3.6 blog page returned no usable content through the fetcher, so its benchmark figures are not checked.
- No independent measurement yet of AFM 3 Core Advanced on tool use, or of Aion Instruct quality.
- No user study of perceived quality of local small-model assistants was found (open question 9 stays open).
