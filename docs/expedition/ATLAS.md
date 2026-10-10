# AWOS Expedition Atlas (2026-10)

*Synthesis of the 20 territory charts and 8 gap charts in `docs/expedition/`, written 2026-10-10. It summarises the charts and does not replace them; every claim links back to the chart it came from. **Method caveat that applies to everything below:** every scout ran out of the shared 200-call WebSearch budget early, so the evidence comes from primary sources the scouts already knew how to find. Most numbers are self-reported by authors or vendors, many were read from abstracts only, and 2026 work is under-covered. Treat the figures as priors to test on AWOS's own tasks, not as facts.*

---

## 1. The map in one page

1. **The verification gate is the product, and its false-accept rate is the most important number AWOS has not measured.** Every territory reaches the same conclusion. External, executable checks beat self-critique ([01](01_what-works.md), [02](02_architectures.md)). Router and gate accuracy drive energy and cost savings almost linearly: an oracle router saves 80% of energy and an 80%-accurate one saves 64% ([06](06_local-computing.md), [16](16_efficient-intelligence.md)). Cascades pay off only when judge error is at most about 10% ([12](12_rl.md)). The gate is also the reward signal for any later learning ([13](13_dl-learning.md)).
2. **Agents game graders, so the grader has to be isolated from them.** Berkeley RDI scored about 100% on SWE-bench Verified, Terminal-Bench and WebArena without solving any tasks. Frontier agents cheat in about 50% of cases when the spec and the tests conflict, and an "abort/flag contradiction" exit cut GPT-5's cheat rate from 54% to 9% ([03](03_evals.md), [12](12_rl.md)). The fixes are read-only tests, grading in a separate container, and stripping git history from sandboxes.
3. **Public scores overstate small-model agency by 2-3x.** Qwen3.6-27B claims 77% on SWE-bench Verified and scores 31% pass@1 on fresh SWE-rebench tasks. On SWE-Bench Pro, the best model scores 43% on public repos and 18% on private ones. Infrastructure noise alone moves scores by about 6 pp ([07](07_local-models.md), [03](03_evals.md)). The owner's own tasks are the only benchmark that reflects the owner's work.
4. **AWOS's own configuration makes local inference serial.** `AWOS_LOCAL_SLOTS=1` runs one sequence at a time, `dag_executor.py` hard-codes `MAX_WORKERS = 4`, and `mlx_lm.server` turns batching off when `--kv-bits` is set. Batched decode gives 11-18x aggregate throughput, and KV-cache RAM sets the ceiling ([08](08_low-level-opt.md), [15](15_hw-sw.md), [18](18_inference.md)). The "8 agents" cap in Claude Code is a workflow-harness default (`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS`), and in this expedition the shared search quota ran out before that cap mattered ([11](11_multi-agent.md), [17](17_frameworks.md)).
5. **Agent loops spend most of their compute reading context, so prefix reuse is the main lever.** Inputs are 13-20x larger than outputs. The M5 speeds up prefill 3.3-4.1x over the M4 but decode only about 1.2x. Prefix caching gives 6-32x ([06](06_local-computing.md), [18](18_inference.md)). One trap needs measuring now: hybrid models such as Qwen3.5-9B can only reuse a prefix at checkpoints, which by default fall every 8,192 tokens. That may quietly cancel the T7b stable-prefix gains ([18](18_inference.md)).
6. **Small models earn their place through pass@k plus a strong selector, not pass@1.** The best small dense model gains about 26 points from pass@1 to pass@5, and hybrid verifiers raise a 32B model from 34% to 51%. But 76% of small-model failures are wrong logic, so better edit formats fix only a small share ([07](07_local-models.md), [09](09_sweet-spot.md)).
7. **Agents that talk to each other hurt on sequential work; independent attempts plus a selector help.** The controlled range runs from +81% on decomposable tasks to -70% on sequential planning, with 2-15% losses on SWE-bench. Returns turn flat or negative once a single agent passes about 45%, and errors amplify 17x without a central verifier versus 4.4x with one ([11](11_multi-agent.md), [02](02_architectures.md)).
8. **Verified, executable procedural memory gives the biggest memory gains, but AWOS's ledger cannot feed it today.** Reported gains are ASI +23.5 points, AWM +51% relative, and 15-217x cost cuts from compiling desktop policies ([02](02_architectures.md), [04](04_computer-use.md)). The ledger stores no diffs, action traces or pre-state hashes, so 0% of it can be compiled. A perfect cache would serve at most 13% of real-issue successes, and all of those are benchmark reruns ([gap 03](gaps/03_program-synthesis-routine-compilation.md)).
9. **Memory needs strict writes more than a clever store.** Evaluator-gated writes beat "store everything" by 15-25 points. Poisoning works at under 0.1% of records, every tested system scores at most 28% on multi-hop staleness, and plain files with BM25 match vendor memory layers ([20](20_security-memory.md), [02](02_architectures.md)).
10. **Only deterministic boundaries hold.** Adaptive attacks broke 12 published defences at over 90% attack success. Task-scoped privilege policies cut attack success from 40% to 1% (Progent), and task-scoped permissions cut confirmation prompts by 43-89% (MiniScope) ([20](20_security-memory.md), [gap 06](gaps/06_capability-os-security.md)). AWOS currently fails the lethal-trifecta test: 527 files under `.awos/` contain key-format strings that the agent can read ([20](20_security-memory.md)).
11. **The watt term should be joules per verified task, and output tokens drive it.** Turning reasoning off saves 30-700x, batching saves 3-5x, and a sparse MoE saves about 3.5x. A new GPU generation gives about 35%. Local hardware loses to the cloud per joule by 1.4-3x on identical models, so local wins energy only through replay, batching and routing ([16](16_efficient-intelligence.md), [10](10_bio-intelligence.md)).
12. **A local box pays for itself only by avoiding frontier escalations.** A ~$2.2k Mac mini breaks even at about 21 tasks/day that would otherwise go to a Sonnet-class model, against about 520/day versus Flash-class models. Cloud prices at fixed capability fall about 10x a year, while frontier cost per call rises 3-18x a year ([gap 08](gaps/08_economics-market-adoption.md), [19](19_five-layers.md)). `providers.py` prices local calls at $0, which misstates the objective.
13. **For computer use, structured actions come first and pixels last.** Code or API actions beat GUI-only agents (CoAct-1 needs about 10 steps against 15), models call available tools only 36% of the time, open small models score under 5% on macOSWorld, and a Mac can run at most 2 macOS VMs ([04](04_computer-use.md), [05](05_aios.md)).
14. **Facts belong in memory and skills in weights; distill before RL.** RLVR mostly turns pass@k into pass@1. Distillation adds capability at about a tenth of RL's compute, and LoRA does not prevent forgetting (NaturalQuestions F1 drops 71% with LoRA against 11% with sparse fine-tuning) ([12](12_rl.md), [13](13_dl-learning.md), [16](16_efficient-intelligence.md)). The asset to build now is the gate-labelled ledger of the local model's own trajectories.
15. **Owner attention is a cost the objective leaves out.** Users approve 93% of permission prompts. Developers believed they were 20% faster while measuring 19% slower. The Gatekeeper is formally a Simplex / run-time assurance design, in which the monitor must be held to a higher standard than the controller it guards ([gap 01](gaps/01_automation-human-factors.md), [gap 02](gaps/02_safety-critical-runtime-assurance.md)).

---

## 2. Cross-territory insights

**Energy × local models × verification: the gate beats the hardware.** Charts [06](06_local-computing.md), [10](10_bio-intelligence.md), [15](15_hw-sw.md) and [16](16_efficient-intelligence.md) agree on the physics. Decode is limited by memory bandwidth, the brain spends 35x more energy on communication than on computation, and moving data dominates cost. The design rule that follows is the same in all four: keep the default path sparse (replay, then a small resident model) and escalate on surprise. Where the charts disagree is on what that buys. "Local is greener" is false per joule and true only per watt. The energy win comes from avoiding work (replay, reasoning off, accurate routing) and from batching, not from local silicon. [Gap 03](gaps/03_program-synthesis-routine-compilation.md) caps the replay win: a routine runs in about 30 ms, but the test gate takes 0.5-2 s, so replay is about 5-20x cheaper than a local attempt, not 1000x.

**Parallelism helps throughput and hurts reliability.** Batching more agents on one server lowers joules per token ([16](16_efficient-intelligence.md)) and raises throughput ([08](08_low-level-opt.md)). But errors are correlated: about 60% of wrong answers are identical across models ([16](16_efficient-intelligence.md)). Voting accuracy can rise and then fall ([11](11_multi-agent.md)), and fan-out without a verifier multiplies errors ([02](02_architectures.md)). The reconciliation is to run parallel *samples* of one task as a batch and let an *executable* check select. Chart [13](13_dl-learning.md) dissents, arguing that extra samples are the weakest use of compute compared with verifier-guided sequential refinement. Chart [14](14_decision-math.md) settles the choice: use adaptive stopping from a per-class posterior, never a fixed N. Whether sampling pays at all depends on one number, the local model's pass@k minus pass@1 gap on owner tasks ([12](12_rl.md)), and nobody has measured it.

**The coding testbed cannot demonstrate the main lever.** Charts [02](02_architectures.md), [04](04_computer-use.md) and [20](20_security-memory.md) treat procedural replay as the biggest win. [Gap 03](gaps/03_program-synthesis-routine-compilation.md) finds about 0% genuine recurrence in one-off bug fixes. [Gap 04](gaps/04_task-mining-demonstration.md) points out that recurrence lives in desktop admin work such as file hygiene, copying data between apps and scheduling, and that nobody has measured it for a single owner. So AWOS's headline mechanism is untested by its own benchmark. Owner logs, or the R40 routine set, are the right test.

**Local closes the capability gap while losing the dollar race.** Open models trail the frontier by 6-12 months ([19](19_five-layers.md)), but at fixed capability the cloud gets about 10x cheaper each year while local hardware got more expensive with 2026 DRAM prices ([gap 08](gaps/08_economics-market-adoption.md)). The durable case for local is therefore escalation avoidance, privacy, unattended operation and compounding owner-specific assets, not price per token. A fast cloud tier also shrinks local's latency edge: Cerebras and Groq serve gpt-oss-120b at 500-3,000 tok/s for $0.15/$0.60 per million tokens ([15](15_hw-sw.md)).

**Platforms supply the plumbing; nobody supplies verification.** Apple, Google and Microsoft all ship local-then-cloud routing, typed tool registries and constrained decoding. All of them escalate on *availability*, timeout or context overflow, never on verification evidence ([05](05_aios.md), [09](09_sweet-spot.md), [15](15_hw-sw.md), [19](19_five-layers.md)). Their permission models grant rights per agent or per host app, never per task ([gap 06](gaps/06_capability-os-security.md)). Verification-gated escalation, verified replay and per-task authority are the unoccupied position.

**One evidence structure can serve safety, memory and learning.** Taint and provenance labels can drive both the sink policy and memory admission ([20](20_security-memory.md)). A routine's ODD ([gap 02](gaps/02_safety-critical-runtime-assurance.md)), its capability manifest and recorded OS footprint ([gap 06](gaps/06_capability-os-security.md)), its reversibility class ([gap 05](gaps/05_transactional-os-undo.md)) and its pass^k record ([01](01_what-works.md)) are all facets of one record: a safety case whose evidence nodes are gate results. The same gate-labelled trajectories feed distillation and RL ([12](12_rl.md), [13](13_dl-learning.md)). This makes gate hardening a prerequisite for everything else: a gamed gate poisons memory, replay and training together.

**Hardware: the current host blocks the plan and biases the data.** The 16 GB M5 cannot hold Qwen3.6-35B-A3B at Q4, which needs about 20 GB ([07](07_local-models.md)). It was benchmarked on battery in Low Power Mode, which biases seconds and watts ([15](15_hw-sw.md)). The live batching run swapped 7.6 GB ([18](18_inference.md)). Several charts recommend buying for GB and GB/s, not TOPS, but only after a concurrency sweep.

---

## 3. What matters most for AWOS: now / next / later

### Now (next 2-4 weeks; cheap, mostly configuration and measurement)
1. **Measure and harden the gate.** Plant ImpossibleBench-style canaries and hidden tests in the 73-issue set and report the false-accept rate. Make tests read-only, reject diffs that touch test, `conftest.py` or oracle paths, grade in a process the agent cannot write to, strip future git refs, and add a `flag_contradiction` exit ([03](03_evals.md), [12](12_rl.md)).
2. **Make E1 and later A/Bs trustworthy.** Randomise arm order per issue, pin one provider per issue for both arms, run an A/A noise-floor test, run gold-pass, null-fail and flake checks on all 73 issues, treat infra errors as their own outcome class, and log power and thermal state with the host on AC power ([03](03_evals.md), [15](15_hw-sw.md)).
3. **Record traces.** For every verified success, store the diff, the tool-call sequence, the pre-state fingerprint, the gate command, the model ID and the checkpoint hash, including failed local samples. Replay, the routine compiler, distillation and RL all depend on this, and it takes about a day ([gap 03](gaps/03_program-synthesis-routine-compilation.md), [13](13_dl-learning.md)).
4. **Fix local serving.** Set `AWOS_LOCAL_SLOTS` to 4-8 with `--kv-unified`, `--cache-ram` and q8_0 KV, use no `--kv-bits` on MLX, and make the worker caps env-configurable. Run `llama-batched-bench` on an idle host, and check `timings.cache_n` for hybrid-checkpoint alignment ([08](08_low-level-opt.md), [18](18_inference.md)).
5. **Instrument joules and honest cost.** Record whole-system Wh per span (ioreg counters or Zeus, calibrated against a wall meter), take cloud cost from provider-reported `usage.cost`, and price local calls at measured Wh plus a latency term instead of $0 ([15](15_hw-sw.md), [16](16_efficient-intelligence.md), [gap 08](gaps/08_economics-market-adoption.md)).
6. **Fix secret sprawl and sandbox every job.** Stop copying `.env` into per-run state, broker one scoped key per run, run jobs under srt/Seatbelt with an egress allowlist, and bind the model server to loopback ([20](20_security-memory.md)).
7. **Turn local reasoning off by default and constrain every local call with a grammar** ([09](09_sweet-spot.md), [16](16_efficient-intelligence.md)).
8. **Measure the local model's pass@1 against pass@5.** This one number decides whether best-of-N, RL or distillation are worth anything ([12](12_rl.md)).

### Next (1-3 months; build on measured numbers)
- **Local best-of-N with a hybrid gate against one cloud call at equal $·s·Wh**, using adaptive stopping, a conformal false-accept bound per (model, task class), Thompson sampling per class, and c/p tier ordering ([14](14_decision-math.md), [11](11_multi-agent.md)). The yardstick is k cost-matched retries with the same gate ([02](02_architectures.md)).
- **Write down the memory admission rule:** gate pass plus no untrusted taint, provenance on every record, `valid_from`/`invalid_at` plus precondition fingerprints, re-verification before replay, and deletion driven by later utility ([20](20_security-memory.md)). Promote routines on pass^k (k=3-5) over held-out parameters ([01](01_what-works.md)).
- **Computer-use foundations:**
  - a structured-first action ladder enforced by the router ([04](04_computer-use.md));
  - an owner-screen grounding eval and a measurement of what share of chores structured actions alone can do (E9);
  - R0-R5 reversibility classes with an outbox and a commit protocol ([gap 05](gaps/05_transactional-os-undo.md));
  - per-task capability manifests checked against the OS event footprint ([gap 06](gaps/06_capability-os-security.md));
  - an opt-in accessibility-tree capture pilot to *measure owner recurrence* ([gap 04](gaps/04_task-mining-demonstration.md)).
- **Owner-attention metrics** (owner-minutes per verified task, ask precision, missed-ask rate) and an ask/act/notify policy keyed on reversibility ([gap 01](gaps/01_automation-human-factors.md)). Log escalations DMV-style, by initiator, cause and exposure ([gap 02](gaps/02_safety-critical-runtime-assurance.md)).
- **A hardware decision**, made from the concurrency sweep: a 48-64 GB Mac for 35B-A3B plus several slots ([07](07_local-models.md)).
- **Reflective prompt and skill optimisation (GEPA-style) behind a frozen regression slice**, since without regression control the gains do not compound ([17](17_frameworks.md)).

### Later (3-12 months; once the volume and gate quality justify it)
- After 1-5k verified trajectories: all-layer LoRA SFT/RFT, then on-policy distillation from a same-family teacher, then light GRPO with the gate as reward. Use rented GPUs and serve the adapter locally, with forgetting checks (KL to base), random-reward and non-Qwen control arms, and inoculation prompts ([07](07_local-models.md), [12](12_rl.md), [13](13_dl-learning.md)).
- A per-owner computer-use specialist trained on a FaraGen-style flywheel at about $1 per verified trajectory, filtered by T10 postconditions ([04](04_computer-use.md)).
- Hidden-state probes (semantic-entropy probes) as a local-only escalation signal, a small learned critic or a Weaver-style fused scorer, and a learned router once the ledger holds thousands of labels ([10](10_bio-intelligence.md), [02](02_architectures.md), [19](19_five-layers.md)).
- A voice I/O shell (Parakeet on the Neural Engine, Silero VAD, Kokoro TTS) around the router, plus a proactive gate based on expected utility ([gap 07](gaps/07_voice-ambient-interface.md)).
- A GSN/UL 4600-style safety case per routine, with runtime indicators that suspend a routine automatically ([gap 02](gaps/02_safety-critical-runtime-assurance.md)).

---

## 4. What to ignore (the biggest hype traps)

1. **Vendor SWE-bench Verified and OSWorld numbers, and leaderboard gaps under 3 pp.** They are contaminated, exploitable and within infra noise ([03](03_evals.md), [07](07_local-models.md)).
2. **Agent count as a goal.** That covers role-play companies, same-model debate, flat swarms and Mixture-of-Agents generation. MAST finds 41-87% failure rates ([11](11_multi-agent.md), [17](17_frameworks.md)).
3. **LLM self-critique or self-diagnosis as a memory or reward source.** Who&When locates the decisive error step 14% of the time, and verbalized confidence predicts failure at AUROC 0.52-0.61 ([01](01_what-works.md), [14](14_decision-math.md)).
4. **NPU TOPS, Neural-Engine LLMs and device sharding for concurrency.** Decode is bandwidth-bound ([06](06_local-computing.md), [15](15_hw-sw.md)).
5. **"Local is greener" and "quantize to save energy".** Both depend on the measurement boundary; measure instead ([16](16_efficient-intelligence.md)).
6. **Vendor memory layers, LoCoMo leaderboards and "store everything".** Full context beats Mem0 in Mem0's own paper ([20](20_security-memory.md)).
7. **Prompt or classifier injection defences used as boundaries** ([04](04_computer-use.md), [20](20_security-memory.md)).
8. **Maximum reasoning effort by default.** It lowered accuracy in most of HAL's 21,730 runs ([01](01_what-works.md)).
9. **Learned or commercial routers before AWOS has its own labels.** kNN matches them ([09](09_sweet-spot.md), [18](18_inference.md)).
10. **Small-model RL headline gains on Qwen math.** Random rewards reproduce most of them ([12](12_rl.md)).
11. **Wetware, spiking LLMs and neuromorphic multipliers** ([10](10_bio-intelligence.md)).
12. **The AIOS 2.1x speedup and "five-layer cake" stack diagrams as architecture guidance** ([05](05_aios.md), [19](19_five-layers.md)).
13. **Adopting an orchestration framework as the kernel.** Copy the durable-execution pattern instead ([17](17_frameworks.md)).

---

## 5. Top 25 resources and a reading path

1. **Measuring Agents in Production**: https://arxiv.org/abs/2512.04123. How deployed agents are actually built: short loops, prompted, checked by humans.
2. **Towards a Science of AI Agent Reliability**: https://arxiv.org/abs/2602.16666. Twelve reliability metrics, and the finding that reliability lags capability.
3. **tau-bench**: https://arxiv.org/abs/2406.12045. pass^k, the promotion metric for routines.
4. **Berkeley RDI, trustworthy benchmarks**: https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/. Exploit recipes to use as a red-team list for the AWOS gate.
5. **ImpossibleBench**: https://arxiv.org/abs/2510.20270. Measures cheat rates and tests which structural fixes work.
6. **Are "Solved Issues" in SWE-bench Really Solved? (PatchDiff)**: https://arxiv.org/abs/2503.15223. Weak oracles inflate resolve rates by about 6 pp.
7. **SWE-rebench**: https://swe-rebench.com/. Fresh-task pass@1 and pass@5 with cost; the reality check on small models.
8. **R2E-Gym**: https://arxiv.org/abs/2504.07164. Hybrid verifiers take a 32B model from 34% to 51%; a blueprint for the gate.
9. **Towards a Science of Scaling Agent Systems**: https://arxiv.org/abs/2512.08296. Controlled single- vs multi-agent study: the 45% ceiling and error amplification.
10. **Anthropic, multi-agent research system**: https://www.anthropic.com/engineering/multi-agent-research-system. About 15x the tokens of chat, and why coding is a poor fit.
11. **Large Language Monkeys**: https://arxiv.org/abs/2407.21787. How coverage grows with samples, and why the selector is the limit.
12. **Inducing Programmatic Skills (ASI)**: https://arxiv.org/abs/2504.06821. Verified code skills beat text skills; the routine-compiler blueprint.
13. **How Memory Management Impacts LLM Agents**: https://arxiv.org/abs/2505.16067. Gated writes beat add-all by 15-25 points.
14. **The Attacker Moves Second**: https://arxiv.org/abs/2510.09023. Why no detector can serve as the boundary.
15. **CaMeL**: https://arxiv.org/abs/2503.18813. Capabilities and data-flow separation, with provable security.
16. **Progent**: https://arxiv.org/abs/2504.11703. Task-scoped privilege in which narrowing is free; the closest match to the T10 sink policy.
17. **llama.cpp server README**: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md. Every local parallelism control.
18. **Continuum**: https://arxiv.org/abs/2511.02230. Keeps an agent's KV cache alive while it waits on tests; over 8x job completion time.
19. **Apple, MLX on M5**: https://machinelearning.apple.com/research/exploring-llms-mlx-m5. Prefill and decode numbers on the owner's chip.
20. **Intelligence per Watt**: https://arxiv.org/abs/2511.07885. The published metric closest to the AWOS objective, with router-accuracy curves.
21. **ML.ENERGY Leaderboard v3 analysis**: https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/. The energy effects of batching, MoE, reasoning mode and images.
22. **Cost-of-Pass** (https://arxiv.org/abs/2504.13359) together with **Conformal Risk Control** (https://arxiv.org/abs/2208.02814). The math for tier ordering and for a gate threshold with a bounded false-accept rate.
23. **Thinking Machines, On-Policy Distillation**: https://thinkingmachines.ai/blog/on-policy-distillation/. Personalisation without forgetting at about a tenth of RL's compute.
24. **CoAct-1**: https://arxiv.org/abs/2508.03923. Code-as-action for computer use; fewer steps and higher success.
25. **The Price of Progress (Gundlach et al.)**: https://arxiv.org/abs/2511.23455. Cost at fixed capability falls while frontier cost rises; the economic case for the cascade.

**First week.** Read 1, 2, 4, 5 and 9, which make the case for gate first and no swarms. Then read 17 and 19 and run the batched-bench sweep. Then read 20 to set up the joules accounting.

**First month.** Add 3, 6, 7 and 8 for the eval and gate design, 12 and 13 for memory and routines, 14-16 for security, 11 and 22 for sampling and stopping math, 18 and 21 for serving and energy, and 23-25 for learning, computer use and economics. Then read gap charts 02, 05 and 06, which turn these into AWOS schemas.

---

## 6. Open questions as pre-registerable hypotheses

Each hypothesis states its comparison and the result that would count as success. Use the T1 paired statistics, and add the A/A noise floor from §3.

- **H1 (gate).** Planting 10 ImpossibleBench-style canaries per 73-issue run will show a current gate false-accept rate above 10%. Read-only tests plus a contradiction exit will cut it below 5% while costing fewer than 2 legitimate solves ([03](03_evals.md), [12](12_rl.md)).
- **H2 (noise).** An A/A run of the E1 control arm at K=1 with pinned providers will show at least 4 discordant pairs out of 73. That would make the current 64% vs 61% comparison against Aider uninterpretable ([03](03_evals.md)).
- **H3 (local batching).** On an idle M5 with Qwen3.5-9B Q4, `-np 8 --kv-unified` will give at least 2.5x the aggregate tok/s of `-np 1` and at least 2x the verified solves per hour on the real-issue set, with p90 latency per agent below 3x ([08](08_low-level-opt.md), [18](18_inference.md)).
- **H4 (hybrid checkpoints).** A fork right after the T7b prefix will show `cache_n` = 0 at the default `--checkpoint-min-step 8192`, and above 80% of the prefix once checkpoints are aligned ([18](18_inference.md)).
- **H5 (sampling).** The local model's pass@5 minus pass@1 on the 73 issues will be at least 15 pp. If it is, local best-of-5 with a hybrid gate will beat one Flash-class cloud call on verified solves per ($·s·Wh); if it is below 5 pp, it will not ([07](07_local-models.md), [12](12_rl.md)).
- **H6 (reasoning).** With thinking off, the local 9B will spend at least 5x fewer output tokens and Wh per verified solve than with thinking on, with no significant drop in solve rate ([13](13_dl-learning.md), [16](16_efficient-intelligence.md)).
- **H7 (energy boundary).** Measured at the wall, joules per verified task for local-attempt-then-escalate will be no lower than cloud-only on the real-issue set unless local gate-pass is at least 40% ([16](16_efficient-intelligence.md), [gap 08](gaps/08_economics-market-adoption.md)).
- **H8 (probe).** A linear probe on the local model's hidden states at the end of each edit will predict gate pass with AUROC ≥ 0.75, against ≤ 0.62 for verbalized confidence ([10](10_bio-intelligence.md), [14](14_decision-math.md)).
- **H9 (memory).** On longitudinal replay, gated-plus-utility-deletion memory will beat add-all memory by at least 10 pp on later-task success, with fewer than half the records ([20](20_security-memory.md)).
- **H10 (recurrence).** A two-week opt-in accessibility-tree capture on the owner's Mac will find at least 5 routines that recur 5 or more times, take at least 2 minutes each and have a state probe. Coding work will show under 5% genuine recurrence ([gap 03](gaps/03_program-synthesis-routine-compilation.md), [gap 04](gaps/04_task-mining-demonstration.md)).
- **H11 (structured computer use).** At least 60% of the owner's recurring chores can be completed with AppleScript, accessibility-tree or CDP actions alone, at under a third of the steps of a pixel agent ([04](04_computer-use.md)).
- **H12 (manifest probe).** A per-task capability manifest checked against observed OS events will flag at least one real, non-adversarial stray write or network connection per 100 agent runs, at a false-flag rate under 2% ([gap 06](gaps/06_capability-os-security.md)).
- **H13 (owner attention).** An ask-once-at-intake policy with batched digests will cut owner-minutes per verified task by at least 30% against per-action approval, with no rise in later reversals ([gap 01](gaps/01_automation-human-factors.md)).
- **H14 (distillation).** After 1,000 gate-verified trajectories, on-policy distillation into the local model will raise held-out first-try gate pass by at least 8 pp. Retrieving the same trajectories without any training will raise it by less than 3 pp ([13](13_dl-learning.md)).

---

## 7. Index of all charts

**Territory charts**
- [01 What actually works in AI agents](01_what-works.md). Production lessons: reliability over capability, gates over prompts, narrow verified work over general agents.
- [02 Agent and cognitive architectures](02_architectures.md). Verifier over scaffold, verified procedural memory, and Soar and SOFAI as a checklist of mechanisms.
- [03 Evals](03_evals.md). Exploitable graders, contamination, infrastructure noise, and the gate stack and eval hygiene for E1.
- [04 Computer use](04_computer-use.md). Structured-first action ladder, small grounders, Rule of Two, and the 2-VM cap.
- [05 AIOS / AI-native OS](05_aios.md). Agent-aware serving, typed tool registries, OS containment, and the unoccupied verification niche.
- [06 Local computing](06_local-computing.md). Bandwidth physics, batching, utilization economics, and privacy as data flow.
- [07 Local/open models](07_local-models.md). Card inflation, pass@k with a hybrid gate, model lineups by RAM, and the training roadmap.
- [08 Low-level optimisation](08_low-level-opt.md). Slots, KV sizing, quantization rules, and when speculation competes with batching.
- [09 Sweet-spot model](09_sweet-spot.md). Route by step type, the ~3B vendor consensus, constrained decoding, and pass^k promotion.
- [10 Biological / energy-efficient intelligence](10_bio-intelligence.md). Data movement dominates energy, plus semantic-entropy probes, information-gain probing and world models; wetware is a dead end.
- [11 Multi-agent](11_multi-agent.md). The decomposability rule, the 45% ceiling, error amplification, and parallel readers with one writer.
- [12 RL for agents](12_rl.md). The gate as reward, reward-hacking fixes, distill-then-RL, and what to log now.
- [13 DL continual/test-time learning](13_dl-learning.md). Facts in memory and skills in weights, on-policy training, LoRA hygiene, and overthinking.
- [14 Decision math and psychology](14_decision-math.md). Cost-of-pass ordering, conformal thresholds, optimal stopping, and structural ask triggers.
- [15 Hardware-software integration](15_hw-sw.md). Roofline and KV budget, measuring watts without sudo, the fast cloud tier, and host fixes.
- [16 Efficient intelligence](16_efficient-intelligence.md). Joules per verified task, reasoning off, batching and MoE levers, and determinism.
- [17 AI frameworks](17_frameworks.md). Keep the thin kernel, copy durable execution, and gate optimisers against regression.
- [18 Inference stacks](18_inference.md). The `AWOS_LOCAL_SLOTS=1` finding, the hybrid checkpoint trap, oMLX, and honest cost accounting.
- [19 Five layers of AI](19_five-layers.md). Diverging cost curves, the missing runtime layer, and Minions and learned routing.
- [20 Agent memory, security, trust](20_security-memory.md). Gated writes, poisoning, deterministic boundaries, and AWOS's secret sprawl.

**Gap charts**
- [Gap 01 Automation human factors](gaps/01_automation-human-factors.md). Supervisory control, approval fatigue, and an ask/act/notify policy with owner-attention metrics.
- [Gap 02 Safety-critical runtime assurance](gaps/02_safety-critical-runtime-assurance.md). The Gatekeeper as Simplex/RTA, a router-enforced ODD schema, and a safety-case template.
- [Gap 03 Program synthesis and routine compilation](gaps/03_program-synthesis-routine-compilation.md). Anti-unification, the routine compiler design, and the 0%/13% ledger eval.
- [Gap 04 Task mining and demonstration](gaps/04_task-mining-demonstration.md). RPA lessons, a capture-to-routine pipeline, and ranked owner task families.
- [Gap 05 Transactional OS and undo](gaps/05_transactional-os-undo.md). The R0-R5 reversibility taxonomy, outbox commit protocol, and measured APFS clone costs.
- [Gap 06 Capability OS security](gaps/06_capability-os-security.md). Per-task capability manifests, OS enforcement, and the footprint probe as a gate signal.
- [Gap 07 Voice and ambient interface](gaps/07_voice-ambient-interface.md). A local cascaded speech stack, the listening-energy budget, and a proactive gate.
- [Gap 08 Economics and market adoption](gaps/08_economics-market-adoption.md). The TCO break-even model, cloud price trends, and pricing local calls by Wh.

## 8. Freshness update (2026-10-10)

A second pass of 28 agents, one per chart, re-checked claims and added March–October 2026 developments. They used the arXiv, GitHub, Hacker News and Hugging Face APIs instead of WebSearch. Each chart now ends with a "Freshness update" section listing new items, corrections, confirmed claims, and what is still unverified. **No main recommendation was overturned.** The changes that matter most:

- **Harness choice buys tokens, not accuracy.** The heaviest and lightest harnesses come out within 5 points of each other at up to 3× the cost (arXiv 2610.04433). None of the 29 adjacent pairs in the SWE-bench Verified top 30 can be separated statistically (arXiv 2609.17394). OpenAI says Verified no longer measures frontier coding. Treat public coding scores as relative signals only ([01](01_what-works.md), [02](02_architectures.md), [03](03_evals.md)).
- **Benchmark cheating is documented, which makes isolating the verifier priority #1.** The top Terminal-Bench 2 entry read `/tests` in 415 of 429 traces. Agents read answers from git history in more than 12% of SWE-Bench Pro rollouts ([01](01_what-works.md), [03](03_evals.md)).
- **Apple Silicon has concurrency candidates.** oMLX 0.7 (continuous batching with an SSD KV tier, M5 prefill kernels), vllm-metal 0.30 (unified KV), and llama.cpp PR 26004 (checkpoints that survive slot save/restore for hybrid models such as Qwen3.5). SiliconBench (arXiv 2609.19169) finds that only vllm-metal scales cleanly from 1 to 16 concurrent streams. The concurrency sweep must be measured on the owner's own engine ([05](05_aios.md), [15](15_hw-sw.md), [16](16_efficient-intelligence.md), [18](18_inference.md)).
- **llama.cpp defaults changed.** `-np` now defaults to auto, and `--kv-unified` is on when slots are auto. So the "raise local slots" advice depends on the build, and AWOS's explicit `AWOS_LOCAL_SLOTS=1` is what serialises requests ([18](18_inference.md)).
- **Computer use.** OSWorld v1 is saturated: Opus 5 scores 83.4%, and OSWorld 2.0 is the new bar, with Simular at 73% (vendor-run). A published neuro-symbolic replay system with a pre-action verifier claims to be 15–217× cheaper (arXiv 2609.36927). Branch-steering attacks reach 94% success even against Dual-LLM defences (arXiv 2610.03089), so verified replay needs pre-declared branches before it counts as injection-safe ([04](04_computer-use.md)).
- **Energy.** Early abort saves 15–20% of wasted agent energy (AgentStop). The scaffold changes tokens per solved task by up to 40×. A single-run pass@1 varies by 2.2–6.0 points, so AWOS's earlier 64% vs 61% result is within noise ([16](16_efficient-intelligence.md)).
- **Ecosystem.** In the MCP 2026-07-28 spec, the protocol is stateless, Tasks moved to an extension, and lists are cacheable. A2A joined the Agentic AI Foundation. Microsoft Agent Framework reached 1.0 GA. Apple's AFM 3 adds a 20B sparse on-device model with 1–4B active parameters, and Foundation Models is open-sourced. Epoch puts the open-vs-closed lag at about 4 months ([05](05_aios.md), [15](15_hw-sw.md), [17](17_frameworks.md), [19](19_five-layers.md)).
- **Downgraded figures.** Live-SWE-agent's 79.2% becomes 77.4%. The SWE-Bench Pro 43.6%/17.8% split could not be reproduced. Continuum's ">8×" claim is unverified. OpenRouter's "10× tokens" is about 4× prompt and 3× completion. Do not cite these figures as stated.
