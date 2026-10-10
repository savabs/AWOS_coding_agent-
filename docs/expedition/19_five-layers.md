# The 5 layers of AI (stack models)

*Expedition chart 19. Compiled 2026-10-10 from four scout reports. Coverage caveat: the session's shared WebSearch budget (200 calls across all agents) ran out early, so most evidence comes from fetching known primary URLs directly. 2026 commentary is thinner than planned. Claims the scouts could not fetch are marked **unverified**.*

---

## 1. Summary

- **"The 5 layers of AI" usually means Jensen Huang's "five-layer cake"**: energy, chips, infrastructure (cloud and data centers), models, applications. It was first well documented at CSIS on Dec 3, 2025, as a geopolitical argument about who can export a full AI stack. The version most people quote is the WEF Davos talk with Larry Fink on Jan 21, 2026, where he said the application layer "ultimately, is where economic benefit will happen."
- **The cake is a supplier's framing, not an analysis of where value goes.** Today the profit sits at layer 2. NVIDIA posted a 72.4% GAAP gross margin on $41.1B of data-center revenue in a single quarter (Q2 FY2026). All enterprise gen-AI spend in 2025 came to $37B (Menlo).
- **The model layer is commoditizing fast at fixed capability.** The price to reach a given capability falls 9x to 900x per year depending on the benchmark (Epoch), roughly 10x per year by a16z's measure. Open models that fit a consumer GPU trail the frontier by about 6-12 months.
- **Running at the frontier is getting more expensive per task, not cheaper.** Frontier operating cost rises 3-18x per year (MIT, Gundlach et al.). Frontier demand barely responds to price: a 10% price cut brings only 0.5-0.7% more usage (OpenRouter). Apps that resell frontier tokens get squeezed to about 25% gross margin (Bessemer). Cursor's July 2025 move to usage-based pricing is the concrete example.
- **Every VC stack map (a16z, Sequoia, Bessemer, Letta) leaves out the same layer**: a runtime that owns verification, evidence-gated memory, routing and an always-on host on hardware the owner controls. That gap is where AWOS fits.
- **Platform vendors are building the plumbing underneath that runtime.** Apple runs on-device models first and escalates to Private Cloud Compute (PCC). Windows runs MCP behind a trusted proxy and registry. Foundry Local picks NPU, GPU or CPU model variants and falls back to the cloud. NVIDIA sells DGX Spark as "a complete platform for local autonomous agents."
- **Each layer of the agent stack now has a converging open standard**, most of them under the Agentic AI Foundation: MCP 2026-07-28 for tools (stateless), A2A v1.0 between agents, AGENTS.md for repo context, Agent Skills for procedural memory, AG-UI for the user interface, and OTel GenAI spans for tracing (status: development).
- **Local is now good enough for most single-turn work, but agentic work has not been measured.** In the Intelligence-per-Watt study, local models answered 88.7% of real single-turn queries, and the share local models can serve rose from 23% to 71% between 2023 and 2025. No comparable number exists for multi-step agentic tasks with tests.

---

## 2. What matters most (ranked)

1. **Two cost curves pull apart: fixed-capability cost falls while frontier cost rises.** This is the economic basis for the Gatekeeper design. Routine work rides the falling curve. Only hard cases pay the rising one. Users who send everything to the frontier pay more each year.
   Evidence: 9-900x/yr price decline at fixed capability (https://epoch.ai/data-insights/llm-inference-price-trends). 5-10x/yr decline per benchmark score versus a 3-18x/yr rise in frontier operating cost (https://arxiv.org/abs/2511.23455).

2. **Value goes to whoever uses fewer frontier tokens per unit of verified work, not to cheaper tokens.** Frontier demand is price-inelastic (10% cut gives about 0.6% more usage). Programming went from about 11% to over 50% of tokens. Agentic requests use up to 10x more tokens each.
   Evidence: https://openrouter.ai/state-of-ai. AI apps run about 25% gross margin versus 75-80% for SaaS (https://www.bvp.com/atlas/the-state-of-ai-2025). Cursor moved Pro to "$20 of frontier usage at API pricing" (https://cursor.com/blog/june-2025-pricing). The $200 "unlimited" plan rollback and the roughly $72/day 24-hour agent are anecdotes (https://ethanding.substack.com/p/ai-subscriptions-get-short-squeezed).

3. **The local tier gets better on its own.** Open models that fit a consumer GPU trail the frontier by 6.3 months (AA Index), 7.3 (MMLU-Pro), 7.4 (GPQA) and 12.4 (LM Arena), on hardware under $2,500. IPW improved 5.3x from 2023 to 2025.
   Evidence: https://epoch.ai/data-insights/consumer-gpu-model-gap, https://arxiv.org/abs/2511.07885. Caveat: local accelerators such as the M4 Max were at least 1.4x worse on IPW than cloud hardware running the same models. Edge watts are not automatically cheaper than data-center watts.

4. **The runtime layer is the missing layer, and the defensible one.** No published stack map has an execution and verification layer. Bessemer names "memory and context" and private evals as the new moats. Letta says hosting agents as stateful services is "much trickier than deploying LLMs as a service."
   Evidence: https://www.sequoiacap.com/article/generative-ais-act-o1/, https://www.bvp.com/atlas/the-state-of-ai-2025, https://www.letta.com/blog/ai-agents-stack, https://a16z.com/emerging-architectures-for-llm-applications/.

5. **Routing is becoming a learned layer.** ToolOrchestra, an RL-trained 8B orchestrator, scored 37.1% on HLE against GPT-5's 35.1% while being 2.5x more efficient, and matched GPT-5 at about 30% of the cost on tau2-Bench and FRAMES (self-reported). Minions, where the cloud decomposes a task and the local model executes the pieces, cut remote cost 5.7x while keeping 97.9% of frontier quality. Its naive variant cut cost 30.4x but kept only 87%.
   Evidence: https://arxiv.org/abs/2511.21689, https://arxiv.org/abs/2502.15964.

6. **Platform vendors are shipping the plumbing.** Apple ships a ~3B on-device model at 2 bits per weight with guided generation and tool calling, free to developers. PCC provides stateless, verifiable escalation. Windows MCP has a proxy, registry, declared privileges and per-pairing consent.
   Evidence: https://machinelearning.apple.com/research/apple-foundation-models-2025-updates, https://security.apple.com/blog/private-cloud-compute/, https://blogs.windows.com/windowsexperience/2025/05/19/securing-the-model-context-protocol-building-a-safer-agentic-future-on-windows/, https://learn.microsoft.com/en-us/windows/ai/foundry-local/get-started, https://www.nvidia.com/en-us/products/workstations/dgx-spark/ (128 GB at 273 GB/s, 240 W supply, vendor claims).

7. **The tool and interface standards have settled, so build on them.** MCP 2026-07-28 removed sessions and the initialize handshake, added Multi Round-Trip Requests and a Tasks extension that is polled, and deprecated Sampling, Roots and Logging. Servers SHOULD return tools/list in a deterministic order "to improve LLM prompt cache hit rates." In one Anthropic example, code execution over MCP cut tokens from 150k to 2k (98.7%, a single workflow, self-reported).
   Evidence: https://modelcontextprotocol.io/specification/2026-07-28/changelog, https://modelcontextprotocol.io/development/roadmap, https://www.anthropic.com/engineering/code-execution-with-mcp, https://agentskills.io, https://agents.md.

8. **Multiple agents pay off for breadth, not for writing.** Anthropic's multi-agent research system beat single-agent Opus by 90.2% on an internal eval, using about 15x the tokens. Token usage alone explained 80% of the variance on BrowseComp. MAST (1,600+ traces) found 14 failure modes, one group of which is missing task verification.
   Evidence: https://www.anthropic.com/engineering/multi-agent-research-system, https://arxiv.org/abs/2503.13657.

9. **Inside the stack, the metric is tokens per dollar per watt, and software accounts for much of the gain.** Microsoft gets "90% more tokens for the same GPU" from software alone. AWOS's version should count verified work, not tokens.
   Evidence: https://www.microsoft.com/en-us/investor/events/fy-2025/earnings-fy-2025-q4, https://blogs.nvidia.com/blog/ai-factory/, https://inferencex.semianalysis.com/.

---

## 3. What does NOT matter / hype / dead ends

- **Reading the five-layer cake as a neutral model.** It was pitched to policymakers (CSIS) and investors (Davos). "Every layer must be built" drives chip demand. It says nothing about how much value each layer captures, and current margins contradict "value accrues on top."
- **Arguing over which 5-layer diagram is correct**, or over layer names. Huang's cake, Sequoia's four tiers, Bessemer's roadmaps and NVIDIA's own five-component "AI factory" all differ. They are capital-allocation maps, so use them for positioning, not architecture.
- **Headline cost drops (280x, 1000x) as a planning input.** They are measured at fixed old capability, often on contaminated MMLU. Measure $ per verified task instead.
- **Token throughput as the success metric.** For an agent runtime, tokens are a cost. Optimizing them rewards verbosity.
- **Owning weights or pre-training a base model as a moat.** With 10x+/yr commoditization and a 6-12 month open-model lag, a base model is obsolete before it pays back. Fine-tuning or distilling on the owner's verified traces is a different question and stays open (see section 6).
- **Reselling frontier tokens or competing on per-token price.** Demand is inelastic and the margin is about 25%.
- **Theoretical inference margins** such as DeepSeek's self-reported 545%. They show pricing headroom, not realized economics (https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).
- **2023 stack components as required layers**: vector-DB-first designs, LangChain orchestration, GPTCache.
- **Rebuilding OS plumbing** (inference server, tool protocol, consent UI) as the differentiator. Incumbents ship it with platform privileges.
- **Deprecated or removed MCP features**: Sampling, Roots, Logging, HTTP+SSE, protocol sessions. Also pre-1.0 A2A samples.
- **Mem0-style memory benchmarks as evidence for agent memory.** They are self-reported, measured on conversational QA (LOCOMO), and do not measure task success.
- **More parallel agents as a goal in itself.** Without a verifier to choose among candidates, more agents mostly produce more unverified output.

---

## 4. Key papers and resources

### Must-read
- **Epoch AI, LLM inference price trends**: https://epoch.ai/data-insights/llm-inference-price-trends. Price decline per capability milestone. Use it to project when cloud-only tasks move to local.
- **Epoch AI, frontier on consumer hardware**: https://epoch.ai/data-insights/consumer-gpu-model-gap. The 6-12 month lag, a planning constant for the local tier.
- **The Price of Progress (Gundlach et al.)**: https://arxiv.org/abs/2511.23455. Separates the falling fixed-capability price from the rising frontier operating cost.
- **Intelligence per Watt (Saad-Falcon et al.)**: https://arxiv.org/abs/2511.07885. The published metric closest to the AWOS objective. Its method can be extended to agentic tasks.
- **Minions**: https://arxiv.org/abs/2502.15964. Cloud plans, local executes. A candidate alternative to escalating on failure.
- **ToolOrchestra**: https://arxiv.org/abs/2511.21689. A template for a learned router.
- **OpenRouter State of AI**: https://openrouter.ai/state-of-ai. Price inelasticity and share of tokens by workload (vendor self-reported).
- **NVIDIA Davos blog (the cake)**: https://blogs.nvidia.com/blog/davos-wef-blackrock-ceo-larry-fink-jensen-huang/ and **CSIS**: https://www.csis.org/analysis/nvidias-jensen-huang-securing-american-leadership-ai. The primary sources.
- **MCP 2026-07-28 changelog**: https://modelcontextprotocol.io/specification/2026-07-28/changelog.

### Useful
- Menlo 2025 enterprise report: https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/. Apps $19B vs infra $18B. Coding $4.0B. Anthropic 40% enterprise share. Open source down to 11%.
- Menlo mid-year update: https://menlovc.com/perspective/2025-mid-year-llm-market-update/. Only 11% of enterprises switch vendors.
- Bessemer State of AI 2025: https://www.bvp.com/atlas/the-state-of-ai-2025
- Sequoia, AI's $600B Question: https://www.sequoiacap.com/article/ais-600b-question/. Sequoia, Act o1: https://www.sequoiacap.com/article/generative-ais-act-o1/
- NVIDIA Q2 FY2026 results: https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2026
- AIOS: https://arxiv.org/abs/2403.16971. Up to 2.1x faster agent serving from kernel-level scheduling. MemGPT: https://arxiv.org/abs/2310.08560
- Small Language Models are the Future of Agentic AI: https://arxiv.org/abs/2506.02153. A position paper with an LLM-to-SLM conversion procedure.
- Code execution with MCP: https://www.anthropic.com/engineering/code-execution-with-mcp
- Multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system. MAST: https://arxiv.org/abs/2503.13657
- sandbox-runtime: https://github.com/anthropic-experimental/sandbox-runtime. Container-free OS sandboxing (Seatbelt on macOS, bubblewrap on Linux).
- Agent Skills: https://agentskills.io. AGENTS.md: https://agents.md
- Apple FM 2025: https://machinelearning.apple.com/research/apple-foundation-models-2025-updates. PCC: https://security.apple.com/blog/private-cloud-compute/
- Context engineering: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OSWorld: http://osworld-v1.xlang.ai/. At launch, the human baseline was 72% and the best model scored 12.24%. Current leaderboard not retrieved.

### Reference
- a16z LLMflation: https://a16z.com/llmflation-llm-inference-cost/. a16z 2023 architectures: https://a16z.com/emerging-architectures-for-llm-applications/. a16z MCP deep dive: https://a16z.com/a-deep-dive-into-mcp-and-the-future-of-ai-tooling/
- Letta agents stack: https://www.letta.com/blog/ai-agents-stack
- Stanford AI Index 2025: https://hai.stanford.edu/ai-index/2025-ai-index-report
- Microsoft FY25 Q4: https://www.microsoft.com/en-us/investor/events/fy-2025/earnings-fy-2025-q4. NVIDIA AI factory: https://blogs.nvidia.com/blog/ai-factory/. SemiAnalysis InferenceX: https://inferencex.semianalysis.com/
- DGX Spark: https://www.nvidia.com/en-us/products/workstations/dgx-spark/
- Windows MCP security: https://blogs.windows.com/windowsexperience/2025/05/19/securing-the-model-context-protocol-building-a-safer-agentic-future-on-windows/. Foundry Local: https://learn.microsoft.com/en-us/windows/ai/foundry-local/get-started
- MCP roadmap: https://modelcontextprotocol.io/development/roadmap. A2A v1.0: https://a2a-protocol.org/latest/whats-new-v1/. AG-UI: https://docs.ag-ui.com/introduction. OTel GenAI agent spans: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- Mem0: https://arxiv.org/abs/2504.19413
- DeepSeek inference economics: https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md
- Ethan Ding: https://ethanding.substack.com/p/ai-subscriptions-get-short-squeezed. Cursor pricing: https://cursor.com/blog/june-2025-pricing
- Press recaps of the cake talk: https://gigazine.net/gsc_news/en/20260123-jensen-huang-nvidia-ai-5-layer-cake, https://eu.36kr.com/en/p/3649372920029576
- Latent Space, Rise of the AI Engineer: https://www.latent.space/p/ai-engineer. The Software 3.0 framing. Karpathy's "personal computing for LLMs hasn't happened yet" is **unverified**.
- Windsurf access cut (from a scout's memory, link not fetched): https://techcrunch.com/2025/06/03/windsurf-says-anthropic-is-limiting-its-first-party-access-to-claude-ai-models/
- Claude Code workflows and env vars: https://code.claude.com/docs/en/workflows, https://code.claude.com/docs/en/env-vars

---

## 5. Implications for AWOS

**Positioning.** In Huang's cake, AWOS is not an app and not a model. It is the missing runtime layer that sits on the owner's own slice of layers 1-3 (a box, its watts, its chip). Its job is to turn commodity layer-4 intelligence into verified work. The defensible assets are the ones that compound for each owner: the verification gate, the library of verified routines, memory built from strong evidence, and a router trained on the owner's own outcomes.

**Adopt now (mapped to Gatekeeper):**
- **Headline KPI: cloud $ and Wh per verified task, tracked over time**, with amortized hardware cost included so "local is free" does not become an accounting illusion. Report it IPW-style (accuracy per joule) on AWOS's own task set. Never count tokens per watt.
- **Verified-routine replay** is the strongest lever. It spends zero tokens and rides no cost curve. Export routines as **Agent Skills (SKILL.md)** for portability and progressive disclosure. Keep a compiled or scripted form where determinism matters.
- **Local attempt stage:** let the small model write short scripts against tool APIs (code execution over MCP) instead of carrying raw tool outputs through its context. This is the cheapest way to fit work into a small model's effective context. Return tools/list in deterministic order and load tools lazily, which compounds with the stable-prefix caching work (T7b).
- **Tool and computer-use surface:** stateless MCP servers with the Tasks extension (polled) for long jobs such as builds and test suites. Do not build on Sampling. Use srt-style OS sandboxing rather than Docker on the always-on host. Feed sandbox-violation logs into the verification gate and sink policy.
- **Cloud escalation:** provider-agnostic, re-priced on every call (OpenRouter, direct DeepSeek off-peak, Anthropic). The top coding model is concentrated in one supplier with pricing power and a history of cutting off access (Windsurf, unverified). Design the local tier so it still works when the top cloud model is unavailable.
- **Observability:** emit OTel GenAI span names in `.awos/spans.jsonl` (pin a version) and add a watts attribute.

**Test (ablations, pre-registered like E1):**
- **Minions ordering versus the current Gatekeeper ordering.** Compare "cloud decomposes, local executes, gate verifies" against "local first, cloud on failure" on the 28/73-issue sets. Measure $, wall-clock and Wh per resolved issue.
- **Learned router.** Train a small router on the `.awos/reward_store.jsonl` gate outcomes, in the style of ToolOrchestra, and compare it with the hand-written escalation rules.
- **Measure the agentic local-servable share** for each local model generation, to check whether it really grows on Epoch's 6-12 month cadence.
- **Parallel candidates with the gate choosing.** Run N local attempts in parallel and let tests pick. This is the setting where multiple agents help (per Anthropic). Keep one writer per artifact (per MAST and Cognition).

**Watch:** OS-native agent layers (Apple FM, Foundry Local, the Windows MCP registry) as substrate AWOS could run on. NVIDIA or box OEMs trying to own the agent runtime (DGX Spark marketing). Stabilization of the OTel GenAI spec. MCP Triggers/Events for the host queue. Enterprise reluctance about Chinese open weights (about 1% of enterprise adoption) as a risk to the local tier's supply.

**Ignore:** the cake as architecture guidance, token-throughput metrics, pre-training a base model, a custom protocol at any layer that already has a standard, and A2A until the box accepts work from outside.

**Note on the user's question (running more than 8 agents in parallel).** One scout checked the Claude Code docs and the local machine. Workflows default to up to 16 concurrent agents, reduced on machines with fewer CPUs. This box has 10 CPUs and `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` is unset, which fits the cap of 8 the user sees. The variable accepts 1-256 (https://code.claude.com/docs/en/workflows). In this run, though, agent count was not the limit. The shared budget of 200 WebSearch calls per session (`CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, https://code.claude.com/docs/en/env-vars) ran out after about one query per scout. Raising concurrency alone would add agents that cannot search. Raise both limits, or give the extra agents work that needs no search (repo analysis, fetching known URLs). The same lesson applies to AWOS: cap local concurrency by unified memory and KV cache, and cap cloud concurrency by rate limit and budget, not by core count.

---

## 6. Open questions worth exploring next

1. What share of AWOS's real agentic coding and computer-use tasks can the local tier verifiably serve? Published numbers (IPW's 71%) cover single-turn chat only.
2. Do edge watts beat data-center watts on verified work per watt once utilization and batching are counted? IPW finds local accelerators 1.4x worse per watt on the same models.
3. When does distilling or fine-tuning on the owner's verified traces beat simply waiting about 6 months for the next open model?
4. Can a router trained on a few thousand AWOS gate outcomes beat the hand-written rules on $ per verified task?
5. Do Agent Skills keep replay deterministic, or should verified routines be compiled tools with a SKILL.md wrapper?
6. Can the library of verified routines withstand OS vendors adding native agent memory? Could routines pooled across owners create a privacy-preserving network effect?
7. What is the right local concurrency cap on the M5 box, given MLX batching versus KV-cache pressure?
8. How much of OSWorld can be solved through accessibility-tree, script or MCP paths rather than pixels? What are the current OSWorld-Verified top scores? (Not retrieved.)
9. **Unverified items to confirm:** any use of the "five-layer cake" phrase before Dec 2025. Karpathy's June 2025 YC claims. IEA data-center electricity figures (the scout got HTTP 403). How margins split across layers in 2026.

---

## 7. Sources

- https://www.csis.org/analysis/nvidias-jensen-huang-securing-american-leadership-ai
- https://blogs.nvidia.com/blog/davos-wef-blackrock-ceo-larry-fink-jensen-huang/
- https://gigazine.net/gsc_news/en/20260123-jensen-huang-nvidia-ai-5-layer-cake
- https://eu.36kr.com/en/p/3649372920029576
- https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2026
- https://blogs.nvidia.com/blog/ai-factory/
- https://www.nvidia.com/en-us/products/workstations/dgx-spark/
- https://www.sequoiacap.com/article/ais-600b-question/
- https://www.sequoiacap.com/article/generative-ais-act-o1/
- https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/
- https://menlovc.com/perspective/2025-mid-year-llm-market-update/
- https://www.bvp.com/atlas/the-state-of-ai-2025
- https://a16z.com/llmflation-llm-inference-cost/
- https://a16z.com/emerging-architectures-for-llm-applications/
- https://a16z.com/a-deep-dive-into-mcp-and-the-future-of-ai-tooling/
- https://epoch.ai/data-insights/llm-inference-price-trends
- https://epoch.ai/data-insights/consumer-gpu-model-gap
- https://hai.stanford.edu/ai-index/2025-ai-index-report
- https://arxiv.org/abs/2511.23455
- https://arxiv.org/abs/2511.07885
- https://arxiv.org/abs/2502.15964
- https://arxiv.org/abs/2511.21689
- https://arxiv.org/abs/2506.02153
- https://arxiv.org/abs/2403.16971
- https://arxiv.org/abs/2310.08560
- https://arxiv.org/abs/2503.13657
- https://arxiv.org/abs/2504.19413
- https://openrouter.ai/state-of-ai
- https://ethanding.substack.com/p/ai-subscriptions-get-short-squeezed
- https://cursor.com/blog/june-2025-pricing
- https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md
- https://www.microsoft.com/en-us/investor/events/fy-2025/earnings-fy-2025-q4
- https://inferencex.semianalysis.com/
- https://www.letta.com/blog/ai-agents-stack
- https://www.latent.space/p/ai-engineer
- https://security.apple.com/blog/private-cloud-compute/
- https://machinelearning.apple.com/research/apple-foundation-models-2025-updates
- https://blogs.windows.com/windowsexperience/2025/05/19/securing-the-model-context-protocol-building-a-safer-agentic-future-on-windows/
- https://learn.microsoft.com/en-us/windows/ai/foundry-local/get-started
- https://techcrunch.com/2025/06/03/windsurf-says-anthropic-is-limiting-its-first-party-access-to-claude-ai-models/
- https://modelcontextprotocol.io/specification/2026-07-28/changelog
- https://modelcontextprotocol.io/development/roadmap
- https://www.anthropic.com/engineering/code-execution-with-mcp
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://agentskills.io
- https://agents.md
- https://a2a-protocol.org/latest/whats-new-v1/
- https://docs.ag-ui.com/introduction
- https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- https://github.com/anthropic-experimental/sandbox-runtime
- http://osworld-v1.xlang.ai/
- https://code.claude.com/docs/en/workflows
- https://code.claude.com/docs/en/env-vars
