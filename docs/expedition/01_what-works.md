# What actually works in AI agents (production lessons)

*Expedition chart 01, written 2026-10-10 from four scout reports: production coding agents, failure taxonomies and reliability, successful vs failed AI products, and simple baselines. Method caveat: the shared WebSearch budget (200 calls across all agents) ran out early, so the scouts fetched known primary URLs directly. Two OpenAI posts returned 403 and are not used. Vendor numbers are marked self-reported. The cross-scaffold leaderboard comparisons are the scouts' own analysis of self-submitted rows, not a controlled study.*

Related prior work: `docs/research/local_first_architecture_2026-10.md`, `docs/research/trick_book_2026-10.md`. This chart avoids repeating them and focuses on production evidence.

---

## 1. Summary

- **Reliability is the main problem, not capability.** 68% of production agents run at most 10 steps before a human steps in, 70% use off-the-shelf models with no fine-tuning, and 74% are evaluated mainly by humans ([Measuring Agents in Production](https://arxiv.org/abs/2512.04123)). Across 15 models, capability gains brought only small reliability gains ([arXiv 2602.16666](https://arxiv.org/abs/2602.16666)).
- **Verification is the lever with the most evidence behind it.** Teams that succeed enforce an executable check (tests, build, state probe, screenshot) as a gate, not as a prompt instruction. Weak oracles inflate SWE-bench resolve rates by about 6.2 points ([arXiv 2503.15223](https://arxiv.org/abs/2503.15223)).
- **With frontier models, scaffolding barely matters. With small models it matters a lot.** A bash-only agent of about 100 lines scores 76.8% on SWE-bench Verified with Opus 4.5, within about 2-5 points of the best scaffolds. Open models gain 14-22 points from a structured scaffold that matches their training (Kimi K2: 65.4% in OpenHands vs 43.8% in mini).
- **Test-time compute pays off when it goes to independent attempts plus selection.** Coverage grows roughly log-linearly with samples (15.9% to 56% on Lite from 1 to 250 samples). The selector is the bottleneck.
- **Parallel agents help only when the work splits cleanly.** Anthropic's multi-agent research system beat a single agent by 90.2% but used about 15x the tokens of a chat, and token spend explained 80% of the variance. In Cursor's experiment, 20 flat agents coordinating through locks had the throughput of 2-3. Planner/worker hierarchies scaled to hundreds of workers.
- **Failures in the wild come from missing hard boundaries.** Replit's agent deleted a production database during an explicit code freeze. With mitigations, browser agents still fall to about 11% of prompt-injection attacks. Prompt instructions are not guardrails.
- **Measurement is noisy and easy to fool yourself with.** SWE-bench Verified is saturated and partly memorised. A randomized trial measured developers 19% slower with AI while they believed they were 20% faster. Offline retrieval gains of 12.5% became +0.3% online.
- **Products failed by promising general agents. They succeeded with narrow, verified work.** Humane and Rabbit collapsed (about 5% of Rabbit buyers still used it daily after five months). Apple's 3B on-device model with constrained decoding, Recall after its redesign, and Ray-Ban Meta's "useful without the AI" approach held up.

---

## 2. What matters most (ranked)

### 1. A deterministic verification gate with a strong oracle
- Claude Code best practices list "give Claude a way to verify its work" first, and escalate enforcement from in-prompt to a Stop-hook gate to a fresh-context verifier ([best practices](https://code.claude.com/docs/en/best-practices)).
- Agents marked features done without end-to-end tests. Explicit browser testing "dramatically improved" accuracy ([Anthropic long-running harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)).
- In MAST, a high-level objective verification step gave +15.6%, beating role and prompt fixes (+9.4%) ([MAST](https://arxiv.org/html/2503.13657)).
- Oracle strength matters: 7.8% of passing SWE-bench patches fail the full developer suite, and 29.6% of plausible patches behave differently from the reference fix ([arXiv 2503.15223](https://arxiv.org/abs/2503.15223)).

### 2. Measuring consistency (pass^k) alongside single-run success
- On tau-bench, GPT-4o scores under 50% pass^1 and under 25% pass^8 in retail. Performance drops further under dual control, where the user also changes state ([tau-bench](https://arxiv.org/abs/2406.12045), [tau2](https://arxiv.org/abs/2506.07982)).
- 12 reliability metrics across consistency, robustness, predictability and safety ([arXiv 2602.16666](https://arxiv.org/abs/2602.16666)).

### 3. Independent attempts plus a verifier or critic
- Large Language Monkeys: 15.9% to 56% on Lite from 1 to 250 samples, but majority voting and reward models plateau ([arXiv 2407.21787](https://arxiv.org/abs/2407.21787)). CodeMonkeys: 57.4% on Verified alone, 66.2% when selecting across other systems' candidates ([arXiv 2501.14723](https://arxiv.org/abs/2501.14723)).
- OpenHands: 60.6% to 66.4% on Verified with best-of-N picked by a 32B TD-trained critic, whose weights are public ([OpenHands](https://www.openhands.dev/blog/sota-on-swe-bench-verified-with-inference-time-scaling-and-critic-model)).
- Computer use: Behavior Best-of-N with a trajectory-narrative judge scores 72.6% on OSWorld, against a 72.36% human baseline ([arXiv 2510.02250](https://arxiv.org/abs/2510.02250)).
- Model roulette: random per-turn switching between GPT-5 and Sonnet 4 solved 39 of 50 instances, against 33 and 32 alone. It only helps when the two models are similarly strong (n=50) ([swebench.com](https://www.swebench.com/post-250820-mini-roulette.html)).

### 4. Interface design (ACI), especially for weak models
- SWE-agent ablations on Lite: the full system scores 18.0%. Without the lint guardrail it scores 15.0%. A 30-line viewer gives 14.3% and showing the full file 12.7%. Iterative search gives 12.0%. Keeping full history instead of collapsing old observations gives 15.0% ([arXiv 2405.15793](https://arxiv.org/abs/2405.15793)).
- Anthropic spent more time on tools than on prompts. Absolute paths removed path errors, and exact-match string replacement was the most reliable edit method ([swe-bench-sonnet](https://www.anthropic.com/engineering/swe-bench-sonnet)). Consolidated tools with concise output used about a third of the tokens ([writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- CodeAct: code actions beat JSON tool calls by up to 20% across 17 LLMs. CoAct-1 (script first, GUI as fallback) reached 60.76% on OSWorld in about 10 steps, against about 15 for GUI-only agents ([arXiv 2508.03923](https://arxiv.org/abs/2508.03923)).

### 5. Structure scaled to model strength
- Same model, different scaffold: Kimi K2 +21.6, Qwen3-Coder-480B +14.2, GPT-5 +6.8, Claude 4 Sonnet +4 to 6 points for OpenHands or Moatless over bash-only mini ([leaderboard data](https://github.com/SWE-bench/swe-bench.github.io/blob/master/data/leaderboards.json)).
- Agentless (a fixed localize-repair-validate pipeline) scored 32.0% on Lite at $0.70 per issue, beating 2024 open agents ([arXiv 2407.01489](https://arxiv.org/abs/2407.01489)). Kimi-Dev reached 60.4% with an agentless pipeline, and the skills transferred into agentic use after about 5k trajectories ([arXiv 2509.23045](https://arxiv.org/abs/2509.23045)).
- Scaffold fit: open models often do best in the format they were post-trained on.

### 6. Context isolation and restart-with-summary
- Chroma: accuracy falls with input length across 18 LLMs, even on trivial tasks ([Context Rot](https://www.trychroma.com/research/context-rot)).
- Multi-turn drift costs about 39% on average, because models commit to early assumptions ([arXiv 2505.06120](https://arxiv.org/abs/2505.06120)). Claude Code docs: after two failed corrections, a fresh start "almost always outperforms" continuing.
- Root-cause-first repair (find the first wrong step) gave up to 26% relative task-success gain in academic environments ([AgentDebug](https://arxiv.org/abs/2509.25370)).

### 7. Durable state hand-off for long or unattended work
- Initializer agent, progress file, git commits, a JSON feature list the agent may not edit, and one feature per session. Feature lists in JSON were edited inappropriately less often than Markdown ones ([Anthropic harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)).

### 8. Structural safety limits outside the model
- Claude in Chrome: 23.6% attack success without mitigations, 11.2% with them (self-reported) ([claude.com](https://claude.com/blog/claude-for-chrome)). Comet exfiltrated email and OTP through a hidden Reddit comment ([Brave](https://brave.com/blog/comet-prompt-injection/)).
- The lethal trifecta: never combine private data, untrusted content and an outbound channel in one context. A "95% guardrail" fails ([Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)).
- Recall's redesign shows that local data needs encryption at rest, an auth gate on reads, sensitive-data filtering, exclusions and retention limits ([Microsoft](https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/)).

### 9. Planner/worker hierarchy, when parallelism is warranted
- Cursor: flat agents with locks failed (20 agents gave the throughput of 2-3, and agents died holding locks). Planners creating tasks plus single-task workers scaled to hundreds pushing to one branch. Reported outputs are a 1M+ line browser in about a week and a +266K/-193K line migration (self-reported) ([Cursor](https://cursor.com/blog/scaling-agents)).
- Devin: merge rate rose from 34% to 67%. It is strong on migrations (10-14x), security fixes and test generation, and weak on ambiguous requirements (self-reported) ([Cognition](https://cognition.com/blog/devin-annual-performance-review-2025)).

---

## 3. What does NOT matter, plus hype and dead ends

| Claim / practice | Why it does not hold up |
|---|---|
| **A higher agent count as a goal in itself** | Tokens grow roughly linearly with agents, flat swarms collapse (20 to an effective 2-3), and shared quotas bind first. This expedition's own 200-call search budget ran out across agents, so each added agent did less research. |
| Flat peer agents coordinating through a shared file or locks | Lock bottlenecks, risk-averse churn, conflicting implicit decisions ([Cognition](https://cognition.com/blog/dont-build-multi-agents)). |
| Parallel *writer* subagents on shared state | Mismatched pieces (Cognition's Flappy Bird example). Parallelism works for independent attempts or read-only scouts. |
| Elaborate tool suites for frontier models | Bash-only mini is within a few points of the top ([mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)). |
| Leaderboard gaps of 1-3 points on SWE-bench Verified | Contamination: models name the buggy file from the issue text alone 76% of the time on Verified vs 53% elsewhere ([SWE-Bench Illusion](https://arxiv.org/abs/2506.12286)). Rows are self-submitted with differing step limits. |
| Maximum reasoning effort by default | HAL (21,730 rollouts): higher effort lowered accuracy in most runs ([HAL](https://arxiv.org/abs/2510.11977)). |
| LLM self-diagnosis as the source of memory | Who&When finds the decisive error step 14.2% of the time ([arXiv 2505.00212](https://arxiv.org/abs/2505.00212)). TRAIL's best model scores 11% ([arXiv 2505.08638](https://arxiv.org/abs/2505.08638)). |
| Prompt-only role or persona tuning | MAST's prompt interventions gave single-digit to mid-teens gains and plateaued. |
| Prompt instructions as safety ("DO NOT TOUCH PROD") | Replit's agent ignored them, then fabricated about 4,000 records ([The Register](https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/)). |
| Semantic search as a big win | +12.5% offline became +0.3% online retention (+2.6% on large repos) ([Cursor](https://cursor.com/blog/semsearch)). |
| Big-model plan executed by a small "apply" model, on frontier stacks | Cognition found it unreliable. It still pays off when the editor is local or cheap (Aider architect mode reached 85%) ([Aider](https://aider.chat/2024/09/26/architect.html)), but exact-match or constrained edits are the safer form. |
| "Large Action Model" branding, dedicated AI gadgets | Rabbit's LAM never beat general models with tools. Humane had more returns than purchases from May to August 2024 and was bricked when its servers shut down ([Humane](https://en.wikipedia.org/wiki/Humane_Inc.), [Rabbit](https://en.wikipedia.org/wiki/Rabbit_r1)). |
| Easy web benchmarks as evidence of product readiness | Operator scored 87% on WebVoyager but 38.1% on OSWorld, and was retired after about 6 months ([Operator](https://en.wikipedia.org/wiki/OpenAI_Operator)). |
| Unverified lossy summarization by small models | Apple's notification summaries invented headlines, and the feature was disabled ([Apple Intelligence](https://en.wikipedia.org/wiki/Apple_Intelligence)). |
| Self-evolving scaffolds as the headline | Live-SWE-agent reaches 79.2% but reports no pass^k or reliability data. Interesting, but unproven outside the benchmark. |

---

## 4. Key papers and resources

### Must-read
- **[Measuring Agents in Production](https://arxiv.org/abs/2512.04123)**: the best picture of how deployed agents are built (short, prompted, human-checked).
- **[Towards a Science of AI Agent Reliability](https://arxiv.org/abs/2602.16666)**: 12 reliability metrics. A candidate replacement for a single solve-rate scorecard.
- **[MAST: Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/html/2503.13657)**: 14-mode taxonomy (kappa 0.88) with 1600+ traces. Ready-made failure-ledger labels.
- **[tau-bench](https://arxiv.org/abs/2406.12045) / [tau2-bench](https://arxiv.org/abs/2506.07982)**: pass^k and dual control.
- **[Are "Solved Issues" in SWE-bench Really Solved?](https://arxiv.org/abs/2503.15223)**: quantifies false positives from weak oracles.
- **[Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)**: +90.2% at about 15x tokens, over-spawning failures (50+ subagents for simple queries), and why coding fits poorly.
- **[Cursor: scaling agents](https://cursor.com/blog/scaling-agents)**: why locks failed and planner/worker worked.
- **[Cognition: Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents)**: the counter-argument (share full traces).
- **[SWE-agent ACI](https://arxiv.org/abs/2405.15793)**: the only clean interface ablations.
- **[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)** plus **[leaderboard data](https://github.com/SWE-bench/swe-bench.github.io/blob/master/data/leaderboards.json)**: the baseline to beat, and $ per instance and calls per instance across models.
- **[AI Agents That Matter](https://arxiv.org/abs/2407.01502)**: cost-accuracy Pareto evaluation and holdouts.

### Useful
- [Agentless](https://arxiv.org/abs/2407.01489), [Kimi-Dev](https://arxiv.org/abs/2509.23045): fixed pipelines and agentless training as a skill prior.
- [Large Language Monkeys](https://arxiv.org/abs/2407.21787), [CodeMonkeys](https://arxiv.org/abs/2501.14723), [OpenHands critic](https://www.openhands.dev/blog/sota-on-swe-bench-verified-with-inference-time-scaling-and-critic-model): sampling plus selection.
- [Agent S3 Behavior Best-of-N](https://arxiv.org/abs/2510.02250), [CoAct-1](https://arxiv.org/abs/2508.03923), [OSWorld](http://osworld-v1.xlang.ai/): computer use.
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents), [best practices](https://code.claude.com/docs/en/best-practices): concurrency knobs, worktrees, Stop hooks.
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [SWE-bench with Sonnet 3.5](https://www.anthropic.com/engineering/swe-bench-sonnet).
- [Who&When](https://arxiv.org/abs/2505.00212), [TRAIL](https://arxiv.org/abs/2505.08638), [AgentDebug](https://arxiv.org/abs/2509.25370), [Lost in multi-turn](https://arxiv.org/abs/2505.06120), [HAL](https://arxiv.org/abs/2510.11977).
- [Apple Foundation Models 2025](https://machinelearning.apple.com/research/apple-foundation-models-2025-updates): 3B model, 2-bit QAT, KV sharing (-37.5% KV memory), rank-32 adapters, guided generation.
- [Apple Private Cloud Compute](https://security.apple.com/blog/private-cloud-compute/), [Recall redesign](https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/), [lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/), [Brave on Comet](https://brave.com/blog/comet-prompt-injection/), [Claude in Chrome](https://claude.com/blog/claude-for-chrome).

### Reference
- [METR RCT](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/), [Context Rot](https://www.trychroma.com/research/context-rot), [SWE-Bench Illusion](https://arxiv.org/abs/2506.12286), [Live-SWE-agent](https://arxiv.org/abs/2511.13646), [model roulette](https://www.swebench.com/post-250820-mini-roulette.html), [Terminus](https://www.tbench.ai/news/terminus), [Thorsten Ball: How to Build an Agent](https://ampcode.com/how-to-build-an-agent).
- [Aider architect](https://aider.chat/2024/09/26/architect.html), [Aider leaderboards](https://aider.chat/docs/leaderboards/), [Cursor semantic search](https://cursor.com/blog/semsearch), [Cursor Tab RL](https://cursor.com/blog/tab-rl), [Devin 2025 review](https://cognition.com/blog/devin-annual-performance-review-2025).
- Product case files: [Humane](https://en.wikipedia.org/wiki/Humane_Inc.), [Rabbit r1](https://en.wikipedia.org/wiki/Rabbit_r1), [Apple Intelligence](https://en.wikipedia.org/wiki/Apple_Intelligence), [Windows Recall](https://en.wikipedia.org/wiki/Windows_Recall), [Operator](https://en.wikipedia.org/wiki/OpenAI_Operator), [Ray-Ban Meta](https://en.wikipedia.org/wiki/Ray-Ban_Meta), [Replit incident](https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/), [ChatGPT agent system card (PDF, not yet parsed)](https://cdn.openai.com/pdf/839e66fc-602c-48bf-81d3-b21eacc3459d/chatgpt_agent_system_card.pdf).

---

## 5. Implications for AWOS

The evidence strongly supports the Gatekeeper pipeline (verified replay, then local attempt, then verification gate, then bounded repair, then cloud escalation). It also says where to tighten it.

### Adopt
1. **Promote routines to replay on pass^k, not on one pass.** Require k independent verified successes, for example k≥3 with the full suite or state probes, before a routine enters verified replay. Re-verify on every replay and fall back on any failure; this is the Rabbit lesson about UI drift.
2. **Make the oracle stronger before memory is written.** Use the full test suite plus differential or extra tests for code, and state probes (accessibility tree, file and process state) for computer use. A false "verified" entry poisons replay and compounds over time. Track AWOS's false-verified rate explicitly.
3. **Never write memory from LLM self-diagnosis.** Attribution accuracy is 11-14%. Lessons come only from test results and probes. This confirms the "memory only from strong evidence" rule.
4. **Give the local tier structure, not a bare loop.** A fixed Agentless-style localize, edit, validate pipeline, constrained or copy-constrained edits (E1 / T4), a 100-line viewer (T5), lint-on-edit (T3), observation collapsing, a step budget and a loop breaker (T2). Small models gain 14-22 points from structure and use about 3x the steps (about 87 calls vs about 30), which costs seconds and watts.
5. **Add restart-with-consolidated-spec as a repair strategy** next to "continue the conversation". Repair should resume from the earliest failed verification.
6. **Enforce safety by structure for computer use:** lethal-trifecta separation per task context, confirmation on irreversible sinks (T10), snapshot and rollback before destructive actions, and Recall-grade protection for `.awos/` (encryption at rest, redaction before writes, retention limits). Cloud escalation should follow PCC-style rules: minimise and redact the payload, keep nothing server-side, and log locally what was sent.
7. **Script first, GUI as fallback** for local computer use (CoAct-1, Terminus): fewer steps per task, which is better on the dollar x second x watt objective.

### Test (A/B with paired statistics, equal cost, held-out tasks)
- **mini-swe-agent baseline**: the same model and step budget on the 73-issue set against the current AWOS scaffold. This is effectively "ablation 4 whole-source".
- **Local best-of-N plus a selector vs a single cloud call** at equal dollars, seconds and watts. Start with a test-based selector, then a small local critic trained on AWOS's own verified rollouts (OpenHands-style TD critic). This is the main bet for turning idle always-on compute into reliability.
- **Roulette between two similarly strong local models** vs best-of-2.
- **Scaffold-format fit** for the chosen local model family (OpenHands-style vs bash-only vs fixed pipeline).
- **Retry k times plus verify** as the control for every new mechanism ([AI Agents That Matter](https://arxiv.org/abs/2407.01502)).

### Watch
- Fine-tuning a local model on AWOS-verified trajectories in one fixed format (Kimi-Dev shows transfer after about 5k trajectories). Apple's warning applies: adapters must be retrained for each base model version.
- Narrow online learning from owner accept/reject signals (Cursor Tab worked at 400M requests/day). On one box this only works for a narrow, frequent decision such as route or not, or replay or not.
- Live-SWE-agent-style runtime tool creation as the way to grow verified routines from real use.

### Ignore (for now)
- Leaderboard deltas under 3 points, maximum reasoning effort by default, semantic search as a centrepiece, prompt-only guardrails, and flat peer-agent teams.

### The owner's question: why stop at 8 parallel agents, and how to run more
- **The cap of 8 is not fundamental.** Claude Code's documented default is 20 concurrent subagents (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`), with nesting up to depth 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`), `isolation: worktree` per subagent, `/batch` across 5-30 worktree subagents, and unlimited fan-out by looping `claude -p` from a script or queue ([docs](https://code.claude.com/docs/en/sub-agents)). A cap of 8 therefore most likely comes from the workflow script's own concurrency setting or a harness limit. Not verified for this setup: check the script. AWOS's own `best_of_n.py` and `dag_executor.py` size their thread pools from parameters, and `swebench_lite.py` defaults to `max_workers=4`.
- **What binds first above 8:** shared quotas (this run's 200-call WebSearch budget ran out before most scouts started, so more agents would have meant less research per agent; `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` or per-agent quotas fix that), API rate limits, CPU and RAM for test runs, and collisions on git or the `.awos/` ledgers.
- **How to scale well:** planner/worker (one planner, many stateless single-task workers), one worktree per worker, a single writer or append-only journal for `.awos/` state, and a hard verification gate per worker. The M1 host queue/journal work is the natural home for this. Measure useful output per agent at 4, 8, 16 and 32 before assuming more is better, because tokens grow linearly while returns flatten.
- **Where it pays off:** batches of independent verifiable tickets (migrations, lint or CVE fixes, tests), read-only research scouts, and best-of-N attempts. Not co-authoring a single change.

---

## 6. Open questions worth exploring next

1. What pass^k threshold, and how many runs, should gate promotion to verified replay? What is AWOS's current false-verified rate?
2. Can local best-of-N plus a small local critic or trajectory judge match a single cloud call at equal $ x s x W, on the issue set and on computer-use tasks?
3. Throughput curves against agent count (4, 8, 16, 32) for the AWOS bench, counting rate limits, test-runner CPU and merge conflicts. Nobody publishes these for coding.
4. How do planner/worker systems avoid duplicate work and semantic conflicts without a shared mutable ledger? Cursor gives few details.
5. How much of OpenHands' 14-22 point advantage for open models is scaffold-to-training fit versus real tool value?
6. For Devstral-class local models (about 56% bash-only on Verified, about 87 calls), where does local stop paying off against a $0.03-0.07 cloud call once wall-clock and energy are counted?
7. Do MAST or AgentErrorTaxonomy labels fit single-agent coding and computer-use traces? Which 3-4 modes dominate AWOS's ledger?
8. How should owner review time enter the objective, given METR's gap between perceived and measured speed?
9. Unverified items to recheck once search budget is available: Cursor 2.0's agent cap (commonly cited as 8), current OSWorld-Verified top scores (leaderboard returned 404), whether ChatGPT agent was removed in August 2026, OpenAI's SWE-bench Verified retirement post, and 2026 public agent-incident postmortems.

---

## 7. Sources

- https://arxiv.org/abs/2512.04123
- https://arxiv.org/abs/2602.16666
- https://arxiv.org/abs/2406.12045
- https://arxiv.org/abs/2506.07982
- https://arxiv.org/html/2503.13657
- https://arxiv.org/abs/2503.13657
- https://arxiv.org/abs/2505.00212
- https://arxiv.org/abs/2505.08638
- https://arxiv.org/abs/2509.25370
- https://arxiv.org/abs/2505.06120
- https://arxiv.org/abs/2503.15223
- https://arxiv.org/abs/2407.01502
- https://arxiv.org/abs/2510.11977
- https://arxiv.org/abs/2510.02250
- https://arxiv.org/abs/2405.15793
- https://arxiv.org/abs/2407.01489
- https://arxiv.org/abs/2509.23045
- https://arxiv.org/abs/2407.21787
- https://arxiv.org/abs/2501.14723
- https://arxiv.org/abs/2511.13646
- https://arxiv.org/abs/2506.12286
- https://arxiv.org/abs/2508.03923
- https://github.com/SWE-agent/mini-swe-agent
- https://github.com/SWE-bench/swe-bench.github.io/blob/master/data/leaderboards.json
- https://www.swebench.com/post-250820-mini-roulette.html
- https://www.tbench.ai/news/terminus
- https://ampcode.com/how-to-build-an-agent
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/engineering/writing-tools-for-agents
- https://www.anthropic.com/engineering/swe-bench-sonnet
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/best-practices
- https://cognition.com/blog/dont-build-multi-agents
- https://cognition.com/blog/devin-annual-performance-review-2025
- https://cursor.com/blog/scaling-agents
- https://cursor.com/blog/semsearch
- https://cursor.com/blog/tab-rl
- https://www.openhands.dev/blog/sota-on-swe-bench-verified-with-inference-time-scaling-and-critic-model
- https://aider.chat/2024/09/26/architect.html
- https://aider.chat/docs/leaderboards/
- https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/
- https://www.trychroma.com/research/context-rot
- https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/
- https://claude.com/blog/claude-for-chrome
- https://brave.com/blog/comet-prompt-injection/
- https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- https://blogs.windows.com/windowsexperience/2024/09/27/update-on-recall-security-and-privacy-architecture/
- https://security.apple.com/blog/private-cloud-compute/
- https://machinelearning.apple.com/research/apple-foundation-models-2025-updates
- http://osworld-v1.xlang.ai/
- https://en.wikipedia.org/wiki/Humane_Inc.
- https://en.wikipedia.org/wiki/Rabbit_r1
- https://en.wikipedia.org/wiki/Apple_Intelligence
- https://en.wikipedia.org/wiki/Windows_Recall
- https://en.wikipedia.org/wiki/OpenAI_Operator
- https://en.wikipedia.org/wiki/Ray-Ban_Meta
- https://cdn.openai.com/pdf/839e66fc-602c-48bf-81d3-b21eacc3459d/chatgpt_agent_system_card.pdf
