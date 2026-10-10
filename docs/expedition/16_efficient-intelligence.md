# Powerful, reliable intelligence at low energy

*Expedition chart 16. Compiled 2026-10-10 from four scout reports covering energy measurement, algorithmic efficiency trends, reliability engineering, and useful-work-per-watt metrics.*

> **Coverage caveat.** All four scouts ran out of the session's shared WebSearch budget (200 calls across all agents) on or before their first search. Every finding here comes from WebFetch reads of primary sources the scouts already knew about. 2026 work that none of them could name in advance, especially energy measured over whole agent trajectories, may be missing. Numbers marked *(self-reported)* come from the vendor or the authors and have not been independently replicated. Numbers marked *(abstract)* were read from the abstract only.

---

## 1. Summary

- **Energy per task ≈ tokens generated × joules per token at the operating point.** Decode accounts for 50-99% of inference energy. Energy and runtime fit token counts with R² > 0.96 per model. Output length is the first-order lever and hardware is second-order.
- **Three levers are each larger than a GPU generation.** Turning reasoning mode off saves about 30x on average and up to about 700x for small models. Raising batch size or concurrency on one server cuts J/token 3-5x. A sparse MoE uses about 3.5x less energy per token than a dense model of similar size. B200 vs H100 is only about 35% (median).
- **"Local is greener" depends on the unit and the boundary.** On agentic GAIA and TerminalBench, local hardware scores about 3.7x better on accuracy per *watt*, while cloud scores 2.4-3.0x better on accuracy per *joule*. The right AWOS unit is **joules per verified-successful task**, with failed attempts and escalations charged to the task.
- **The router or verifier is worth more than the local model.** In the IPW routing simulation, an oracle local/cloud router cut energy 80.4%, and an 80%-accurate router still cut it 64.3%. Cascade theory says the same thing: quality estimation is the bottleneck.
- **Small models catch up on a schedule.** Frontier capability reaches a single consumer GPU in about 6-12 months. Capability density doubles about every 3.3 months on benchmarks. The price of fixed capability falls 9-900x per year, so the cloud baseline AWOS competes against also keeps getting cheaper.
- **Distillation beats RL for small models at about 1/10 the compute.** Qwen3-8B reached better scores with 1,800 vs 17,920 GPU-hours. On-policy distillation also recovers skills lost during domain fine-tuning, which makes it the most concrete route to a model that "gets better at the owner's work".
- **Reliability is a separate axis from capability and is improving more slowly.** pass^k falls well below pass@1. Errors are strongly correlated across models (about 60% identical wrong answers). Temperature 0 is not deterministic unless batch-invariant kernels are used. Redundancy only pays off when the checks are independent and executable.

---

## 2. What matters most (ranked)

**1. Avoid the LLM call entirely (replay, compiled routines, tiny classifiers).** Task-specific systems are "orders of magnitude" cheaper than general generative models per inference, even at matched parameter count. That makes verified-routine replay the largest per-task energy saving available. Source: [Power Hungry Processing](https://arxiv.org/abs/2311.16863).

**2. Control output tokens, especially reasoning tokens.** AI Energy Score v2 (H100, Wh per 1,000 queries) shows the cost of reasoning mode:

| Model | Reasoning off | Reasoning on | Multiplier |
|---|---|---|---|
| R1-Distill-Llama-70B | 49.5 | 7,626 | 154x |
| Phi-4-reasoning-plus | 18.4 | 9,462 | 514x |
| SmolLM3-3B | 18.4 | 12,791 | 697x |

On ML.ENERGY v3, problem solving costs 25x more energy per response than chat (6,988 vs 717 output tokens, plus 1.5-2.1x higher J/token). More reasoning is not a free reliability lever either: in HAL (21,730 rollouts), higher reasoning effort *lowered* accuracy in most runs. Sources: [AI Energy Score v2](https://huggingface.co/blog/sasha/ai-energy-score-v2), [ML.ENERGY v3](https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/), [HAL](https://arxiv.org/abs/2510.11977).

**3. The verifier and router set reliability per dollar and per joule.** In the IPW simulation (80.2M queries), router accuracy maps to energy saved as follows: oracle 80.4%, 80% accuracy 64.3%, 60% accuracy 48.4%. Cascade-routing theory names "good quality estimators" as the critical factor. Imperfect verifiers impose a false-positive ceiling that resampling cannot remove, and the best number of attempts is usually under 10. Sources: [Intelligence per Watt](https://arxiv.org/html/2511.07885), [Routing and Cascading](https://arxiv.org/abs/2410.10347), [Limits of Resampling](https://arxiv.org/abs/2411.17501).

**4. Batch or concurrency on one shared server.** J/token falls 3-5x as batch size rises. Llama 3.1 8B with chunked prefill went from 559.66 J per generation at batch 32 to 151.50 J at batch 1024. Local devices typically run at batch 1, which is a main reason cloud wins per joule. **This is the energy answer to "why not run more than 8 agents?"** N agents sharing one continuous-batching server get the batch discount. N separate processes each running at batch 1 do not. The ceiling is KV-cache memory, not agent count. Source: [ML.ENERGY v3](https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/).

**5. Active parameters, not total parameters.** Qwen3-30B-A3B used 3.56x less energy per token than dense Qwen3-32B. On a unified-memory box (lots of RAM, modest bandwidth), a small-active MoE that fits in memory is the likely sweet spot. Source: [ML.ENERGY v3](https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/).

**6. Measure; do not estimate.** TDP-based estimates overestimated energy 4.1x (CodeGemma 2B on H100). FLOP-based estimates *underestimate* full-system energy. Software choices alone change energy by up to 73%. nvidia-smi samples only about 25% of runtime on A100/H100. Thermal state at the start of a measurement biases readings, so use windows of at least 5 s plus cooldowns. Sources: [ML.ENERGY Benchmark](https://arxiv.org/abs/2505.06371), [Fernandez et al.](https://arxiv.org/abs/2504.17674), [Part-time Power](https://arxiv.org/abs/2312.02741), [Thermally stable profiling](https://ml.energy/blog/energy/measurement/thermally-stable-profiling-for-accurate-gpu-energy-measurement/).

**7. Distill from the teacher you already pay for.** R1-Distill-Qwen-32B scored 72.6 on AIME vs 47.0 for RL on the same base. Qwen3-8B with distillation vs RL: AIME24 74.4 vs 67.6, using 1,800 vs 17,920 GPU-h. Thinking Machines *(self-reported)* used on-policy distillation to bring IF-eval back from 45% to 83% after domain mid-training, while keeping the new knowledge (41% vs 18%). Distillation scaling laws say it pays when a teacher already exists or when many students are trained, which is AWOS's situation. Sources: [DeepSeek-R1](https://arxiv.org/html/2501.12948v1), [Qwen3](https://arxiv.org/html/2505.09388v1), [On-Policy Distillation](https://thinkingmachines.ai/blog/on-policy-distillation/), [Distillation Scaling Laws](https://arxiv.org/abs/2502.08606) *(abstract)*.

**8. Measure reliability as consistency, not one-shot success.** On tau-bench, GPT-4o succeeds on under 50% of tasks overall, and pass^8 in retail is under 25%. Across 15 models, reliability gains lag capability gains, and larger models are more variable from run to run. Sources: [tau-bench](https://arxiv.org/abs/2406.12045), [Science of Agent Reliability](https://arxiv.org/abs/2602.16666).

**9. Determinism is a cheap reliability primitive.** At temperature 0, 1,000 Qwen3-235B completions produced 80 distinct outputs. With batch-invariant kernels, all 1,000 were identical, at a cost of 26 s → 42 s. SGLang's deterministic mode costs 25-45% (34% average) and adds seeded sampling. A single-user host at batch 1 may get determinism almost for free. Precision alone shifts R1-Distill-7B accuracy by up to 9%. Sources: [Defeating Nondeterminism](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/), [SGLang deterministic](https://lmsys.org/blog/2025-09-22-sglang-deterministic/), [LayerCast](https://arxiv.org/abs/2506.09501).

**10. Decompose into checkable micro-steps when you need near-perfect reliability.** MAKER finished a task of over a million steps with zero errors using small non-reasoning models (gpt-oss-20b, GPT-4.1-mini). It combined first-to-ahead-by-k voting with red flags that discard outputs over about 700 tokens or with bad formatting. Cost scales as Θ(c·s·ln s). The strategy was given in the prompt, so this shows execution reliability, not discovery. Source: [MAKER](https://arxiv.org/abs/2511.09030).

**11. Screenshots cost a lot in computer use.** Images cost 1.1-5.2x the energy per token of text, and video 1.3-15x. CPU-side vision preprocessing limited batch size. Source: [ML.ENERGY v3](https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/).

**12. Long context costs a lot.** Epoch estimates about 0.3 Wh for GPT-4o at 500 output tokens, about 2.5 Wh at 10k input tokens, and about 40 Wh at 100k input tokens. Agent prompts sit at the expensive end, so prefix caching and skeleton views save energy as well as dollars. Source: [Epoch AI](https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use).

---

## 3. What does NOT matter: hype and dead ends

| Claim / approach | Why it doesn't hold |
|---|---|
| "Local Mac is 30-40x more efficient than datacenter GPUs" (GreenBench) | This compares against unbatched cloud and uses a GPU/SoC-only boundary. M4 Pro at about 10 W / 59 tok/s works out to roughly 0.17 J/token (scout arithmetic). An H100 at batch 64 does about 0.12 J/token for an 8B model. In like-for-like tests, IPW finds cloud 2.4-3x better per joule. Local wins on privacy, latency and marginal cost, not on silicon efficiency. GreenBench's figure of 0.47 W CPU+GPU package power conflicts with its own system figure of 8-12 W. |
| Accuracy per *watt* as the headline metric | It ignores time, so slow low-power hardware looks good. Use accuracy per joule. |
| TDP- or FLOP-based energy estimates | Errors of up to 4.1x in either direction. |
| "Quantize to save energy" | FP8 used up to 56% *more* energy at batch 8-16 and saved only about 11% at batch 65 or more. Locally, quantization buys memory fit and bandwidth. Over-trained small models also lose more from post-training quantization ([Scaling Laws for Precision](https://arxiv.org/abs/2411.04330)). Measure each case. |
| New GPU generation as the main energy lever | A100 → H100 gave minimal benefit for memory-bound decode, and B200 is about 35% better. Batching, MoE and reasoning-off are each 3-700x levers. |
| More parallel agents or votes as a *reliability* strategy | Errors are correlated: about 60% identical wrong answers across 350+ models ([Correlated Errors](https://arxiv.org/abs/2506.07962)). Voting accuracy can rise and then fall as calls increase ([More LLM Calls](https://arxiv.org/abs/2403.02419)). Multi-agent systems mostly fail on design and verification ([MAST](https://arxiv.org/abs/2503.13657)). Parallelism buys throughput and batching efficiency. It buys reliability only when each output passes an executable check. |
| Cross-model LLM judges as "independent" checks | Correlated errors make a second model a weak check. Diversity has to come from *kinds* of checks: tests, static analysis, state probes. |
| Static-threshold semantic caches (GPTCache-style) | Their error rate is unbounded. vCache's per-entry learned thresholds report up to 12.5x more hits and 26x lower error *(self-reported)* ([vCache](https://arxiv.org/abs/2502.03771)). The trap is the static threshold, not fuzzy caching as such. |
| RL from scratch on small local models | Distillation beats it at about 10x less compute. RL is a final polish step. |
| Raw long-CoT frontier traces distilled into models of 3B or under | This hits the learnability gap. Mixing trace lengths or teachers fixes it ([Li et al.](https://arxiv.org/abs/2502.12143), *abstract*). |
| Densing law (3-month doubling) taken as a fact about agentic reliability | It is measured on static, contamination-prone benchmarks. Check it on the owner's own task set. |
| MLPerf Power for choosing models | v5.1 had only two power submissions. |
| HF AI Energy Score stars, "How Hungry is AI?" 29 Wh figures, "3 Wh per query" folklore, water/carbon per query | These are GPU-only, inferred, or use inconsistent boundaries. Google's measured full-stack median is 0.24 Wh *(self-reported)*. For a local box, Wh is the quantity AWOS can control; carbon only adds grid-mix noise. |
| Speculative agent actions as a headline feature | They are lossless but deliver at most about 20% latency gain with 55% prediction accuracy, and they cost extra watts ([Speculative Actions](https://arxiv.org/abs/2510.04371)). |
| Cluster DVFS systems (DynamoLLM, Perseus) as things to build | The only idea that transfers to one Mac: run background work at low clocks or in low-power mode, and run interactive work at full speed (energy = power × time). |

---

## 4. Key papers and resources

### Must-read
- **Intelligence per Watt** (Saad-Falcon et al.): https://arxiv.org/abs/2511.07885 (HTML: https://arxiv.org/html/2511.07885). Closest work to the AWOS objective. Defines IPW and APJ, includes local-vs-cloud routing simulations and agentic GAIA/TerminalBench results, and ships an open profiling harness.
- **ML.ENERGY Leaderboard v3.0 analysis**: https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/. Batch, MoE, FP8, reasoning and multimodal energy drivers across 46 models and 1,858 configs.
- **The ML.ENERGY Benchmark** (NeurIPS D&B 2025): https://arxiv.org/abs/2505.06371. Decode share, TDP-estimate error, and 44% savings from targeting a latency SLO instead of the fastest configuration.
- **Thinking Machines, On-Policy Distillation**: https://thinkingmachines.ai/blog/on-policy-distillation/. Recipe for personalizing without forgetting *(self-reported)*.
- **Thinking Machines, Defeating Nondeterminism**: https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/. Batch invariance is the root cause of nondeterminism.
- **Towards a Science of AI Agent Reliability**: https://arxiv.org/abs/2602.16666. Twelve metrics: consistency, robustness, predictability, safety.
- **A Unified Approach to Routing and Cascading**: https://arxiv.org/abs/2410.10347. Shows the gate dominates.
- **MAKER, million-step zero-error task**: https://arxiv.org/abs/2511.09030.
- **zeus-apple-silicon / profiling on Macs**: https://ml.energy/blog/energy/measurement/profiling-llm-energy-consumption-on-macs/. Sub-millisecond per-rail energy (CPU, GPU, ANE, DRAM).

### Useful
- AI Energy Score v2: https://huggingface.co/blog/sasha/ai-energy-score-v2 (multipliers for reasoning mode). Docs: https://huggingface.github.io/AIEnergyScore/
- ML.ENERGY longitudinal analysis: https://ml.energy/blog/measurement/energy/llm-inference-energy-a-longitudinal-analysis/ (software alone cut 15-41%; 0.20 → 0.12 J/token)
- Google full-stack measurement: https://arxiv.org/abs/2508.15734 (template for the measurement boundary)
- Epoch, consumer-GPU gap: https://epoch.ai/data-insights/consumer-gpu-model-gap
- Epoch, inference price trends: https://epoch.ai/data-insights/llm-inference-price-trends
- Densing Law: https://arxiv.org/abs/2412.04315
- DeepSeek-R1 distillation tables: https://arxiv.org/html/2501.12948v1
- Qwen3 Technical Report: https://arxiv.org/html/2505.09388v1
- Distillation Scaling Laws: https://arxiv.org/abs/2502.08606
- Small Models Struggle to Learn from Strong Reasoners: https://arxiv.org/abs/2502.12143
- Agent Distillation with Retrieval and Code Tools: https://arxiv.org/abs/2505.17612 (0.5-3B agent-distilled students match CoT-distilled students one size tier up)
- Scaling Test-Time Compute Optimally: https://arxiv.org/abs/2408.03314. s1: https://arxiv.org/abs/2501.19393
- tau-bench: https://arxiv.org/abs/2406.12045
- Correlated Errors in LLMs: https://arxiv.org/abs/2506.07962
- Limits of Resampling: https://arxiv.org/abs/2411.17501. More LLM Calls: https://arxiv.org/abs/2403.02419
- SGLang deterministic: https://lmsys.org/blog/2025-09-22-sglang-deterministic/
- LayerCast: https://arxiv.org/abs/2506.09501
- vCache: https://arxiv.org/abs/2502.03771. Agentic Plan Caching: https://arxiv.org/abs/2506.14852 (about 50% lower cost, *self-reported*)
- Conformal Abstention: https://arxiv.org/abs/2405.01563
- HAL: https://arxiv.org/abs/2510.11977. MAST: https://arxiv.org/abs/2503.13657
- Minions (local reads, cloud reasons): https://arxiv.org/abs/2502.15964. 5.7x lower cloud cost at 97.9% of quality.
- Fernandez et al., Energy Considerations: https://arxiv.org/abs/2504.17674
- Measuring GPU energy best practices: https://ml.energy/blog/energy/measurement/measuring-gpu-energy-best-practices/. Thermal: https://ml.energy/blog/energy/measurement/thermally-stable-profiling-for-accurate-gpu-energy-measurement/
- Zeus library: https://ml.energy/zeus/. macmon (no sudo, JSON): https://github.com/vladkens/macmon

### Reference
- GreenBench (M4 Pro): https://arxiv.org/abs/2608.28667 (unreviewed; some numbers suspect)
- Algorithmic Progress in LMs: https://arxiv.org/abs/2403.05812
- Beyond Chinchilla-Optimal: https://arxiv.org/abs/2401.00448
- Scaling Laws for Precision: https://arxiv.org/abs/2411.04330
- Phi-4: https://arxiv.org/abs/2412.08905 *(self-reported, contamination questions)*
- BitNet b1.58 2B4T: https://arxiv.org/abs/2504.12285 (native low-bit, 2B only)
- SLMs are the Future of Agentic AI (NVIDIA): https://arxiv.org/html/2506.02153 (position paper; replaceable-call estimates of 40-70% are unmeasured)
- Offline Energy-Optimal Serving (Wilkins et al.): https://arxiv.org/abs/2407.04014
- DynamoLLM: https://arxiv.org/abs/2408.00741
- How Hungry is AI?: https://arxiv.org/abs/2505.09598 (inferred, not measured)
- Epoch, ChatGPT energy: https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use
- Mistral LCA: https://mistral.ai/news/our-contribution-to-a-global-environmental-standard-for-ai
- MLPerf Power: https://arxiv.org/abs/2410.12032
- Part-time Power Measurements: https://arxiv.org/abs/2312.02741
- Apple Mac mini power: https://support.apple.com/103253
- Power Hungry Processing: https://arxiv.org/abs/2311.16863
- Speculative Actions: https://arxiv.org/abs/2510.04371

---

## 5. Implications for AWOS

### Adopt now
1. **Define the watt term as joules per verified-successful task.** Charge failed local attempts, repairs and cloud escalations to the task they serve. Report idle Wh/day separately: a Mac mini M4 idles at about 4 W, roughly 0.1 kWh/day. Idle-time practice spends that budget, so charge it against the savings it later produces.
2. **Instrument energy.** The scouts found no powermetrics, Zeus or NVML code in the repo.
   - Add `wh` fields to `.awos/spans.jsonl`, with per-span rail attribution via `zeus-apple-silicon` or `macmon`, calibrated against a smart-plug wall meter.
   - Until that exists, fit a per-device, per-model estimator `E = a·in_tok + b·out_tok + c·idle_s` (R² > 0.96 in the literature).
   - Price cloud calls from ML.ENERGY v3 J/token, using Google's 0.24 Wh as a lower bound, and state the boundary.
3. **Log output tokens and thinking tokens per task** as the first-order energy metric. Default reasoning mode to *off* on the local tier, and enable it only after the gate fails (a 30-700x lever).
4. **Log every verified escalation trace at full fidelity.** Cloud escalations are free teacher signal for later distillation. Only gate-passed traces count, which extends the "memory only from strong evidence" rule to training data.
5. **Report pass^k (k = 3-5) and outcome consistency** alongside pass@1. The 64% vs 61% comparison against Aider is single-run. Promotion to verified-routine replay should require pass^k on the task class.

### Test next (map to Gatekeeper rungs)
| Experiment | Gatekeeper rung | Expected signal |
|---|---|---|
| **Concurrency sweep**: 1/2/4/8/16 agents against *one* shared batching server (MLX batch generation, llama.cpp `--parallel`, or vLLM) vs one process per agent. Measure J/task and tok/s, and find where KV memory caps it. | Local attempt | 3-5x J/token improvement until KV pressure. This is the measured answer to "more than 8 agents". |
| Small-active MoE (30B-A3B class) vs dense 9B, compared on J per verified success | Local attempt | About 3.5x less energy per token if it fits in memory |
| Quantization A/B (4-bit vs 8-bit) on the real-issue set, measuring energy and pass rate | Local attempt | Do not assume low bits are free |
| Determinism check: hash 100 repeated temperature-0 local completions, including across restarts | Replay | If bitwise stable, replay can rest on exact equality |
| Error correlation between the local 9B and the cloud model on the same issues | Escalation | Decides whether "local fails, cloud repairs" is real redundancy |
| Conformal or Beta-posterior escalation threshold calibrated on ledgers (e.g. ≤5% false accepts per class) | Verification gate | Replaces a hand-tuned threshold with a stated guarantee |
| MAKER-style micro-steps (locate → one-hunk edit → static gate) with ahead-by-k voting and red flags (>700 tokens or malformed) | Bounded repair / compiled routines | Near-zero per-step error if errors stay independent |
| On-policy distillation, cloud teacher → local 9B on owner tasks, with held-out retention checks | Memory / improvement loop | First training experiment, ahead of RL |
| Plan-template caching between exact replay and fresh solving | Replay T0/T0.5 | About 50% cost reduction *(self-reported)* |

### Computer use (the end goal)
- **Observations should come from the accessibility tree or DOM first, with screenshots only on demand.** Image tokens cost 1.1-5.2x the energy of text tokens, and host-side preprocessing throttles the accelerator. Profile image preprocessing on the host.
- Computer-use micro-steps (one action, one postcondition probe) fit MAKER-style reliability better than open-ended repo patches do. State probes are the independent, executable checks that make voting worth its cost.

### Answering the owner's parallelism question directly
Evidence and the scouts' own experience point to three separate limits:
1. **Local energy and throughput.** More concurrent agents is *good* if they share one batching inference server, because J/token falls 3-5x. It is bad if each agent runs its own model at batch 1. The real ceiling is KV-cache memory; stable prefixes plus prefix caching raise it.
2. **Reliability.** More agents add throughput, not correctness. Each parallel output needs its own executable check (MAST, correlated errors).
3. **Shared tool budgets.** In this expedition every scout was starved by the shared WebSearch budget (200 calls per session), not by the agent count. Going past 8 parallel agents without raising shared quotas such as `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` just drains the budget faster. Per-agent search quotas would make wider fan-out useful. *The scouts did not inspect where the 8-agent cap is set.*

### Watch
- Each new open-model generation: re-measure the local-capable task set, since the 6-12 month lag means today's escalations become local work.
- Native low-bit models (BitNet-class beyond 2B).
- The cloud price decline (9-900x per year) shrinking local's dollar advantage, which leaves privacy, latency, offline use and owner memory as the advantages that last.
- Minions-style splits (local reads context, cloud reasons) as an alternative to escalate-on-failure.

### Ignore
TDP/FLOP estimates, carbon/water framing, cluster DVFS, accuracy per watt as a headline, static-threshold semantic caches, LLM-judge "redundancy", and speculative actions as a priority.

---

## 6. Open questions worth exploring next

1. What are AWOS's real J/token and J/task on the owner's Mac as concurrency rises (1-16 agents on one server)? No public unified-memory batch-scaling curve exists.
2. What is the energy per *resolved* SWE issue, including failures and escalations? No public benchmark reports it, so AWOS could publish the first one.
3. How do reasoning budgets trade success rate against joules on coding tasks for small local models? AI Energy Score reports energy but not the accuracy gained.
4. Does speculative decoding (prompt lookup or n-gram, which suits code edits) cut J/token at batch 1 on Apple Silicon, or only latency? No primary energy study was found.
5. Is local MLX inference at batch 1 bitwise deterministic?
6. Does the learnability gap also apply to agent trajectories, or only to math chain-of-thought? This decides whether a tiny router or verifier can be trained on raw cloud traces.
7. How many verified traces per owner does on-policy distillation need to move a local 9B, and can it run overnight within a watt budget on the owner's box?
8. Does the capability-density doubling hold for agentic coding? Fit AWOS's own doubling time on the fixed real-issue set across model generations.
9. What does screenshot-based vs accessibility-tree computer use cost in energy on a local VLM?
10. A follow-up literature pass *with* search budget is needed for 2026 agent-trajectory energy papers, durable execution (Temporal/DBOS), and newer distilled coding agents.

---

## 7. Sources

- https://ml.energy/blog/measurement/energy/diagnosing-inference-energy-consumption-with-the-mlenergy-leaderboard-v30/
- https://ml.energy/blog/measurement/energy/llm-inference-energy-a-longitudinal-analysis/
- https://ml.energy/blog/energy/measurement/thermally-stable-profiling-for-accurate-gpu-energy-measurement/
- https://ml.energy/blog/energy/measurement/measuring-gpu-energy-best-practices/
- https://ml.energy/blog/energy/measurement/profiling-llm-energy-consumption-on-macs/
- https://ml.energy/zeus/
- https://github.com/vladkens/macmon
- https://arxiv.org/abs/2505.06371 · https://arxiv.org/html/2505.06371
- https://huggingface.co/blog/sasha/ai-energy-score-v2
- https://huggingface.github.io/AIEnergyScore/
- https://arxiv.org/abs/2608.28667
- https://arxiv.org/abs/2504.17674
- https://arxiv.org/abs/2508.15734
- https://arxiv.org/abs/2505.09598
- https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use
- https://arxiv.org/abs/2408.00741
- https://arxiv.org/abs/2407.04014
- https://mistral.ai/news/our-contribution-to-a-global-environmental-standard-for-ai
- https://arxiv.org/abs/2311.16863
- https://epoch.ai/data-insights/consumer-gpu-model-gap
- https://epoch.ai/data-insights/llm-inference-price-trends
- https://arxiv.org/abs/2412.04315
- https://arxiv.org/abs/2403.05812
- https://arxiv.org/html/2501.12948v1
- https://arxiv.org/html/2505.09388v1
- https://thinkingmachines.ai/blog/on-policy-distillation/
- https://arxiv.org/abs/2502.08606
- https://arxiv.org/abs/2502.12143
- https://arxiv.org/abs/2505.17612
- https://arxiv.org/abs/2401.00448
- https://arxiv.org/abs/2411.04330
- https://arxiv.org/abs/2408.03314
- https://arxiv.org/abs/2501.19393
- https://arxiv.org/abs/2412.08905
- https://arxiv.org/html/2506.02153
- https://arxiv.org/abs/2504.12285
- https://arxiv.org/abs/2602.16666
- https://arxiv.org/abs/2406.12045
- https://arxiv.org/abs/2511.09030
- https://arxiv.org/abs/2411.17501
- https://arxiv.org/abs/2403.02419
- https://arxiv.org/abs/2506.07962
- https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- https://lmsys.org/blog/2025-09-22-sglang-deterministic/
- https://arxiv.org/abs/2506.09501
- https://arxiv.org/abs/2502.03771
- https://arxiv.org/abs/2506.14852
- https://arxiv.org/abs/2410.10347
- https://arxiv.org/abs/2405.01563
- https://arxiv.org/abs/2510.11977
- https://arxiv.org/abs/2503.13657
- https://arxiv.org/abs/2510.04371
- https://arxiv.org/abs/2511.07885 · https://arxiv.org/html/2511.07885
- https://arxiv.org/abs/2312.02741
- https://arxiv.org/abs/2410.12032
- https://arxiv.org/abs/2502.15964
- https://support.apple.com/103253

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method note: WebSearch was not used. arXiv search was rate-limited (HTTP 429) after the first two queries, so most discovery went through the Hugging Face papers search, Hacker News, and GitHub. Abstracts were read via HF paper metadata or arXiv abs pages. Full texts were not read except IPW.

### New since the chart (dated, with URLs; most important first)

1. **AgentStop (2026-05-01, ACM CAIS '26).** Measures time, token and energy overhead of locally deployed coding and web agents on consumer hardware. A supervisor uses token log-probs to kill trajectories unlikely to succeed. It cuts *wasted* energy 15-20% with under 5% utility drop. This is the first agent-trajectory energy paper found, which was open question 10. It supports charging failed attempts to the task, and a cheap early-abort gate is a Stage-1 candidate. https://arxiv.org/abs/2605.15206
2. **SiliconBench (2026-09-12).** Benchmarks nine Apple Silicon serving engines at concurrency 1-16 on chat and agent workloads. Only vllm-metal "more than doubles throughput" from concurrency 1 to 16, and that is on Qwen3-0.6B. CUDA vLLM and SGLang scale better. Some stacks complete every request while memory approaches physical capacity and throughput *falls*. It reports no energy. This is the first public Apple-Silicon concurrency curve (open question 1). It qualifies the chart's "3-5x J/token from batching" for a Mac: the gain is engine-dependent and memory-limited. Run the concurrency sweep on the owner's engine rather than assuming it. https://arxiv.org/abs/2609.19169
3. **SWEnergy (2025-12-10; missed by the chart).** Four agent frameworks with 1.7-4B SLMs on SWE-bench Verified Mini, 150 runs per configuration. Framework architecture drives energy (9.4x spread), yet resolution rates were near zero. Small models in heavy scaffolds waste energy. https://arxiv.org/abs/2512.09543
4. **Scaffold Effect (2026-06-08).** The harness alone changes tokens per solved task by up to 40x, while paired pass-rate differences are 0-8 pp. For AWOS, the tokens-per-resolved-task term depends on the harness at least as much as on the model. https://arxiv.org/abs/2607.22585
5. **Offline LLM energy/cost blog on HN (2026-05-17, 355 points).** Reports an M5 Max at about 50-100 W and 10-40 tok/s on Gemma 4 31B. Amortized hardware plus electricity comes to about $0.40-4.79 per million tokens, against about $0.38-0.50 on OpenRouter. Hardware cost dominates electricity. This is a single blogger's measurement, but it supports the chart's point that local's lasting advantages are privacy, latency and offline use, not cost or energy. https://www.williamangel.net/blog/2026/05/17/offline-llm-energy-use.html
6. **On Randomness in Agentic Evals (2026-02-06).** 60,000 SWE-bench Verified trajectories. Single-run pass@1 varies 2.2-6.0 pp depending on which run is picked, and the standard deviation exceeds 1.5 pp even at temperature 0. This backs the chart's pass^k recommendation and means the 64% vs 61% result (single run) is within noise. https://arxiv.org/abs/2602.07150
7. **SERA (2026-01-28).** SFT-only repository-specialized coding agents, claimed 26x cheaper than RL and 57x cheaper than earlier synthetic-data methods *(self-reported)*. It adds weight to "distill or SFT before RL" for private-codebase specialization. https://arxiv.org/abs/2601.20789
8. **On-policy distillation for multi-turn agents is now an active line.** Examples: TCOD (2026-04-27), Multi-Turn OPD with Prefix Replay (2026-07-16), RetireOPD (2026-09-17), ActFirst-OPD (2026-09-29, 1.8-4.9x faster training on 0.6-4B Qwen3 students). All are small-model, ALFWorld/WebShop-style evaluations, not coding. The method is maturing, but evidence on repo-level coding is still thin. https://arxiv.org/abs/2609.36608
9. **Where Do the Joules Go? (2026-01-29).** This is the paper form of ML.ENERGY v3. It covers 46 models and 1,858 configs on H100 and B200. It says LLM task type gives up to 25x energy differences and GPU utilization differences give 3-5x. https://arxiv.org/abs/2601.22076
10. **Calibrated routing/caching.** UCCI (2026-05-11): isotonic-calibrated per-query error with a cost-minimizing threshold, 31% cost cut at fixed F1 on NER. Not coding. Closing the Calibration Gap in Semantic Caching (2026-06-18): offline-best cache scorers are often the worst at deployment. Both back the chart's calibrated-threshold advice. https://arxiv.org/abs/2605.18796 · https://arxiv.org/abs/2606.19719
11. **TraceLab (2026-06-30) and CacheWise (2026-06-15).** Real coding-agent traces. Workloads have long contexts, short outputs, and high but imperfect prefix-cache hit rates. CacheWise cuts KV evictions 2-2.6x. This supports the chart's prefix-caching point and gives data to model KV pressure for the concurrency sweep. https://arxiv.org/abs/2606.30560 · https://arxiv.org/abs/2606.16824
12. **Tooling.** Zeus v0.16.0 (2026-07-07) and macmon v0.9.0 (2026-10-07) are both actively released, so the instrumentation plan has maintained tools. https://github.com/ml-energy/zeus · https://github.com/vladkens/macmon
13. **JustFit (2026-09-15).** MLX runtime serving 213k-token context on a 24 GiB M4 Pro (6.93x the mlx-vlm baseline). Long-context memory on a Mac is improving, which affects the KV ceiling. https://arxiv.org/abs/2609.17475

### Corrections

- Chart: "Intelligence per Watt ... Third, local ... " is not claimed in the chart, but note the **IPW abstract** says local accelerators have "at least 1.4x lower IPW than cloud accelerators running identical models", and IPW "improved 5.3x" from 2023 to 2025 with local query coverage rising 23.2% to 71.3% (https://arxiv.org/abs/2511.07885). The chart's framing (cloud wins per joule, local wins per watt) is consistent with this. No numeric contradiction.
- Chart: GreenBench "30-40x" and "0.47 W CPU+GPU vs 8-12 W system". The arXiv page (https://arxiv.org/abs/2608.28667, submitted 2026-08-24) does state both numbers, so the chart's description of the internal inconsistency is accurate. It was not updated or retracted. Corrections: none found.
- Other corrections: none found.

### Confirmed claims (briefly)

- IPW routing simulation: oracle 80.4% energy savings, 80%-accuracy router 64.3%, 60% 48.4% (https://arxiv.org/html/2511.07885).
- IPW agentic appendix E.12: cloud 2.4-3.0x higher per-joule, local M4 Max 3.7-3.8x higher per-watt, about 2.2 pp accuracy loss locally. Matches the chart.
- ML.ENERGY v3 headline: 25x task-type spread and 3-5x from utilization (https://arxiv.org/abs/2601.22076).
- Science of Agent Reliability: capability gains yield only small reliability gains (https://arxiv.org/abs/2602.16666). The abstract says 14 models; the chart says 15 models, a minor discrepancy that probably reflects a version difference.

### Still unverified

- The AI Energy Score v2 multipliers (154x/514x/697x), the 3.56x MoE figure, the FP8 "56% more energy" claim, and Thinking Machines' IF-eval numbers were not re-checked.
- HAL's "higher reasoning effort lowered accuracy" and MAKER's details were not re-read. Ares (https://arxiv.org/abs/2603.07915) suggests static low-effort modes degrade agent performance, which may cut against a blanket "reasoning off" default for agents. Test per step type.
- The Epoch Wh figures and the cloud price decline rate were not rechecked.
- No primary study was found on speculative decoding energy at batch 1 on Apple Silicon, nor on bitwise determinism of MLX.
- The 2026 papers above were read from abstracts only, and several are single-lab and not yet replicated.
