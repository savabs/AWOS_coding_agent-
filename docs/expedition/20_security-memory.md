# Agent memory, security and trust

*Expedition chart, territory 20. Compiled 2026-10-10 from four scout reports covering memory systems, agent security, oversight UX and privacy. Method caveat: the session-wide WebSearch budget (200 calls shared by all agents) ran out early, so the scouts worked from direct fetches of primary sources they already knew. Work published in 2026 is under-covered. Most product numbers are vendor self-reports and are marked as such. The secret-sprawl count in §2 was re-checked locally while writing this chart.*

---

## 1. Summary

- **What you write into memory matters more than the memory architecture.** Strict, evaluator-gated memory writes beat "store every trajectory" by 15-25 points on four agents ([Xiong et al.](https://arxiv.org/abs/2505.16067)). Writing everything makes agents worse through experience-following and error propagation.
- **Staleness is unsolved.** In 2025 every tested system, including commercial memory layers and long-context models, scored at most about 28% on multi-hop fact consolidation, and Mem0 and Cognee scored about 2% ([MemoryAgentBench](https://arxiv.org/abs/2507.05257)).
- **Cheap memory beats clever memory.** A filesystem plus grep, or BM25, matches or beats the vendor layers. Frontier vendors have converged on client-side files ([Anthropic memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool), [Letta](https://www.letta.com/blog/benchmarking-ai-agent-memory)). Procedural (workflow) memory gives the largest agentic gains: +51% relative on WebArena ([AWM](https://arxiv.org/abs/2409.07429)).
- **Memory is an attack surface that works at very low dose.** Query-only injection reaches about 98% injection success ([MINJA](https://arxiv.org/abs/2503.03704)). Backdoor poisoning works at a poison rate below 0.1% with less than 1% benign impact ([AgentPoison](https://arxiv.org/abs/2407.12784)). ChatGPT and Gemini still let untrusted documents write memories ([Embrace The Red](https://embracethered.com/blog/posts/2025/gemini-memory-persistence-prompt-injection/)).
- **Only deterministic boundaries count.** Adaptive attacks broke 12 published prompt-injection defences, most at above 90% attack success ([Nasr, Carlini et al.](https://arxiv.org/abs/2510.09023)). What holds up: sandboxing, information-flow/capability control ([CaMeL](https://arxiv.org/abs/2503.18813), [FIDES](https://arxiv.org/abs/2505.23643)), argument-level privilege policies ([Progent](https://arxiv.org/abs/2504.11703)), and the Rule of Two / lethal trifecta ([Meta](https://ai.meta.com/blog/practical-ai-agent-security/)).
- **Oversight is a budgeting and containment problem.** Users approve 93% of per-action prompts ([Anthropic auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode)). Sandboxing cuts prompts by 84% ([Anthropic](https://www.anthropic.com/engineering/claude-code-sandboxing)). Human review capacity is finite and can be flooded ([arXiv 2606.08919](https://arxiv.org/abs/2606.08919)). Model explanations are not reliable audit evidence ([Anthropic](https://www.anthropic.com/research/reasoning-models-dont-say-think)).
- **Privacy is an engineering problem more than a regulatory one** for a single-owner box. EU AI Act Art. 2(10) exempts purely personal use ([tracker](https://artificialintelligenceact.eu/implementation-timeline/)). Models leak private context in 25-57% of cases even when instructed not to ([PrivacyLens](https://arxiv.org/abs/2409.00138)), so the controls have to live in code.
- **AWOS itself currently fails the lethal-trifecta test.** 527 files under `.awos/` contain API-key-format strings, including 503 per-run `.env` copies. They are not in git, but the agent under test can read them while it talks to cloud LLMs.

---

## 2. What matters most (ranked)

**1. Evidence-gated memory writes, with deletion driven by downstream utility.** Going from add-all to strict selective addition raised EHRAgent from 13.05% to 38.50% (with fewer than half the records), AgentDriver from 32.3% to 51.0%, CIC-IoT from 59.9% to 85.4% and RegAgent from 55.5% to 71.0%. History-based deletion then lifted CIC-IoT to 89.6%. The authors name three mechanisms: experience-following, error propagation, and "misaligned experience replay", meaning runs that looked correct but mislead later. Future task evaluations work as free quality labels for stored records. — [arXiv 2505.16067](https://arxiv.org/abs/2505.16067)

**2. Deterministic boundaries, not detectors.** Nasr, Carlini, Sitawarin et al. used gradient, RL, random-search and human attacks to break 12 defences (prompting, classifiers, adversarial training) whose papers had reported near-zero attack success. Detectors with "95% detection" are fine as telemetry, but they are not a gate. — [arXiv 2510.09023](https://arxiv.org/abs/2510.09023), [Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)

**3. Rule of Two / lethal trifecta as a routing rule.** Within one session an agent may combine at most two of: (A) untrusted input, (B) private data or sensitive systems, (C) changing state or communicating externally. For computer use on the owner's machine, B is always true, so each step must drop A or C, or go to a gate. — [Meta](https://ai.meta.com/blog/practical-ai-agent-security/)

**4. OS sandbox plus egress proxy as the floor.** Claude Code uses Seatbelt on macOS and bubblewrap on Linux, and the rule covers child processes. The open-source srt runtime adds deny-by-default network through a proxy and blocks cloud metadata IPs. It reports 84% fewer permission prompts (self-reported). Documented gaps: no DNS filtering, IP literals bypass checks, filesystem rules are fixed at launch. — [sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime), [Anthropic](https://www.anthropic.com/engineering/claude-code-sandboxing)

**5. Task-scoped privilege policies where narrowing is free and widening escalates.** Progent cut attack success on AgentDojo from 39.9% to 1.0%, and on ASB from 70.3% to 3.9%, with utility roughly kept. Only 6% of policy updates were expansions that needed approval. — [arXiv 2504.11703](https://arxiv.org/abs/2504.11703)

**6. Staleness needs explicit primitives.** Single-hop FactConsolidation: GPT-5-mini about 53%, MemGPT 15.5%. Multi-hop: best about 28%, Mem0 and Cognee about 2%. On retrieval, BM25 scored 60.5% and HippoRAG-v2 65.1%, against 53.8% for embedding RAG. These figures came through a summariser, so the exact table cells should be checked. Zep's bi-temporal validity intervals (valid_from / invalid_at) are the most concrete primitive available. — [MemoryAgentBench](https://arxiv.org/abs/2507.05257), [Zep](https://arxiv.org/html/2501.13956)

**7. Provenance and taint labels on memory and tool output.** MINJA reached about 98.2% injection success and about 76.8% attack success. Embedding sanitisation failed because malicious and benign records are entangled in embedding space. An LLM detector produced 34 false positives out of 50. AgentPoison: above 80% attack success at a poison rate below 0.1%. FIDES-style labels (owner / repo / web / MCP server) can drive both the sink policy and memory admission. — [MINJA](https://arxiv.org/abs/2503.03704), [AgentPoison](https://arxiv.org/abs/2407.12784), [FIDES](https://arxiv.org/abs/2505.23643)

**8. Procedural memory is where the value is.** Agent Workflow Memory: +51.1% relative on WebArena and +24.6% on Mind2Web, and the online variant gains 8.9-14.0 points as distribution shift grows. This maps onto verified-routine replay. Routines learned from unverified runs inherit the problem in item 1. — [arXiv 2409.07429](https://arxiv.org/abs/2409.07429)

**9. Secret hygiene and the supply chain.** The Aug 2025 nx "s1ngularity" malware ran `claude --dangerously-skip-permissions`, `gemini --yolo` and `q chat --trust-all-tools` to hunt for secrets ([Snyk](https://snyk.io/blog/weaponizing-ai-coding-agents-for-malware-in-the-nx-malicious-package/); the official advisory did not mention the AI-CLI angle). Open models hallucinate package names in 21.7% of samples, against 5.2% for commercial models ([USENIX Sec 2025](https://arxiv.org/abs/2406.10279)). GitGuardian: 70% of secrets leaked in 2022 were still valid ([report](https://blog.gitguardian.com/the-state-of-secrets-sprawl-2025/)). In AWOS: 527 files with key-format strings under `.awos/`, none git-tracked, but readable by the agent.

**10. Undo below the tool layer, plus a capped escalation budget.** Claude Code checkpoints do not track Bash side effects, subagent edits or linked files ([docs](https://code.claude.com/docs/en/checkpointing)). Only 0.8% of actions looked irreversible, and experienced users' auto-approve share rises from about 20% to more than 40% ([Anthropic](https://www.anthropic.com/research/measuring-agent-autonomy)). A simulation finds that escalating everything is less safe than escalating about 64% under a reviewer capacity of 25, and that 50 filler actions raise flooding attack success from 0% to 40% (modelled with LLM personas, single author, not peer reviewed) ([arXiv 2606.08919](https://arxiv.org/abs/2606.08919)).

**11. Local-to-cloud minimisation is both the privacy mechanism and a cost trick.** PAPILLON keeps quality on 85.5% of queries with 7.5% leakage. — [arXiv 2410.17127](https://arxiv.org/abs/2410.17127)

**12. Idle-time consolidation.** Sleep-time compute: about 5x less test-time compute at equal accuracy, and 2.5x lower cost per query when amortised. Gains scale with how predictable the queries are. — [arXiv 2504.13171](https://arxiv.org/abs/2504.13171)

---

## 3. What does NOT matter: hype and dead ends

| Thing | Why not |
|---|---|
| LoCoMo leaderboards (Mem0 / Zep / Letta, 66-75%) | Conversations are 16-26k tokens and fit in context. Full context (72.9%) beats Mem0 (66.9%) in Mem0's own paper. Ground truth is flawed, there are no knowledge-update tests, and every top score is a disputed self-report ([Zep rebuttal](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/), [Mem0](https://arxiv.org/abs/2504.19413)). |
| Picking a memory vendor or graph DB as the core | Weak on test-time learning (Mem0 21.2% vs about 48.6% for long context) and on conflict resolution. Adds a network dependency. Filesystem or BM25 matches it. |
| "Store everything, retrieval will sort it out" | Refuted: 15-25 points worse than gated writes. |
| Long context as a replacement for memory | Still at most about 28% on multi-hop consolidation, and the most expensive option per dollar, second and watt on local hardware. |
| A-MEM-style self-rewriting notes as a default | Every LLM rewrite is a new drift and poisoning channel. MemoryAgentBench did not show it solving forgetting. |
| Classifier or guardrail products ("95-99% blocked"), "ignore injected instructions" prompts | Broken by adaptive attacks. A 5% miss rate is an exfiltration rate. |
| Embedding or LLM "is this memory malicious?" filters as the poisoning defence | MINJA shows entanglement in embedding space and high false-positive rates. |
| Standalone Dual-LLM pattern | Superseded by CaMeL and FIDES. Without an enforcing interpreter, the separation is illusory ([Willison 2023](https://simonwillison.net/2023/Apr/25/dual-llm-pattern/)). |
| Full CaMeL on every step | Costs about 7 points on AgentDojo (77% vs 84%) and about 21 points for weak planners (prior AWOS notes). Many chores do not fit a fixed program. |
| Approving every action | 93% approval rate turns review into rubber-stamping, and floods are an attack vector. |
| Chain-of-thought as audit evidence | Hint disclosure 25% (Claude 3.7) and 39% (R1). Reward hacks were exploited more than 99% of the time and admitted less than 2%. |
| "Security by incompetence" | WASP: injections partially succeed in up to 86% of cases. That rate will rise as small models improve ([WASP](https://arxiv.org/abs/2504.18575)). |
| VMs for every coding task on the Mac | Seatbelt plus a proxy gives the same filesystem and network boundary at near-zero watt cost. Keep VMs for GUI computer-use tiers. |
| EU AI Act high-risk compliance now | Personal-use exemption applies, and the high-risk dates are 2027-28. |
| Homomorphic or fully private cloud inference | Orders of magnitude too slow for agent loops. Minimisation gets most of the benefit. |
| "Local" means private | Cisco found 1,139 exposed Ollama servers, all answering unauthenticated calls ([Cisco](https://blogs.cisco.com/security/detecting-exposed-llm-servers-shodan-case-study-on-ollama)). |

---

## 4. Key papers and resources

### Must-read
- **How Memory Management Impacts LLM Agents**: https://arxiv.org/abs/2505.16067. The empirical basis for a verified-only memory policy and utility-based deletion.
- **MemoryAgentBench**: https://arxiv.org/abs/2507.05257. An independent multi-system comparison. FactConsolidation is the best existing staleness test.
- **The Attacker Moves Second**: https://arxiv.org/abs/2510.09023. Read it before trusting any defence number, including AWOS's own.
- **MINJA**: https://arxiv.org/abs/2503.03704 and **AgentPoison**: https://arxiv.org/abs/2407.12784. Threat models for the routine and experience store.
- **Progent**: https://arxiv.org/abs/2504.11703. The closest match to the AWOS T10 sink policy, with a "narrowing is free" UX.
- **Meta Rule of Two**: https://ai.meta.com/blog/practical-ai-agent-security/ and **lethal trifecta**: https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/. A routing rule plus a catalogue of real incidents for regression tests.
- **Anthropic sandbox-runtime**: https://github.com/anthropic-experimental/sandbox-runtime. Can be used on the host today. Its limitations section doubles as a red-team checklist.
- **Claude Code auto mode**: https://www.anthropic.com/engineering/claude-code-auto-mode. Tiered approval and a reasoning-blind classifier (full pipeline 0.4% FPR / 17% FNR, self-reported).

### Useful
- CaMeL https://arxiv.org/abs/2503.18813, FIDES https://arxiv.org/abs/2505.23643, Design Patterns https://arxiv.org/abs/2506.08837. Capability, IFC and pattern menus.
- Agent Workflow Memory https://arxiv.org/abs/2409.07429. Procedural memory gains.
- LongMemEval https://arxiv.org/abs/2410.10813. Tests updates, temporal reasoning and abstention, and gives cheap indexing recipes (fact-augmented keys, time-aware query expansion).
- Zep / Graphiti https://arxiv.org/html/2501.13956. Bi-temporal validity. Extraction hurts recall of the assistant's own turns by 9-18%.
- Letta filesystem benchmark https://www.letta.com/blog/benchmarking-ai-agent-memory and Anthropic memory tool https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool. File memory plus a security checklist.
- Sleep-time compute https://arxiv.org/abs/2504.13171; Memory-R1 https://arxiv.org/abs/2508.19828 (an RL memory manager trained from 152 QA pairs, 3-14B).
- Meta-SecAlign https://arxiv.org/abs/2507.02735. Injection-robust fine-tuning tested on Qwen3-4B and Llama-8B.
- WASP https://arxiv.org/abs/2504.18575; AgentDojo https://github.com/ethz-spylab/agentdojo (not fetched this session).
- PrivacyLens https://arxiv.org/abs/2409.00138, AgentDAM https://arxiv.org/abs/2503.09780, PAPILLON https://arxiv.org/abs/2410.17127.
- Measuring agent autonomy https://www.anthropic.com/research/measuring-agent-autonomy; Reasoning models don't say what they think https://www.anthropic.com/research/reasoning-models-dont-say-think.
- Ctrl-Z https://arxiv.org/abs/2504.10374. Resampling suspicious actions cut attack success from 58% to 7% at a 5% usefulness cost.
- Claude Code checkpointing docs https://code.claude.com/docs/en/checkpointing; AgentFS https://github.com/tursodatabase/agentfs (beta copy-on-write filesystem with an audit trail).
- Package hallucinations https://arxiv.org/abs/2406.10279; nx malware analysis https://snyk.io/blog/weaponizing-ai-coding-agents-for-malware-in-the-nx-malicious-package/; MCP tool poisoning https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks.

### Reference
- Memory survey (Dec 2025) https://arxiv.org/abs/2512.13564; A-MemGuard https://arxiv.org/abs/2510.02373 (reports above 95% attack-success reduction, unreplicated); MEXTRA https://arxiv.org/abs/2502.13172; Mem0 https://arxiv.org/abs/2504.19413.
- Spotlighting https://arxiv.org/abs/2403.14720; MELON https://arxiv.org/abs/2502.05174; Dual LLM https://simonwillison.net/2023/Apr/25/dual-llm-pattern/.
- Oversight capacity https://arxiv.org/abs/2606.08919 plus https://github.com/turangenesis/headroom; Magentic-UI https://arxiv.org/abs/2507.22358; Vasconcelos et al. https://arxiv.org/abs/2212.06823; GoEX https://arxiv.org/abs/2404.06921; SagaLLM https://arxiv.org/abs/2503.11951; KnowNo https://arxiv.org/abs/2307.01928; Replit incident https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/.
- Windows agent workspace https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3; Apple PCC https://security.apple.com/blog/private-cloud-compute/; Recall redesign https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/.
- OTel GenAI conventions https://github.com/open-telemetry/semantic-conventions-genai; 1Password agentic autofill https://1password.com/blog/closing-the-credential-risk-gap-for-browser-use-ai-agents; Anthropic consumer terms https://www.anthropic.com/news/updates-to-our-consumer-terms; EU AI Act timeline https://artificialintelligenceact.eu/implementation-timeline/.

---

## 5. Implications for AWOS

Mapped onto the Gatekeeper pipeline: replay, then local attempt, then verification gate, then bounded repair, then cloud escalation, with memory written only from strong evidence.

### Adopt now (cheap, deterministic, evidence-backed)
1. **Fix secret sprawl first.** Stop copying the full `.env` (about 20 provider keys) into per-run state. Inject one scoped key per run, by reference, from outside the agent's readable filesystem (a host proxy or broker), and purge the 503 existing `.env` copies. Add a gitleaks-style scanner on agent diffs and outbound payloads, as part of the verification gate.
2. **Sandbox every job.** Use srt / Seatbelt on the Mac host: worktree-only writes, egress through a proxy allowlist (model APIs and the package registry only), no secrets in the sandbox, postinstall scripts off. Add DNS filtering and IP-literal bypass to the red-team list.
3. **Memory admission rule, written down.** A record or routine is written only if (a) the producing step passed the verification gate (tests, acceptance or state probe), and (b) its taint labels show no untrusted content (web, issue text, third-party tool output) influencing the step, unless the owner confirms. Store provenance (run id, probe hash, model, labels) on each record. This makes the existing rule a security control as well as a quality one.
4. **Re-verify before replay.** Run the routine's precondition probe before replaying it, and demote the routine after one failed probe. This is the deterministic version of A-MemGuard-style consensus checking, and it handles staleness and poisoning together.
5. **Staleness primitive.** Give every fact and routine `valid_from` / `invalid_at` plus a precondition fingerprint (app version, file hash, UI signature). Supersede explicitly instead of letting an LLM judge UPDATE/DELETE.
6. **Plain-file memory with BM25.** Keep memory as git-versioned files that the owner can audit and diff. No vendor memory layer.
7. **Rule-of-Two tagging in the router.** Tag each step A/B/C. Steps with all three (always the case for computer use that sends anything out) go to the sink policy, staging or the owner.
8. **Progent-style scope UX on the T10 sink policy.** A local model may narrow its scope, but only the gate or the owner may widen it.
9. **Dependency gate.** A new package must exist, be older than N days, have enough downloads and be lockfile-pinned. Otherwise escalate. Local models hallucinate packages 4x more often than commercial ones.
10. **Local model server bound to loopback or a unix socket, with a token required.**
11. **Telemetry holds metadata only** (tokens, dollars, Wh, tier, verdict). Move model-turn content out of git-tracked `.awos/` cassettes into a local, encrypted, retention-limited store.
12. **Escalation path stays private.** Cloud escalation goes only to API or zero-data-retention endpoints, never consumer tiers, and passes through a local PAPILLON-style minimiser. Record each provider's retention class as a cost term next to dollars, seconds and watts.

### Test (A/B on AWOS's own sets)
- **Utility cost of security on the 73-issue set:** srt plus a Progent-style policy against no policy, measured for local 4-9B workers. No one has measured this.
- **Add-all vs gated vs gated-plus-utility-deletion** memory on longitudinal replay. This is the Xiong et al. result rerun on AWOS's own tasks.
- **Small-model filesystem memory:** do 3-14B models use grep loops as well as GPT-4o-class models? Letta's result has not been replicated at that size.
- **AWOS memory red team:** inject stale facts and MINJA/AgentPoison-style poisoned records, and include the case where poisoned content passes weak tests.
- **Meta-SecAlign LoRA** on the local worker. It must hold against an adaptive attacker in AWOS's own harness, not only static benchmarks.
- **Pseudonymisation before escalation:** what it costs in task success.
- **Memory-R1-style curator** trained with verification outcomes as reward (training is in scope).

### Build for computer use (next)
- **Undo below the tool layer:** an APFS clone, AgentFS or worktree per job. Every external sink declares whether it is reversible and what its compensating action is. Irreversible sinks (send, push, pay, credential use) are staged, never auto-executed.
- **Credentials by reference** through a broker, 1Password-autofill style. The model only ever sees a handle.
- **An owner escalation budget per day,** delivered as a batched digest or PR-style handoff. Approval cards are built from facts (diff, commands, destinations, probe results), never from model rationale. Before spending human attention, spend dollars: resample, or a cloud reasoning-blind judge.
- **Seeded-injection tests (WASP-style) in the promotion gate** for any computer-use routine.
- **An append-only, hash-chained journal** exportable as OTel GenAI spans. It is the audit record and the evidence source for memory admission.
- **Idle-time curation** on the always-on host: re-verify, dedupe, expire and supersede during idle cycles (sleep-time compute).

### Watch
- 2026 independent replications of memory-vendor claims and of A-MemGuard.
- Conformal calibration of the gate's signal log (KnowNo) to get a guaranteed false-autonomy rate per action class.
- The EU Digital Omnibus (adoption unverified) and the AI Act Art. 50 transparency duties if AWOS ships models or ships commercially.

### Ignore
LoCoMo leaderboards, vendor memory layers, detector-only defences, full CaMeL everywhere, per-action approval, chain-of-thought as audit evidence, and VM-per-coding-task.

### Note on the parallelism question
Three of the four scouts lost web search because a 200-call cap was shared across the whole session. Running more than 8 agents yields thinner research unless each agent gets its own tool quota (or the session cap is raised) and narrower briefs. The same principle applies to AWOS as a product: owner attention is a fixed budget. Adding more parallel agents only pays off when review is asynchronous and batched and each agent's blast radius is contained to its own workspace.

---

## 6. Open questions worth exploring next

1. Which staleness primitive works best for computer-use state (validity intervals, precondition fingerprints, or re-probing every time), and at what cost in watts and latency? None has been benchmarked head to head.
2. How resistant is verification-gated memory when the attacker can craft content that passes weak probes? Test strength becomes a security parameter.
3. Can one set of taint labels serve as both the security boundary and the memory-admission filter, without busting the stable prompt prefix during bounded repair?
4. What is the real reviewer-capacity curve for one owner? It can be measured cheaply from approval latency and later-reverted approvals.
5. Can a reasoning-blind judge built on a local small model reach acceptable FNR, or does the residue need a cloud model?
6. Can an irreversibility classifier for macOS app actions be deterministic?
7. Which external sinks (email, calendar, GitHub, payments) have compensating actions, and which must stay staged permanently?
8. Is a host DNS resolver with an allowlist needed to close srt's DNS-exfiltration gap?
9. What deletion policy suits a single owner (utility-based, expiry, or both), and what does memory bloat cost in retrieval latency and watts?
10. Is there 2026 independent replication of anything in §2? Not searched, because the search budget was exhausted.

---

## 7. Sources

- https://arxiv.org/abs/2505.16067
- https://arxiv.org/abs/2507.05257
- https://arxiv.org/abs/2503.03704
- https://embracethered.com/blog/posts/2025/gemini-memory-persistence-prompt-injection/
- https://embracethered.com/blog/posts/2024/chatgpt-macos-app-persistent-data-exfiltration/
- https://arxiv.org/abs/2407.12784
- https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- https://www.letta.com/blog/benchmarking-ai-agent-memory
- https://arxiv.org/html/2501.13956
- https://arxiv.org/abs/2410.10813
- https://arxiv.org/abs/2409.07429
- https://arxiv.org/abs/2508.19828
- https://arxiv.org/abs/2504.13171
- https://arxiv.org/abs/2510.02373
- https://arxiv.org/abs/2502.13172
- https://arxiv.org/abs/2504.19413
- https://arxiv.org/abs/2512.13564
- https://arxiv.org/abs/2510.09023
- https://ai.meta.com/blog/practical-ai-agent-security/
- https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- https://simonwillison.net/2023/Apr/25/dual-llm-pattern/
- https://arxiv.org/abs/2503.18813
- https://arxiv.org/abs/2505.23643
- https://arxiv.org/abs/2504.11703
- https://arxiv.org/abs/2506.08837
- https://github.com/anthropic-experimental/sandbox-runtime
- https://www.anthropic.com/engineering/claude-code-sandboxing
- https://snyk.io/blog/weaponizing-ai-coding-agents-for-malware-in-the-nx-malicious-package/
- https://arxiv.org/abs/2406.10279
- https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks
- https://arxiv.org/abs/2507.02735
- https://arxiv.org/abs/2504.18575
- https://github.com/ethz-spylab/agentdojo
- https://arxiv.org/abs/2403.14720
- https://arxiv.org/abs/2502.05174
- https://www.anthropic.com/engineering/claude-code-auto-mode
- https://arxiv.org/abs/2606.08919
- https://github.com/turangenesis/headroom
- https://www.anthropic.com/research/measuring-agent-autonomy
- https://code.claude.com/docs/en/checkpointing
- https://www.anthropic.com/research/reasoning-models-dont-say-think
- https://arxiv.org/abs/2212.06823
- https://arxiv.org/abs/2507.22358
- https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/
- https://arxiv.org/abs/2504.10374
- https://support.microsoft.com/en-us/windows/experimental-agentic-features-a25ede8a-e4c2-4841-85a8-44839191dfb3
- https://arxiv.org/abs/2503.11951
- https://arxiv.org/abs/2307.01928
- https://arxiv.org/abs/2404.06921
- https://github.com/tursodatabase/agentfs
- https://github.com/open-telemetry/semantic-conventions-genai
- https://arxiv.org/abs/2409.00138
- https://arxiv.org/abs/2503.09780
- https://arxiv.org/abs/2410.17127
- https://1password.com/blog/closing-the-credential-risk-gap-for-browser-use-ai-agents
- https://blogs.cisco.com/security/detecting-exposed-llm-servers-shodan-case-study-on-ollama
- https://www.anthropic.com/news/updates-to-our-consumer-terms
- https://artificialintelligenceact.eu/implementation-timeline/
- https://security.apple.com/blog/private-cloud-compute/
- https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- https://blog.gitguardian.com/the-state-of-secrets-sprawl-2025/
- Local audit: `.awos/` in this worktree (527 files with key-format strings, 503 `.env` copies, 0 git-tracked; checked 2026-10-10)
