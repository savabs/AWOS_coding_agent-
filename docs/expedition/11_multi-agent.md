# Multi-agent systems

> Expedition chart 11, written 2026-10-10 from four scout reports. **Method caveat:** the session's shared WebSearch budget (200 calls) ran out early, so the scouts worked mainly from direct reads of known primary sources: arXiv, vendor engineering blogs and the Claude Code docs. Work published in 2026 is thinly covered. A few numbers came from LLM summaries of full-text HTML and are marked "(summary)". They should be checked against the PDFs.

> **Direct answer to the triggering question, "why can we only run 8 agents in parallel?"** The cap comes from the Claude Code workflow runtime, not from AWOS or the method itself. By default a workflow runs up to 16 agents at once, and fewer when Claude Code sees fewer CPUs. This Mac reports 10 logical CPUs, which fits the observed 8. The exact formula is not documented: `ncpu-2` is a guess. To raise it, set `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (1–256, needs v2.1.269+; 2.1.296 is installed) in `~/.claude/settings.json` under `"env"`, and set `workflowSizeGuideline` to `large` or `unrestricted`. Related limits: `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20, for the Agent tool), `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` (default 10), 1,000 agents per run, and 4,096 items per `parallel()`/`pipeline()` call. **The limit that actually hit first in this run was the shared 200-call WebSearch budget** (`CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, which can be raised but not disabled). After that come API rate and usage limits, RAM (every running agent's transcript stays in memory, and this machine has 16 GB), and collisions on a shared git index or the `.awos/` ledgers (separate worktrees fix this). Example config: `{"env":{"CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS":"16","CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION":"600"}}`. Sources: [workflows](https://code.claude.com/docs/en/workflows), [env-vars](https://code.claude.com/docs/en/env-vars), [sub-agents](https://code.claude.com/docs/en/sub-agents).

---

## 1. Summary

- **The controlled evidence is now clear.** Multi-agent setups help on tasks that split into independent parts and hurt on sequential ones. The best study so far (Kim et al., Google Research/DeepMind/MIT) found +80.8% on Finance-Agent with a central coordinator, −39% to −70% on PlanCraft for every multi-agent variant, and 2–15% *worse* than a single agent on SWE-bench Verified ([2512.08296](https://arxiv.org/abs/2512.08296)).
- **There is a capability ceiling.** Once a single agent scores above roughly 45% on a task, extra agents give flat or negative returns (interaction β = −0.236, p = 0.004). The implication is that multi-agent methods may help *weak* models, such as AWOS's local tier, more than strong ones.
- **Topology decides how errors spread.** Compared with a single agent (1.0×), errors amplify 17.2× with independent agents, 7.8× decentralized, 5.1× hybrid and 4.4× with a central verifying orchestrator. Token overhead is +58% to +515%, and success per turn on tool-heavy tasks drops 2–6×.
- **Popular frameworks fail often, and mostly because of how they are designed.** MAST studied 7 frameworks (MetaGPT, ChatDev, AG2, Magentic-One and others) over 1,600+ traces. They failed 41–86.7% of the time. Roughly 44% of failures trace to spec and system design, 32% to misalignment between agents, and 24% to verification ([2503.13657](https://arxiv.org/abs/2503.13657)).
- **Debate and discussion mostly come down to voting.** At equal compute, debate often fails to beat CoT or self-consistency ([2502.08788](https://arxiv.org/abs/2502.08788), [2508.17536](https://arxiv.org/abs/2508.17536)). Repeated samples from the single best model beat mixed-model teams (Self-MoA, [2502.00674](https://arxiv.org/abs/2502.00674)).
- **The productive kind of parallelism is independent attempts plus a verifier.** Large Language Monkeys raised coverage from 15.9% to 56% on SWE-bench Lite at k=250 ([2407.21787](https://arxiv.org/abs/2407.21787)). CodeMonkeys' selector pooled candidates to 66.2%, above any single system ([2501.14723](https://arxiv.org/abs/2501.14723)).
- **In production, multi-agent mostly buys breadth and wall-clock time by spending tokens.** Anthropic's research system scored +90.2% on an internal eval while using about 15× the tokens of a chat, and token usage explained 80% of the variance on BrowseComp. Anthropic calls it a poor fit for most coding ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Leading coding agents are single and linear ([mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), over 74% Verified, self-reported).
- **Protocols have settled.** MCP (agent to tool) and AGENTS.md now sit under the Linux Foundation's Agentic AI Foundation. A2A (agent to agent) reached v1.0 and absorbed IBM's ACP. The open problems are security (tool poisoning in about 5.5% of servers) and reliability on real MCP tasks (GPT-5 scores 43.7%), not plumbing.

## 2. What matters most (ranked)

1. **Check whether the task decomposes before fanning out.** The parallel/sequential split explains the sign of the effect. A predictor built from task properties (how well it decomposes, tool count, single-agent baseline) picks the best architecture for 87% of held-out configurations. Its fit is modest, though: the scouts read cross-validated R² as 0.37–0.41 or 0.524 depending on the version, and report 180 vs 260 configurations, so check against the PDF. [arXiv 2512.08296](https://arxiv.org/abs/2512.08296) · [Google Research blog](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
2. **Put one central verification gate in front of every parallel branch.** It cuts error amplification from 17.2× to 4.4×. MAST's cheapest effective fix was verifying against the top-level objective, which gave +15.6% on ChatDev with the same GPT-4o. [2512.08296 HTML](https://arxiv.org/html/2512.08296) · [MAST](https://arxiv.org/html/2503.13657v3)
3. **Run independent attempts with no communication, and select by execution.** Coverage grows log-linearly with samples. Voting and reward models plateau after a few hundred samples, so the selector's precision is the real limit. [Large Language Monkeys](https://arxiv.org/abs/2407.21787) · [CodeMonkeys](https://arxiv.org/abs/2501.14723)
4. **Parallel readers, one writer.** "Actions carry implicit decisions" ([Cognition](https://cognition.com/blog/dont-build-multi-agents)). Multi-agent works for read tasks and fails for write tasks that need shared context ([LangChain](https://www.langchain.com/blog/how-and-when-to-build-multi-agent-systems)). Anthropic's own agent-teams guidance is to start with 3–5 teammates, because "two teammates editing the same file leads to overwrites" ([agent-teams](https://code.claude.com/docs/en/agent-teams)).
5. **Set the sample count per task, not as a fixed N.** Voting accuracy can rise and then fall as calls increase: more calls help easy queries and hurt hard ones. N can be predicted from a small sample ([2403.02419](https://arxiv.org/abs/2403.02419)).
6. **Use a different model only as a checker.** Using different models is the one debate fix that reliably helps ([2502.08788](https://arxiv.org/abs/2502.08788)). But mixing weaker *generators* drags quality down ([Self-MoA](https://arxiv.org/abs/2502.00674)). Matching models to roles does help: X-MAS reports up to +8.4% on MATH and +47% on AIME pairing chat agents with reasoner agents, though the AIME gain probably reflects adding a reasoner more than collaboration ([2505.16997](https://arxiv.org/abs/2505.16997)).
7. **Juries and debate as verifiers, not generators.** A panel of small judges from different model families (PoLL) tracked human judgment better than one GPT-4 judge at more than 7× lower cost ([2404.18796](https://arxiv.org/abs/2404.18796)). Debate helps weak judges reach 76–88% on QuALITY, against baselines of 48–60% ([2402.06782](https://arxiv.org/abs/2402.06782)). Neither has been tested on code or desktop tasks.
8. **Escalate from single to multi.** Running the single agent first and handing failures to a multi-agent setup gave +1.1–12% accuracy at up to 20% lower cost, and the multi-agent advantage shrinks as models get stronger ([2505.18286](https://arxiv.org/abs/2505.18286)).
9. **Shared prefixes make fan-out affordable.** Claude Code holds back fan-out clones (up to 5 s, `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS`) so their first requests read the prompt prefix already in cache ([workflows](https://code.claude.com/docs/en/workflows)). The local equivalent is batched serving with prefix caching, which is unmeasured on MLX.
10. **Present MCP tools as code and retrieve them per task.** One self-reported example went from 150k to 2k tokens ([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp)). Retrieving tools first raised tool-selection accuracy from 13.6% to 43.1% and cut prompt tokens by more than 50% ([RAG-MCP](https://arxiv.org/abs/2505.03275)).

## 3. What does NOT matter / hype / dead ends

| Thing | Why not |
|---|---|
| Role-play software companies (ChatDev, MetaGPT CEO/CTO/engineer pipelines) | Fail 41–86.7% under MAST. Their gains come from SOPs and verification, not personas. Far behind a 100-line single agent on SWE-bench. |
| Same-model debate or "society of minds" to improve reasoning | Loses or ties against self-consistency at equal compute. Voting explains most of the gain ([2508.17536](https://arxiv.org/abs/2508.17536)). A single agent with strong prompts and demonstrations matches the best discussion method ([2402.18272](https://arxiv.org/abs/2402.18272)). |
| Mixture-of-Agents leaderboard wins | Measured on LLM-judged chat benchmarks (AlpacaEval 65.1 vs GPT-4o 57.5, [2406.04692](https://arxiv.org/abs/2406.04692)). Self-MoA beats MoA. Says little about verifiable work. |
| "More agents is all you need" as a scaling law | Accuracy is non-monotonic ([2403.02419](https://arxiv.org/abs/2403.02419)), there is a ceiling around 45%, and turns grow as T ≈ 2.72·(n+0.5)^1.724 (4-agent hybrid: 44.3 turns vs 7.2). |
| Raising the agent count as a goal in itself | Shared budgets bind first: search, rate limits, RAM, the local GPU. This expedition is the proof. Concurrency helps only for independent read or sample work. |
| Peer-to-peer swarms with no validator | 7.8–17.2× error amplification. Failure attribution is nearly impossible. |
| LLM blame attribution to learn from multi-agent failures | Who&When: 53.5% accuracy at the agent level, 14.2% at the step level, even with o1/R1 ([2505.00212](https://arxiv.org/abs/2505.00212)). Too noisy to write into durable memory. |
| Generic frameworks (AutoGen/AG2 group chat, CAMEL, OpenAI Swarm) as a foundation | Fine for prototypes, but MAST traces nearly half of failures to framework-level spec and design. A small custom orchestrator with explicit gates is easier to debug. |
| A2A as internal architecture, IBM ACP, DID-based agent marketplaces | A2A targets interop across organizations. ACP merged into A2A in Aug 2025. Marketplaces showed no measured usefulness, and auto-trusting unknown agents conflicts with local-first privacy. |
| Carrying Anthropic's +90% research gain over to coding | Internal, self-reported eval on breadth-first research, at 15× tokens. Anthropic itself excludes most coding. |

## 4. Key papers and resources

### Must-read
- **Towards a Science of Scaling Agent Systems** (Kim et al., 2025). https://arxiv.org/abs/2512.08296 · HTML https://arxiv.org/html/2512.08296 · blog https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/ — the controlled study: topologies, the 45% ceiling, error amplification, overhead, and the architecture predictor.
- **Why Do Multi-Agent LLM Systems Fail? (MAST)**. https://arxiv.org/abs/2503.13657 · https://arxiv.org/html/2503.13657v3 — 14 failure modes, kappa 0.88, a released LLM annotator that could label AWOS traces.
- **How we built our multi-agent research system** (Anthropic). https://www.anthropic.com/engineering/multi-agent-research-system — production orchestrator-worker numbers, token multipliers, and why coding is a poor fit.
- **Don't Build Multi-Agents** (Cognition). https://cognition.com/blog/dont-build-multi-agents — share full traces and keep a single writer. It describes the mid-2025 state.
- **Large Language Monkeys**. https://arxiv.org/abs/2407.21787 — repeated sampling plus a verifier scales, and selectors plateau.
- **Claude Code workflows and env vars** (the answer to the 8-agent question). https://code.claude.com/docs/en/workflows · https://code.claude.com/docs/en/env-vars · https://code.claude.com/docs/en/sub-agents

### Useful
- Stop Overvaluing Multi-Agent Debate: https://arxiv.org/abs/2502.08788
- Debate or Vote (NeurIPS 2025 Spotlight): https://arxiv.org/abs/2508.17536
- Rethinking Mixture-of-Agents (Self-MoA): https://arxiv.org/abs/2502.00674
- CodeMonkeys: https://arxiv.org/abs/2501.14723 — serial plus parallel compute on SWE-bench, selector reaching 66.2%.
- Replacing Judges with Juries (PoLL): https://arxiv.org/abs/2404.18796
- Are More LLM Calls All You Need?: https://arxiv.org/abs/2403.02419
- Single-agent or Multi-agent? Why Not Both?: https://arxiv.org/abs/2505.18286
- AFlow (MCTS workflow search; small models beat GPT-4o at 4.55% of the cost on some tasks): https://arxiv.org/abs/2410.10762
- AI Agents That Matter (cost-controlled Pareto evaluation): https://arxiv.org/abs/2407.01502
- Who&When (failure attribution): https://arxiv.org/abs/2505.00212
- mini-swe-agent (the single-agent baseline to beat): https://github.com/SWE-agent/mini-swe-agent
- Agentless (32.0% on Lite at $0.70 per issue): https://arxiv.org/abs/2407.01489
- LangChain, how and when to build multi-agent systems: https://www.langchain.com/blog/how-and-when-to-build-multi-agent-systems
- Claude Code agent teams: https://code.claude.com/docs/en/agent-teams
- MCP Tasks (experimental durable jobs): https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks · changelog https://modelcontextprotocol.io/specification/2025-11-25/changelog
- Code execution with MCP: https://www.anthropic.com/engineering/code-execution-with-mcp
- RAG-MCP: https://arxiv.org/abs/2505.03275
- CaMeL (prompt-injection defense by design; 77% vs 84% undefended on AgentDojo): https://arxiv.org/abs/2503.18813
- MCP server security study (1,899 servers): https://arxiv.org/abs/2506.13538
- The lethal trifecta: https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/

### Reference
- X-MAS (heterogeneous LLMs, 27 models): https://arxiv.org/abs/2505.16997
- Magentic-One (orchestrator with task and progress ledgers, AutoGenBench): https://arxiv.org/abs/2411.04468
- Debating with More Persuasive LLMs: https://arxiv.org/abs/2402.06782
- Rethinking the Bounds of LLM Reasoning (single agent matches discussion): https://arxiv.org/abs/2402.18272
- More Agents Is All You Need: https://arxiv.org/abs/2402.05120
- Mixture-of-Agents: https://arxiv.org/abs/2406.04692
- Multiagent Finetuning: https://arxiv.org/abs/2501.05707
- MCP-Universe benchmark: https://arxiv.org/abs/2508.14704
- A2A protocol: https://a2a-protocol.org/latest/
- Agent Skills standard: https://agentskills.io/
- Agentic AI Foundation launch: https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- Agent Client Protocol (editor to agent): https://agentclientprotocol.com/overview/introduction

## 5. Implications for AWOS

The evidence **supports the Gatekeeper shape as it stands**: one writer worker behind one central verification gate. On coding and terminal tasks, which are AWOS's testbed, multi-agent coordination loses (SWE-bench −2 to −15%, Terminal-Bench −19.2% to +1.7%). Computer use is sequential and tool-heavy, which is the regime where multi-agent setups degrade most. AWOS's own result (64% vs Aider 61%, not significant) matches the general finding that gains come from edit reliability and verification, not architectural complexity.

**Adopt**
- **Rule: parallel readers, one writer.** Inside a job, parallelize only read-only side work (repo search, test runs, research, state probes). Writes to the repo or desktop stay on one thread.
- **Every branch goes through the gate.** No parallel output is accepted without the verification gate. This is the 17.2× → 4.4× lever.
- **Use MAST categories as failure labels.** Tag escalation traces as spec, termination, or verification. Termination and repetition overlap with the existing loop-breaker work (T2). Use deterministic outcome signals, never LLM blame attribution, for "memory only from strong evidence".
- **MCP as the tool bus for local computer use**, with a capability and sink policy per server (extends T10), pinned server versions and descriptions, no auto-install from registries, and CaMeL-style data-flow tags before touching the owner's mail, files or browser.
- **For AWOS development itself:** raise `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (try 12, then 16, watching RAM) *together with* `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`. Give each agent its own files or worktree, per the §9 rule. Wider fan-out pays off for research and exploration sweeps and for independent benchmark issues, not for edits to the same files or ledgers.

**Test (pre-register, measured in solved issues per dollar × second × watt)**
1. **Parallel local best-of-N before cloud escalation.** If the local model scores well below 45% on the 73-issue set, the ceiling result predicts N verifier-gated local attempts could beat one cloud call on cost per solved issue. The known risk is weak selection: AWOS's earlier best-of-3 was capped by verifier precision. Measure coverage and selected accuracy separately.
2. **Adaptive N.** Predict the sample count from cheap difficulty signals (localization confidence, first-attempt gate result) rather than a fixed N.
3. **Checker from a different model family.** A local generator checked by a different local or cloud model, compared with same-model self-check, at equal cost.
4. **PoLL-style soft gate.** A jury of 2–3 small local judges from different families for tasks without tests (computer use), checked against a single judge on labelled acceptance outcomes.
5. **Throughput curve for batched local fan-out.** On MLX/M5, measure solved issues and joules against N with a shared stable prefix (builds on T7/T7b). Find where batching stops paying.
6. **Single → fan-out cascade** as one escalation rung between bounded repair and the cloud, chosen by a decomposability router (in the style of the Kim et al. predictor).

**Watch**
- MCP Tasks leaving experimental status. It is a ready-made job API (working / input_required / completed, TTL, cancel) for the always-on host's queue.
- MCP sampling-with-tools as the routing seam between local and cloud models.
- AFlow-style workflow search during idle time, compiled into verified routines (T8). Store routines in the Agent Skills format.
- Multiagent Finetuning, if AWOS later trains several LoRAs on owner traces, to avoid self-training collapse.

**Ignore**
- Role-play teams, same-model debate, mixed-model MoA generation, swarms without a validator, A2A or marketplaces as kernel architecture, and agent count as a goal in itself.

## 6. Open questions worth exploring next

1. Does the ~45% ceiling hold for 7–30B local models on AWOS's real issues? If so, fan-out helps the local tier and not the cloud tier. (The threshold itself came partly from an LLM summary; confirm against the PDF.)
2. What is the selector precision ceiling for coding on AWOS's tasks, and is parallel best-of-N or serial repair better at equal dollars? CodeMonkeys suggests a mix of both.
3. Energy: K parallel small-model attempts (batched) against one larger local model, measured in joules per solved issue on Apple silicon. No data exists.
4. Can MAST's released annotator label AWOS escalation traces cheaply and accurately enough to feed the repair loop?
5. Does debate or a jury outperform a single judge as an acceptance signal for desktop tasks without tests?
6. Development ops: what is the exact CPU → cap formula, and at 16–24 agents on the 16 GB Mac, which limit binds first (RAM, rate limits, search budget, or human merge and review)? A quick empirical run would settle it.
7. 2026 literature gap: the search budget ran out, so follow-up work on Kim et al., newer Cognition/OpenAI posts on parallel agents, and post-2025-11-25 MCP revisions were not surveyed. Re-run a search pass with a higher budget.

## 7. Sources

- https://code.claude.com/docs/en/workflows
- https://code.claude.com/docs/en/env-vars
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/agent-teams
- https://arxiv.org/abs/2512.08296
- https://arxiv.org/html/2512.08296
- https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/
- https://arxiv.org/abs/2503.13657
- https://arxiv.org/html/2503.13657v3
- https://arxiv.org/abs/2502.08788
- https://arxiv.org/abs/2508.17536
- https://arxiv.org/abs/2402.18272
- https://arxiv.org/abs/2502.00674
- https://arxiv.org/abs/2406.04692
- https://arxiv.org/abs/2505.16997
- https://arxiv.org/abs/2402.05120
- https://arxiv.org/abs/2403.02419
- https://arxiv.org/abs/2407.21787
- https://arxiv.org/abs/2501.14723
- https://arxiv.org/abs/2404.18796
- https://arxiv.org/abs/2402.06782
- https://arxiv.org/abs/2505.18286
- https://arxiv.org/abs/2505.00212
- https://arxiv.org/abs/2410.10762
- https://arxiv.org/abs/2407.01502
- https://arxiv.org/abs/2407.01489
- https://arxiv.org/abs/2411.04468
- https://arxiv.org/abs/2501.05707
- https://arxiv.org/abs/2505.03275
- https://arxiv.org/abs/2508.14704
- https://arxiv.org/abs/2506.13538
- https://arxiv.org/abs/2503.18813
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.anthropic.com/engineering/code-execution-with-mcp
- https://cognition.com/blog/dont-build-multi-agents
- https://www.langchain.com/blog/how-and-when-to-build-multi-agent-systems
- https://github.com/SWE-agent/mini-swe-agent
- https://modelcontextprotocol.io/specification/2025-11-25/changelog
- https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks
- https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- https://a2a-protocol.org/latest/
- https://agentskills.io/
- https://agentclientprotocol.com/overview/introduction
- https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method: arXiv's API returned HTTP 429 (shared rate limit), so abstracts were read through the Hugging Face papers API (`huggingface.co/api/papers/<id>`, which mirrors arXiv abstracts). Specs and releases came from GitHub. Findings below are abstract-level; none of the PDFs were read, so effect sizes are as the authors report them.

### New since the chart (dated, with URLs; most important first)

1. **MCP spec revision 2026-07-28 (released after the chart's 2025-11-25 baseline).** The changelog makes MCP stateless (no `initialize` handshake, no `Mcp-Session-Id`), moves Tasks out of core into an official extension (`io.modelcontextprotocol/tasks`, poll via `tasks/get`, new `tasks/update`, `tasks/list` removed), and **deprecates Roots, Sampling and Logging**. Source: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx (tag `2026-07-28` exists). Why it matters: the chart's "Watch" items (MCP Tasks leaving experimental; sampling-with-tools as the local/cloud routing seam) are overtaken. See Corrections.
2. **The Illusion of Multi-Agent Advantage (2026-06-13).** Automatically generated multi-agent systems consistently underperform CoT with self-consistency (CoT-SC) on reasoning and BrowseComp-Plus, at up to 10x the cost. https://arxiv.org/abs/2606.13003 Why it matters: independent support for the chart's "debate is voting" and "ignore swarms" rows, using a cost-matched single-agent baseline.
3. **Scaling Test-Time Compute for Agentic Coding (2026-04-16).** Summarizing each rollout, then Recursive Tournament Voting (parallel) plus Parallel-Distill-Refine (sequential): Claude-4.5-Opus 70.9% to 77.6% on SWE-bench Verified (mini-SWE-agent) and 46.9% to 59.1% on Terminal-Bench v2.0. https://arxiv.org/abs/2604.16529 Why it matters: direct evidence for AWOS test 1 and 6 (independent attempts plus a selector); the selector works on compact trajectory summaries, which is the precision bottleneck the chart flags.
4. **Effective Strategies for Asynchronous SWE Agents / CAID (2026-03-23).** Central manager, isolated git worktrees, branch-and-merge with executable test verification: +26.7 points on PaperBench and +14.3 on Commit0 versus a single agent. https://arxiv.org/abs/2603.21489 Why it matters: a coding-side counterexample to "multi-agent loses on coding", but only for long-horizon, decomposable tasks, and it has the same shape as AWOS's "central gate, isolated writers" rule. It does not cover issue-sized SWE-bench work.
5. **Multi-Agent Computer Use (2026-06-01).** Manager decomposes into a DAG and dispatches parallel computer-use subagents: +3.4 to 25.5% over single-agent baselines on OSWorld and three web benchmarks, about 1.5x faster wall-clock on Odysseys. https://arxiv.org/abs/2606.01533 Why it matters: it challenges the chart's claim that computer use is the regime where multi-agent degrades most. Self-reported by the authors, and the gain is largest on long-horizon tasks; T10 postconditions still apply.
6. **Multi-agent Scaling Across Disjunctive and Compensatory Tasks (2026-09-25).** With up to 30 agents and 13 open-weight models, the chance that at least one agent is right grows 5 to 20 points, yet plurality voting realises almost none of it. https://arxiv.org/abs/2609.31563 Why it matters: this is the selector-precision ceiling for best-of-N, measured with open-weight models like AWOS's local tier.
7. **When Does Multi-Agent Collaboration Help? An Entropy Perspective (2026-06-04).** A single agent beats multi-agent systems in about 43.3% of cases. https://arxiv.org/abs/2602.04234 Rethinking Scale (2026-04-21): for models under 10B, a single agent with tools gives the best performance/cost balance and multi-agent adds overhead for limited gain. https://arxiv.org/abs/2604.19299 Why it matters: answers open question 1 in part for small local models.
8. **Claim Plane (2026-07-24) and AgentRoom (2026-08-24).** Pre-write admission control for parallel coding agents (declared ChangeIntents, deterministic control plane): https://arxiv.org/abs/2607.21909. CRDT-backed shared workspace with file claims over MCP; 2 agents abandon fewer tasks than solo for CLI-stable models: https://arxiv.org/abs/2608.23740 Why it matters: a candidate design for a "parallel writers" rung if AWOS ever needs one, instead of one writer.
9. **When Agents Coordinate (2026-08-17).** 1,902 coding runs: naming a coordinator creates no communication hub and gives no reliable gain; shared files cut output tokens about 42% at 8 agents on message-heavy work; agents sought hidden grading material in about 4/5 of sealed re-runs. https://arxiv.org/abs/2608.16801 Why it matters: supports "gate over role"; the grader-peeking result is a benchmark-hygiene warning for AWOS's 73-issue set.
10. **Who&When Pro (2026-07-10).** 12,326 failed trajectories with golden labels across 26 benchmarks, for automated failure attribution. https://arxiv.org/abs/2607.09996 Why it matters: a better testbed than the original Who&When for the chart's "do not learn from LLM blame" rule; the abstract does not state accuracy, so recheck before reversing it.
11. **Drop the Hierarchy and Roles (2026-03-30).** 25,000 tasks, 4 to 256 agents: a sequential self-organizing protocol beats centralized coordination by 14%, but models below a capability threshold still benefit from rigid structure. https://arxiv.org/abs/2603.28990 Why it matters: a counter-signal to "central orchestrator"; it points the other way for weak local models, which is AWOS's case.
12. **Ecosystem.** A2A v1.0.0 (2026-03-12) and v1.0.1 (2026-05-28): https://github.com/a2aproject/A2A/releases. Claude Code is at v2.1.296 (2026-10-09): https://github.com/anthropics/claude-code/releases. HN has many "agent team" wrappers (e.g. https://news.ycombinator.com/item?id=47602986, 77 points) but no controlled evidence.

### Corrections

- Chart: "MCP Tasks leaving experimental status" as a Watch item. Source: Tasks left the core protocol and became the `io.modelcontextprotocol/tasks` extension with a redesigned API (`tasks/result` replaced by polling, `tasks/list` removed). The chart's "working / input_required / completed, TTL, cancel" description of the old API may no longer hold. https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx
- Chart: "MCP sampling-with-tools as the routing seam between local and cloud models." Source: Sampling is deprecated in 2026-07-28 ("integrate directly with LLM provider APIs instead"). Drop this Watch item; route in AWOS's own provider layer.
- Chart: Kim et al. "interaction β = −0.236, p = 0.004", "+80.8%", and R² "0.37–0.41 or 0.524", "180 vs 260 configurations". The current abstract says 180 configurations, cross-validated R²=0.513, β=−0.408 (p<0.001), and +80.9% on parallelizable tasks. Sequential degradation of 39 to 70% and 17.2x/4.4x match. https://arxiv.org/abs/2512.08296 (the numbers may differ between paper versions; I read the HF-mirrored abstract only).
- Chart: "A2A reached v1.0". Confirmed, but the date is now fixed: v1.0.0 on 2026-03-12.

### Confirmed claims (briefly)

- Workflow concurrency: "up to 16 agents at once, fewer when Claude Code has fewer CPUs", range 1 to 256, v2.1.269+, memory grows with each agent's transcript. https://code.claude.com/docs/en/env-vars
- `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`: default 200, no upper bound, cannot be turned off, v2.1.212+.
- Kim et al.: ~45% capability saturation, 17.2x vs 4.4x error amplification, 87% of held-out configurations predicted, sequential tasks degraded 39 to 70%.
- mini-swe-agent README still says ">74%" on SWE-bench Verified (self-reported); latest release v2.4.6 (2026-07-23). https://github.com/SWE-agent/mini-swe-agent

### Still unverified

- Chart numbers not re-checked this pass: MAST failure rates (41 to 86.7%), Large Language Monkeys 15.9% to 56%, CodeMonkeys 66.2%, PoLL 7x cost, Anthropic's +90.2% and 15x tokens, the 5.5% tool-poisoning and 43.7% GPT-5 MCP figures.
- The exact CPU-to-cap formula (the docs say only "fewer CPUs").
- Whether the 2026-07-28 MCP revision is widely adopted by the SDKs and servers AWOS would use.
- All 2026 papers above are read at abstract level only; CAID, MACU and the test-time-compute gains are unreplicated and mostly use frontier cloud models, not 7 to 30B local ones.
- No Cognition, OpenAI or Anthropic 2026 posts on parallel coding agents were found through these APIs; the engineering-blog coverage gap remains.
