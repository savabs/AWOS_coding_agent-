# AI and agent frameworks

*Expedition chart 17. Synthesised 2026-10-10 from four scout reports covering: (a) agent frameworks compared, (b) prompt/program optimisation and evaluation frameworks, (c) tool/context protocols and observability, (d) build-your-own-kernel versus adopt-a-framework.*

**Coverage caveat.** The session's shared WebSearch pool (200 calls for all agents) ran out on the scouts' first queries. Every finding here therefore comes from direct fetches of primary docs, papers, repos and engineering blogs. Coverage of 2026 framework releases (LangGraph 1.x, Google ADK, Microsoft Agent Framework GA) is thin. Adoption numbers come from one secondary roundup and are unverified. Most production figures are self-reported by vendors or authors and are marked as such.

---

## 0. The user's direct question: why does this workflow stop at about 8 parallel agents?

**Short answer:** it is a Claude Code workflow-runtime default, not an AWOS limit and not a hard limit. You can raise it.

- **Where it comes from.** The workflow runtime allows "up to 16 concurrent agents by default, fewer when Claude Code has fewer CPUs available" ([workflows docs](https://code.claude.com/docs/en/workflows.md)). This Mac reports `hw.ncpu=10` (4 performance + 6 efficiency cores). That most likely produces the ~8 cap, but the scaling formula is not published, so this part is an inference.
- **Lever 1: the concurrency env var.** Set `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (allowed 1-256, needs v2.1.269+; installed version is 2.1.296) under `"env"` in `~/.claude/settings.json`, e.g. `"24"`. It is not set today.
- **Lever 2: the size guideline.** The default `workflowSizeGuideline` is "medium", which tells Claude to write workflows with fewer than 10 agents. Switch it with `/config` to `large` (<50) or unrestricted. The docs warn above 25 agents or 1.5M projected tokens.
- **Lever 3: shared quotas.** All agents in a session share one pool of 200 WebSearch calls, which refills at about 100 per hour. This run drained it immediately. For research fan-outs, raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` too. Otherwise extra agents just starve.
- **Other limits.** Plain Agent-tool subagents cap at 20 concurrent (`CLAUDE_CODE_MAX_SUBAGENT_CONCURRENT_LIMIT`; [subagents docs](https://code.claude.com/docs/en/sub-agents)). Each run is capped at 1,000 agents and 4,096 items per `parallel()`/`pipeline()` call.
- **What binds next.** The agents mostly wait on the network, so CPU is not the limit after the cap is raised. Expect API rate limits, plan usage limits and token cost to bind instead. Multi-agent runs use about 15x the tokens of chat ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- **Is it worth it?** More agents help only for independent, separately verifiable pieces, such as breadth research. Merging their results is the real bottleneck. In Combee, a single reflection step fed 100 parallel traces fell from 87.0% to 72.5% accuracy; hierarchical map-reduce merging fixed it ([GEPA/Combee](https://gepa-ai.github.io/gepa/blog/2026/04/09/gepa-at-scale-with-combee/)). Above roughly 8-16 workers, merge in sub-groups first.

No settings were changed by the expedition. Changing them is the owner's call.

---

## 1. Summary: the territory in 2026

- **The best production agents own their loop.** Claude Code, Codex CLI, Amp, Aider, Manus, Browser Use and OpenHands all run home-grown loops on raw model APIs. Anthropic found the most successful teams "weren't using complex frameworks" ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Even LangChain's CEO concedes that agent *abstractions* hurt reliability. He defends frameworks only as runtime infrastructure ([LangChain](https://www.langchain.com/blog/how-to-think-about-agent-frameworks)).
- **Frameworks have converged on durable execution as their real value:** checkpoint, resume, human-in-the-loop. LangGraph checkpointers, Pydantic AI on 8 durable engines (Temporal, DBOS, Prefect, Restate…), Mastra suspend/resume, and Claude Code's deterministic replay all provide it.
- **Multi-agent orchestration is contested and failure-prone.** MAST found 60-87% failure rates across 7 frameworks over 1,642 traces. Failures concentrate in system design (~44%), inter-agent misalignment (~32%) and verification (~24%) ([MAST](https://arxiv.org/abs/2503.13657)). Cognition says "Don't build multi-agents" ([Cognition](https://cognition.com/blog/dont-build-multi-agents)).
- **The interesting frontier has moved from orchestration frameworks to optimiser frameworks.** DSPy/GEPA, Meta-Harness and gskill optimise prompts, skills and even harness code against a metric. GEPA beats GRPO RL with up to 35x fewer rollouts ([GEPA](https://arxiv.org/abs/2507.19457)). A 2026 result adds a warning: without regression control, these gains do not compound ([2607.14004](https://arxiv.org/abs/2607.14004)).
- **AutoGen is in maintenance mode** ([repo](https://github.com/microsoft/autogen)). Microsoft Agent Framework is its successor. Vendor SDKs (OpenAI Agents SDK, still pre-1.0; Claude Agent SDK) are thin harnesses tilted toward their own models.
- **Protocols have consolidated under the Linux Foundation.** MCP and AGENTS.md sit in the Agentic AI Foundation, and A2A reached v1.0. MCP 2026-07-28 went stateless, deprecated Sampling and Logging, and added cacheable, deterministically ordered tool lists ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)). Agent Skills (SKILL.md) is a de facto cross-vendor format ([agentskills.io](https://agentskills.io/)).
- **Observability is converging on OpenTelemetry GenAI conventions.** They cover agent, tool and MCP spans but are still in "Development" status ([semconv-genai](https://github.com/open-telemetry/semantic-conventions-genai)). Inspect is the de facto open eval harness ([Inspect](https://inspect.aisi.org.uk/)).
- **GitHub stars and real usage diverge sharply.** AutoGen has ~59-61K stars but ~856K monthly downloads. LangGraph has ~34K stars but ~34.5M downloads (secondary source, unverified).

---

## 2. What matters most (ranked)

1. **Verification and termination, not orchestration, decide reliability.** In MAST, incorrect or missing verification is 17.3% of failures, and step repetition plus not knowing when to stop add ~28%. In ChatDev, adding objective verification helped more (+15.6%) than refining roles (+9.4%). MCPMark shows frontier models at 52.6% pass@1 but only 33.9% pass^4 on state-changing tool tasks, each judged by a verifier script. *Evidence:* [MAST](https://arxiv.org/html/2503.13657), [MCPMark](https://arxiv.org/abs/2509.24002).

2. **Own the prompt, the context window and the control flow, and keep the KV cache warm.** Manus calls KV-cache hit rate "the single most important metric". Cached input costs 10x less on Claude Sonnet ($0.30 vs $3/MTok). Manus keeps context append-only and masks tools instead of removing them. 12-Factor Agents describes a "framework ceiling" at 70-80% quality, past which teams reverse-engineer the framework (practitioner opinion). *Evidence:* [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus), [12-Factor](https://github.com/humanlayer/12-factor-agents).

3. **Reflective optimisation against a metric, behind a regression gate.** GEPA: +6pp on average over GRPO, up to +20pp, with 35x fewer rollouts. On Qwen3-8B it gains +12.4% versus MIPROv2's +5.6%, using 100-500 evaluations versus 5k-25k for RL. gskill (GEPA + SWE-smith synthetic tasks) took mini-SWE-agent/gpt-5-mini from 24% to 93% on Bleve and from 55% to 82% on Jinja, and the skills transferred to Claude Code/Haiku (self-reported; the tasks are simple). **But** on Terminal-Bench 2.0, the GEPA-optimised agent scored *below baseline* on new tasks. Regression-controlled RELAI-VCL reached 76.4% versus GEPA 66.0% and baseline 58.7% (partly self-reported). *Evidence:* [GEPA](https://arxiv.org/abs/2507.19457), [gskill](https://gepa-ai.github.io/gepa/blog/2026/02/18/automatically-learning-skills-for-coding-agents/), [2607.14004](https://arxiv.org/abs/2607.14004).

4. **Fewer visible tools, especially for small models.** "Less is More" on edge LLMs: dynamically cutting the tool set reduced execution time by up to 70% and power by up to 40%, with no fine-tuning. RAG-MCP raised tool-selection accuracy from 13.6% to 43.1% with over 50% fewer prompt tokens. *Evidence:* [2411.15399](https://arxiv.org/abs/2411.15399), [RAG-MCP](https://arxiv.org/abs/2505.03275).

5. **Code as the action format.** CodeAct gives up to +20% success across 17 LLMs. smolagents reports ~30% fewer steps (self-reported). Anthropic's programmatic tool calling cut tokens by 37% and raised accuracy from 46.5% to 51.2% (self-reported, frontier models). Code actions need a real sandbox: smolagents states that its local executor "is not a security boundary". *Evidence:* [CodeAct](https://arxiv.org/abs/2402.01030), [code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp), [advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use).

6. **Durable execution is the one layer worth buying or copying.** Temporal names Codex and Replit Agent 3 as production users (vendor claim). DBOS is a library over SQLite or Postgres with no server. OpenHands V1 (event sourcing, stateless design, co-located execution) cut system-attributable failures from 78.0 to 30.0 per 1,000 conversations (-61%). *Evidence:* [Temporal](https://www.temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal), [DBOS](https://www.dbos.dev/blog/durable-execution-crashproof-ai-agents), [OpenHands SDK](https://arxiv.org/html/2511.03690).

7. **Parallel fan-out pays for breadth, at a token cost.** Anthropic's research system (Opus 4 lead with Sonnet 4 subagents) scored +90.2% over single-agent Opus 4 and cut time by up to 90%. It used ~15x the tokens of chat, token usage explained 80% of the variance, and leads typically spawn 3-5 subagents. Anthropic names coding as a poor fit. *Evidence:* [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system).

8. **Tool metadata is an attack surface.** MCPTox covers 45 real servers and 353 tools: o1-mini had a 72.8% attack success rate, more capable models were *more* susceptible, and the best refuser refused under 3%. CaMeL separates control flow from data flow and solves 77% of AgentDojo tasks with provable security, versus 84% undefended. *Evidence:* [MCPTox](https://arxiv.org/abs/2508.14925), [CaMeL](https://arxiv.org/abs/2503.18813), [Invariant Labs](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks).

---

## 3. What does NOT matter: hype and dead ends

| Thing | Why it doesn't matter |
|---|---|
| Adopting LangGraph, CrewAI, Mastra or AutoGen as AWOS's kernel | Abstractions hide the exact prompt and context, break prefix caching, and own the control flow where AWOS needs its edge. No framework provides verified replay, local-first routing, a verification gate or evidence-only memory. |
| AutoGen/AG2 conversational "agents debating" | Maintenance mode. N agents x R rounds of calls (4 agents x 5 rounds = 20+ calls). High misalignment and termination failures in MAST. |
| Role-play crews (CEO/CTO/coder personas, ChatDev, MetaGPT) | Add handoff loss without independent verification. Success stayed low on ChatDev even after fixes. Teams migrate to explicit graphs as complexity grows. |
| GitHub stars as an adoption signal | Stars run roughly 40x out of line with downloads (AutoGen vs LangGraph). Use downloads and named deployments. |
| Raising agent count as a general quality lever | Token spend explains 80% of the gain. Without independent subtasks, a verifier and hierarchical merging, more agents mostly multiply cost (Combee: 87.0% to 72.5%). |
| TextGrad, Trace/OptoPrime, COPRO/OPRO | Superseded by GEPA, MIPROv2 and Meta-Harness. Few-shot demos also cost context on every call, which is bad for small context windows and prefix caching. |
| MCP Sampling, the old MCP Tasks API, MCP Logging | Deprecated or redesigned in 2026-07-28. AWOS's router should own model calls, and OTel replaces logging. |
| A2A on a single-owner box | Real (v1.0, Linux Foundation) but built for cross-organisation federation. It adds nothing to work per dollar-second-watt on one machine. |
| A Temporal cluster on the local box | The idea is right but the tool is too heavy for one machine. A SQLite library or an owned journal gets most of the benefit. |
| Picking a trace UI vendor | Langfuse (now ClickHouse-owned but still OSS), Phoenix (ELv2) and others all ingest OTLP. Lock-in comes from the data shape, not the UI. |
| Headline self-reported numbers (98.7% token cut, 90x cheaper, ARC-AGI 32% to 89%, Shopify 550x) | Hand-picked workflows on frontier models. Treat them as hypotheses to A/B, not facts. |
| Market forecasts ($52B by 2030 and similar) | Say nothing about which architecture works. |

---

## 4. Key papers and resources

### Must-read
- **MAST: Why Do Multi-Agent LLM Systems Fail?** https://arxiv.org/abs/2503.13657. A 14-mode taxonomy over 1,642 traces. Could serve as a stress-test set for the AWOS loop breaker and gate.
- **GEPA: Reflective Prompt Evolution Can Outperform RL** (ICLR 2026 oral). https://arxiv.org/abs/2507.19457 · repo https://github.com/gepa-ai/gepa. Optimises any text artifact against a custom evaluator that returns traces.
- **Do Agent Optimizers Compound?** https://arxiv.org/abs/2607.14004. Regression control is mandatory for self-improvement.
- **How we built our multi-agent research system** (Anthropic). https://www.anthropic.com/engineering/multi-agent-research-system. The quantitative case for and against fan-out.
- **Don't Build Multi-Agents** (Cognition). https://cognition.com/blog/dont-build-multi-agents. Share full traces; actions carry implicit decisions.
- **Context Engineering: Lessons from Building Manus.** https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus. KV-cache, append-only context, tool masking.
- **OpenHands Software Agent SDK** (MLSys 2026). https://arxiv.org/html/2511.03690. A measured kernel rewrite with -61% failures.
- **Claude Code workflows docs.** https://code.claude.com/docs/en/workflows.md. Concurrency cap, size guideline, deterministic replay.

### Useful
- gskill, learned per-repo skills: https://gepa-ai.github.io/gepa/blog/2026/02/18/automatically-learning-skills-for-coding-agents/
- Combee, parallel learning with map-shuffle-reduce: https://gepa-ai.github.io/gepa/blog/2026/04/09/gepa-at-scale-with-combee/
- GEPA parallel proposals (3-4x wall-clock gain at up to 16-way): https://gepa-ai.github.io/gepa/blog/2026/07/30/parallel-proposals/
- Meta-Harness, optimising harness code: https://arxiv.org/abs/2603.28052
- Learning Fast and Slow (GEPA + RL): https://gepa-ai.github.io/gepa/blog/2026/05/11/learning-fast-and-slow/ · BetterTogether: https://arxiv.org/abs/2407.10930
- AdaMAST named failure modes: https://gepa-ai.github.io/gepa/blog/2026/10/06/named-failure-modes/
- ACE, delta-update playbooks: https://arxiv.org/abs/2510.04618
- CodeAct: https://arxiv.org/abs/2402.01030 · smolagents: https://github.com/huggingface/smolagents
- Less is More (edge tool reduction): https://arxiv.org/abs/2411.15399 · RAG-MCP: https://arxiv.org/abs/2505.03275
- MCPTox: https://arxiv.org/abs/2508.14925 · CaMeL: https://arxiv.org/abs/2503.18813 · MCPMark: https://arxiv.org/abs/2509.24002
- Inspect parallelism and concurrency: https://inspect.aisi.org.uk/parallelism.html · https://inspect.aisi.org.uk/models-concurrency.html
- Agentic Benchmark Checklist: https://arxiv.org/abs/2507.02825 · HAL: https://arxiv.org/abs/2510.11977
- 12-Factor Agents: https://github.com/humanlayer/12-factor-agents · mini-swe-agent: https://github.com/SWE-agent/mini-swe-agent
- Browser Use, "bitter lesson" of frameworks: https://browser-use.com/posts/bitter-lesson-agent-frameworks
- Effective harnesses for long-running agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Pydantic AI durable execution: https://pydantic.dev/docs/ai/integrations/durable_execution/overview/ · DBOS: https://www.dbos.dev/blog/durable-execution-crashproof-ai-agents
- Databricks, 90x cheaper with GEPA: https://www.databricks.com/blog/building-state-art-enterprise-agents-90x-cheaper-automated-prompt-optimization

### Reference
- Building effective agents: https://www.anthropic.com/engineering/building-effective-agents (also at /research/)
- LangChain on frameworks: https://www.langchain.com/blog/how-to-think-about-agent-frameworks · LangGraph durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
- DSPy: https://dspy.ai/current/ · optimizer guide: https://raw.githubusercontent.com/stanfordnlp/dspy/main/docs/docs/learn/optimization/optimizers.md
- MCP 2026-07-28 changelog: https://modelcontextprotocol.io/specification/2026-07-28/changelog · roadmap: https://modelcontextprotocol.io/development/roadmap
- OTel GenAI conventions: https://github.com/open-telemetry/semantic-conventions-genai · agent spans: https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md
- Agent Skills: https://agentskills.io/ · Writing tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview · https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- Subagents: https://code.claude.com/docs/en/sub-agents · Agent teams: https://code.claude.com/docs/en/agent-teams
- AutoGen (maintenance notice): https://github.com/microsoft/autogen · Mastra: https://github.com/mastra-ai/mastra
- Phoenix: https://github.com/Arize-ai/phoenix · Langfuse joins ClickHouse: https://langfuse.com/blog/joining-clickhouse
- BFCL V4: https://gorilla.cs.berkeley.edu/leaderboard.html · AAIF: https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- Amp, How to build an agent: https://ampcode.com/how-to-build-an-agent · LangGraph showcase (vendor claims): https://www.langchain.com/built-with-langgraph
- Framework roundup (secondary, unverified): https://the-agent-report.com/2026/07/ai-agent-frameworks-comparison-2026-langgraph-crewai-autogen/

---

## 5. Implications for AWOS

**Verdict: keep the thin custom kernel.** AWOS (~56k lines in `scaffold/`) has no framework dependency, and the evidence says that is correct. Borrow patterns, not frameworks.

### Adopt (low cost, strong evidence)
1. **Regression-gated self-improvement** (memory gate / idle-time practice T9). Any learned prompt, skill, routine or harness change must pass a frozen held-out slice of past owner tasks before admission. This is the Gatekeeper's "memory only from strong evidence" rule in measurable form ([2607.14004](https://arxiv.org/abs/2607.14004)).
2. **Per-task tool subsetting for the local tier** (local attempt stage). Show the small model only the 3-8 tools its task class needs, taken from the verified routine's manifest or retrieved from the skill index. Measure success, tokens, seconds and Wh on the 73-issue set.
3. **Durable replay journal for the always-on host (M1).** Append-only event log, with replay of recorded model and tool results on crash (the OpenHands V1 and Claude-workflow pattern). Evaluate DBOS-on-SQLite against an owned journal; do not run a Temporal cluster.
4. **OTel mapping for `.awos/spans.jsonl`.** Add `gen_ai.*`/`mcp.*` attribute names plus `awos.gate_verdict`, `awos.repair_round` and `awos.wh`. Keep the JSONL as the source of truth while the conventions are unstable.
5. **Untrusted-by-default third-party tools and skills** (sink policy T10). Pin MCP tool descriptions and imported SKILL.md files by hash, diff them on change, and run a sandboxed trial before promotion.
6. **A frozen failure taxonomy from `error_patterns.jsonl`**, fed to the bounded-repair prompt and the loop breaker (AdaMAST reports +2-10pp at equal budget, self-reported).

### Test (A/B on the 73-issue set, pre-registered)
- **GEPA/gskill over the local model's prompts and per-repo skills**, with the test gate as the metric. Measure on real issues, not only synthetic SWE-smith tasks.
- **Code-mode for the local tier**: verified routines and compiled tools (T8) exposed as a sandboxed Python API, versus N JSON tools. All published evidence is from frontier models.
- **Minimal-loop ablation per model tier**: compare each kernel component (constrained SEARCH grammar, fuzzy apply, skeleton viewer, stable prefix) against a mini-swe-agent-style ~100-line baseline. Frontier models may not need the scaffolding; 4-9B models may.
- **Parallel best-of-N plus test gate versus sequential bounded repair**, on work per dollar-second-watt. Local inference on unified memory serialises, so measure throughput as the number of concurrent sessions on one batching MLX/llama.cpp server grows.
- **Adaptive concurrency for cloud escalation**: an Inspect-style AIMD limiter (start at about 20 requests, multiply by 0.8 on 429, 15 s cooldown) instead of a fixed cap. For the local server, use a static cap sized to the KV cache.
- **Stronger baselines**: Claude Agent SDK or OpenHands SDK alongside Aider (the current result is 64% vs 61%, not significant). Apply ABC validity checks to the graders first.

### Watch
- MCP progressive discovery and ETag caching of tool results (the roadmap). These could later serve as a replay substrate.
- OTel GenAI conventions moving to Stable.
- Prompt optimisation combined with LoRA/RL (BetterTogether: 11.6% weights-only, 10.6% prompts-only, 21.2% both on HoVer-hard) for when AWOS starts training the local model.
- Microsoft Agent Framework and OpenAI Agents SDK reaching 1.0.

### Ignore
- AutoGen/AG2, CrewAI-style crews, A2A, MCP Sampling, TextGrad/Trace, star counts, and vendor headline numbers until reproduced.

### For developing AWOS itself (owner's parallel-work rule)
Raise the workflow cap, the size guideline and the search quota together. Give each parallel agent its own worktree and state directory, because `.awos/` ledgers and the git index collide. Merge hierarchically when fan-out exceeds about 8-16. Spend parallelism on research and test-checked candidate patches, never on agents co-editing the critical path.

---

## 6. Open questions worth exploring next

1. What exact CPU formula sets the default workflow cap? And after raising it on this account, which limit binds first: API rate, plan usage or the search pool? Measure runs at 16 and 32.
2. Do GEPA/gskill gains hold for a local 4-9B model on real AWOS issues with a held-out regression gate? Do skills learned with frontier models transfer *down* to 8B?
3. What is the cheapest regression-control design that makes optimiser gains compound? RELAI-VCL is partly proprietary. Is a frozen slice of past owner tasks enough?
4. Does code-mode beat JSON tool calls for 7-32B models on success, tokens, seconds and Wh? What is the optimal visible-tool count per task class?
5. What is the throughput-optimal number of concurrent local sessions on one Apple-silicon batching server, as a function of model size and context length?
6. DBOS-on-SQLite or an owned event-sourced journal for M1? Compare per-step overhead and crash-replay correctness.
7. How much does MAST's taxonomy overlap with AWOS `error_patterns.jsonl`? Can MAST data stress-test the loop breaker?
8. Is MCPMark's "initial state + verifier script" format enough as the computer-use gate spec, or are mid-trajectory state probes needed?
9. Not yet checked because the search budget ran out: 2026 framework releases, per-model BFCL V4 scores for small open models, independent A2A adoption data.

---

## 7. Sources

- https://code.claude.com/docs/en/workflows.md
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/agent-teams
- https://code.claude.com/docs/en/agent-sdk/overview
- https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.anthropic.com/engineering/building-effective-agents
- https://www.anthropic.com/research/building-effective-agents
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/engineering/code-execution-with-mcp
- https://www.anthropic.com/engineering/advanced-tool-use
- https://www.anthropic.com/engineering/writing-tools-for-agents
- https://cognition.com/blog/dont-build-multi-agents
- https://arxiv.org/abs/2503.13657 · https://arxiv.org/html/2503.13657
- https://arxiv.org/abs/2507.19457
- https://arxiv.org/abs/2607.14004
- https://arxiv.org/abs/2603.28052
- https://arxiv.org/abs/2510.04618
- https://arxiv.org/abs/2407.10930
- https://arxiv.org/abs/2406.07496
- https://arxiv.org/abs/2402.01030
- https://arxiv.org/abs/2507.02825
- https://arxiv.org/abs/2510.11977
- https://arxiv.org/abs/2411.15399
- https://arxiv.org/abs/2505.03275
- https://arxiv.org/abs/2508.14925
- https://arxiv.org/abs/2503.18813
- https://arxiv.org/abs/2509.24002
- https://arxiv.org/html/2511.03690
- https://github.com/gepa-ai/gepa
- https://gepa-ai.github.io/gepa/blog/2026/02/18/automatically-learning-skills-for-coding-agents/
- https://gepa-ai.github.io/gepa/blog/2026/04/09/gepa-at-scale-with-combee/
- https://gepa-ai.github.io/gepa/blog/2026/07/30/parallel-proposals/
- https://gepa-ai.github.io/gepa/blog/2026/05/11/learning-fast-and-slow/
- https://gepa-ai.github.io/gepa/blog/2026/10/06/named-failure-modes/
- https://gepa-ai.github.io/gepa/blog/2026/07/22/optimize-anything-omni/
- https://dspy.ai/current/
- https://raw.githubusercontent.com/stanfordnlp/dspy/main/docs/docs/learn/optimization/optimizers.md
- https://www.databricks.com/blog/building-state-art-enterprise-agents-90x-cheaper-automated-prompt-optimization
- https://inspect.aisi.org.uk/ · https://inspect.aisi.org.uk/parallelism.html · https://inspect.aisi.org.uk/models-concurrency.html
- https://pydantic.dev/docs/ai/integrations/durable_execution/overview/
- https://docs.langchain.com/oss/python/langgraph/durable-execution
- https://www.langchain.com/blog/how-to-think-about-agent-frameworks
- https://www.langchain.com/built-with-langgraph
- https://www.temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal
- https://www.dbos.dev/blog/durable-execution-crashproof-ai-agents
- https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- https://github.com/humanlayer/12-factor-agents
- https://github.com/SWE-agent/mini-swe-agent
- https://ampcode.com/how-to-build-an-agent
- https://browser-use.com/posts/bitter-lesson-agent-frameworks
- https://github.com/microsoft/autogen
- https://github.com/mastra-ai/mastra
- https://github.com/huggingface/smolagents
- https://the-agent-report.com/2026/07/ai-agent-frameworks-comparison-2026-langgraph-crewai-autogen/
- https://modelcontextprotocol.io/specification/2026-07-28/changelog
- https://modelcontextprotocol.io/development/roadmap
- https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks
- https://github.com/open-telemetry/semantic-conventions-genai
- https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md
- https://agentskills.io/
- https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- https://langfuse.com/blog/joining-clickhouse
- https://github.com/Arize-ai/phoenix
- https://gorilla.cs.berkeley.edu/leaderboard.html
