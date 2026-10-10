# Biological and energy-efficient intelligence

*Expedition chart 10. Compiled 2026-10-10 from four scout reports: wetware/organoid intelligence, neuromorphic hardware, the brain as an efficiency benchmark, and active inference / predictive coding.*

> **Method caveat.** Every scout in this run hit the session-wide WebSearch limit (200 calls shared by all parallel agents) before making a single search. All evidence below therefore comes from direct WebFetch of primary sources the scouts already knew about: arXiv, PMC, Frontiers, vendor pages, and the GitHub API. Paywalled papers (Nature, Cell, Neuron, Science) were cited from abstracts or DOIs only. Claims marked *UNVERIFIED* are from the scouts' memory. Coverage of 2026 work is thinner than it should be.

---

## 1. Summary

- **Wetware is real biology, not a compute platform.** Cultured neurons (10^5–10^6 cells) learn toy control tasks like Pong and cartpole within minutes. An independent 2026 replication found the gains **vanish after a 45-minute rest**. A CL1 unit costs US$35k (or about $300/week in the cloud), uses about 30 W including life support, has 59 electrodes, and the culture lasts 6 months at most. The field's realistic market is drug screening.
- **The brain's "20 W" is mostly communication, not computation.** About 0.1 W of cortical ATP goes to computation versus about 3.5 W to communication, a **35x** ratio. Energy limits mean fewer than 1% of neurons can be strongly active at once. The lesson that transfers is to *minimise data movement and keep the default path sparse*, not to "reach brain-level FLOPs".
- **The neuromorphic LLM wins that are real come from memory locality, not spikes.** IBM NorthPole serves a 3B model at under 1 ms/token on 16 cards by keeping 4-bit weights in on-chip SRAM. The best Loihi 2 LLM result is a 370M model at 405 mJ/token, partly extrapolated, and only about 2–3x better than a Jetson Orin Nano. None of this hardware can be bought for an AI box, and none of it runs a 7B+ agent model.
- **"Spiking LLMs" are linear attention plus activation quantisation under another name.** SpikingBrain-7B loses about 8–10 MMLU points against its Qwen2.5-7B base. Its energy savings are *estimated, not measured*. Its measured gain (over 100x time-to-first-token at 4M tokens) comes from hybrid linear attention, which needs no spikes.
- **Vendor efficiency multipliers (15 TOPS/W, 18x, 72.7x, 78x) are conditional or unverified.** They rely on 10%-activity synthetic workloads, lowest-latency GPU baselines, or placeholder numbers. NeuroBench exists because of this.
- **Active inference contributes three cheap, testable ideas, not an architecture:** expected-information-gain probe selection (UoT reports +38% task success), surprise as a gate signal (semantic-entropy probes from a single forward pass), and predict-then-verify world models for computer use (WebDreamer: a 7B world model is competitive with GPT-4o).
- **Energy per query varies by about 65x across models and deployments**, and configuration alone can save 40% or more. AWOS's watts term must be *measured* in joules per verified task. A local path is not automatically greener than the cloud.

---

## 2. What matters most (ranked)

1. **Data movement dominates energy, in the brain and on silicon.** Levy & Calvert's audit gives 0.1 W for computation and about 3.5 W for communication (35x). On a local box, decode energy and speed are set by the bytes of weights plus KV cache read per token. At the agent level, the expensive "communication" is re-sent prompts, bloated tool output, and cloud round-trips. NorthPole's 72.7x claim (self-reported, against a lowest-latency GPU) comes from the same effect: 4-bit weights resident next to compute. — [PNAS 2021](https://doi.org/10.1073/pnas.2008173118), [IBM NorthPole LLM](https://research.ibm.com/blog/northpole-llm-inference-results)

2. **Sparse default path, escalate on surprise.** Lennie: energy limits active cortical neurons to under 1%, so the brain must route energy by task demand. That is the biological argument for the Gatekeeper cascade (replay → small model → cloud). Mixture-of-Depths (up to 50% faster sampling) and LayerSkip (1.82x on coding) apply the same principle inside a model. MoE models (30B-A3B class) are the deployable form of activation sparsity. — [Lennie 2003](https://pubmed.ncbi.nlm.nih.gov/12628181/), [LayerSkip](https://arxiv.org/abs/2404.16710), [MoD](https://arxiv.org/abs/2404.02258)

3. **Measure joules per verified task.** Google reports 0.24 Wh for a median Gemini prompt (first-party). "How Hungry is AI?" estimates 0.42 to over 29 Wh per prompt across 30 models, about 65x. ML.ENERGY shows configuration alone can save 40% or more. A 30-step agent episode on a cloud frontier model plausibly lands in the tens of Wh. That is in the same range as a Mac mini at 30–60 W for a few minutes, so local wins on watts only when replay or the small model succeeds quickly. — [Google 2025](https://arxiv.org/abs/2508.15734), [How Hungry](https://arxiv.org/abs/2505.09598), [ML.ENERGY](https://arxiv.org/abs/2505.06371)

4. **Cheap uncertainty from hidden states as an escalation trigger.** Semantic Entropy Probes approximate semantic entropy, which normally costs 5–10x compute from sampling, using one generation's hidden states at near-zero overhead. Only a local model exposes hidden states, so this signal is structurally local-first. — [SEP](https://arxiv.org/abs/2406.15927)

5. **Choose diagnostic actions by expected information gain.** UoT reports +38.1% average task completion across LLMs with fewer questions. BED-LLM (ICLR 2026) puts it on a principled Bayesian-experimental-design footing. Both map directly onto Gatekeeper's bounded-repair step: choose the next test, file read, or state probe that best separates competing hypotheses. — [UoT](https://arxiv.org/abs/2402.03271), [BED-LLM](https://arxiv.org/abs/2508.21184)

6. **Predict-then-verify for computer use.** In WebDreamer, an LLM world model simulates action outcomes before acting. It is 4–5x more efficient than tree search on VisualWebArena, and its Dreamer-7B is reported comparable to GPT-4o and works on live sites (self-reported). A mismatch between the predicted and observed screen is a natural repair or escalation trigger. — [WebDreamer](https://arxiv.org/abs/2411.06559)

7. **Surprise-segmented episodic memory.** EM-LLM splits streams at high Bayesian surprise, beats InfLLM, matches RAG at similar compute up to 10M tokens, and needs no fine-tuning (self-reported). It gives "memory only from strong evidence" a concrete segmentation rule. — [EM-LLM](https://arxiv.org/abs/2407.09450)

8. **An energy-proportional, event-driven always-on host.** For an always-on box, most watts are idle watts. The brain wakes on input instead of polling, and consolidates memory offline. AWOS's prior notes record a Mac mini M4 Pro at 4–5 W idle versus 22–25 W for DGX Spark. Sleep-time compute fits batched consolidation windows. — [Sleep-time compute](https://arxiv.org/abs/2504.13171)

9. **Low-bit and linear-attention models for the routine tier.** MatMul-free ternary LMs scale to 2.7B with over 10x lower inference memory. bitnet.cpp gives 1.37–6.17x CPU speedups. Hybrid linear attention is what makes SpikingBrain fast at long context. All of this can be tested today on commodity hardware, judged by verification-gate pass rate. — [MatMul-free](https://arxiv.org/abs/2406.02528), [bitnet.cpp](https://arxiv.org/abs/2410.16144), [SpikingBrain](https://arxiv.org/abs/2509.05276)

10. **Milliwatt event-driven sensing is where neuromorphic hardware wins.** Examples: ABR TSP1 does speech recognition at under 35 mW; Innatera Pulsar; SynSense Speck. These fit a future always-on wake or trigger front-end, not the reasoning model. — [Open Neuromorphic](https://open-neuromorphic.org/neuromorphic-computing/hardware/)

---

## 3. What does NOT matter (hype and dead ends)

| Claim / direction | Why it does not matter for AWOS |
|---|---|
| "The brain runs on 20 W, so biocomputers are a million times more efficient" ([OI roadmap](https://www.frontiersin.org/articles/10.3389/fsci.2023.1017235/full)) | Compares a whole 86B-neuron brain with the Frontier supercomputer. Real devices have under 1M neurons, draw about 30 W/unit mostly for life support, and have no measured task-level energy advantage. |
| "Neurons beat deep RL" ([Khajehnejad et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC12320521/)) | Holds only at a 70-episode budget against untuned DQN/A2C/PPO on Pong. The vendor-authored paper itself says RL overtakes the cultures with more episodes. |
| Organoid sentience or intelligence framing | About 30 senior neuroscientists publicly rejected it ([Balci et al.](https://doi.org/10.1016/j.neuron.2023.02.009)). It is PR and ethics debate, not capability. |
| Doom/Pong demos, the Singapore "biocomputing data center" (20 CL1, 16M neurons) | No baselines or controls. Tens of electrodes per dish is a research-access service, not compute. |
| Organoid reservoir computing (Brainoware) | The organoid acts as a random nonlinear feature map. An echo-state network or random projection on silicon does the same at almost no cost. |
| Hala Point "15 TOPS/W", "owl-brain scale" ([Intel](https://www.intel.com/content/www/us/en/newsroom/news/intel-builds-worlds-largest-neuromorphic-system.html)) | Valid only for a synthetic MLP with 10:1 sparse connectivity and 10% activity. 2.6 kW, research-only, and dense transformer decoding does not behave this way. |
| Spiking LLMs as an energy breakthrough | On GPUs, spikes are integer-quantised activations that run as dense kernels. The energy figures are estimated, and quality drops. |
| Buying neuromorphic hardware for the box in 2026–28 | Loihi is research-only, and Lava's last release was v0.10.0 in Aug 2024 ([repo](https://github.com/lava-nc/lava)). NorthPole is not sold. SpiNNcloud's 18x/78x claims show placeholder numbers ([site](https://spinncloud.com/)). |
| PowerInfer-style activation-sparsity runtimes on current models | The large gains need ReLU-family models. SwiGLU models (Qwen3, Llama 3) have weak natural sparsity, and MoE already captures most of the deployable sparsity. |
| Formal free-energy controller or "active inference OS" | No measured advantage over a bandit plus information-gain heuristic in any LLM-agent setting found. In practice, expected free energy reduces to reward plus a curiosity bonus ([Millidge et al.](https://arxiv.org/abs/2004.08128)). |
| Predictive coding as a training method | Reached 128 layers only in 2025, on simple classification ([μPC](https://arxiv.org/abs/2505.13124)). Scalability is "the central unsolved problem" ([PCX](https://arxiv.org/abs/2407.01163)). |
| VERSES Genius marketing, RGMs | Self-reported (Mastermind vs o1, unverified), and RGMs have no quantitative baselines ([RGMs](https://arxiv.org/abs/2407.20292)). |
| Brain "FLOP-equivalent" targets | Estimates span about 1e13–1e17 FLOP/s (UNVERIFIED, [Carlsmith](https://coefficientgiving.org/research/how-much-computational-power-does-it-take-to-match-the-human-brain/)), and only about 0.1 W is computation. Not usable for engineering decisions. |

---

## 4. Key papers and resources

### Must-read
- **Levy & Calvert, "Communication consumes 35 times more energy than computation in the human cortex" (PNAS 2021)**: https://doi.org/10.1073/pnas.2008173118. The energy audit behind "minimise data movement".
- **Semantic Entropy Probes (Kossen, Gal et al. 2024)**: https://arxiv.org/abs/2406.15927. A near-free uncertainty signal from hidden states, and a candidate local-vs-cloud trigger.
- **Uncertainty of Thoughts**: https://arxiv.org/abs/2402.03271, and **BED-LLM (ICLR 2026)**: https://arxiv.org/abs/2508.21184. Information-gain selection of questions and probes.
- **WebDreamer**: https://arxiv.org/abs/2411.06559. A world model for predict-then-act computer use; the 7B world model is competitive.
- **IBM NorthPole LLM results**: https://research.ibm.com/blog/northpole-llm-inference-results. Memory locality as the real source of efficiency.
- **Measuring AI energy at Google scale**: https://arxiv.org/abs/2508.15734, and **ML.ENERGY Benchmark**: https://arxiv.org/abs/2505.06371. Calibration and tooling for the watts term.

### Useful
- **EM-LLM (ICLR 2025)**: https://arxiv.org/abs/2407.09450. Surprise-based episodic memory.
- **Lennie, "The cost of cortical computation" (2003)**: https://pubmed.ncbi.nlm.nih.gov/12628181/. The under-1% active-neurons limit.
- **Neuromorphic LLM on Loihi 2**: https://arxiv.org/abs/2503.18002. The only hardware-measured mJ/token numbers on Loihi; read the caveats.
- **Scalable MatMul-free LM**: https://arxiv.org/abs/2406.02528, with code at https://github.com/ridgerchu/matmulfreellm.
- **SpikingBrain**: https://arxiv.org/abs/2509.05276. Read the limitations section, which separates linear-attention gains from spike claims.
- **PowerInfer**: https://arxiv.org/abs/2312.12456, **Deja Vu**: https://arxiv.org/abs/2310.17157, **LLM in a flash**: https://arxiv.org/abs/2312.11514. Activation sparsity and flash-resident weights.
- **LayerSkip**: https://arxiv.org/abs/2404.16710 and **Mixture-of-Depths**: https://arxiv.org/abs/2404.02258. Adaptive per-token compute.
- **bitnet.cpp**: https://arxiv.org/abs/2410.16144. Ternary inference on CPU.
- **NeuroBench**: https://arxiv.org/abs/2304.04640. Rules for judging efficiency claims.
- **AXIOM (VERSES)**: https://arxiv.org/html/2505.24784. Gradient-free Bayesian agent with 4–1400x fewer parameters than DreamerV3/BBF on the authors' own benchmark, with candid limitations.
- **CL API (Cortical Labs)**: https://arxiv.org/abs/2602.11632. Deterministic, transactional closed-loop control of a noisy substrate; a design pattern for agent runtimes.
- **pymdp**: https://github.com/infer-actively/pymdp and **RxInfer.jl**: https://github.com/ReactiveBayes/RxInfer.jl. Small belief-state trackers.
- **How Hungry is AI?**: https://arxiv.org/abs/2505.09598. Estimates, not measurements.

### Reference (wetware and context)
- DishBrain (Kagan et al. 2022): https://pmc.ncbi.nlm.nih.gov/articles/PMC9747182/
- Robbins et al., organoid cartpole (Cell Reports 2026): https://doi.org/10.1016/j.celrep.2026.116984
- Bio vs deep RL sample efficiency: https://pmc.ncbi.nlm.nih.gov/articles/PMC12320521/
- OI roadmap (Smirnova et al. 2023): https://www.frontiersin.org/articles/10.3389/fsci.2023.1017235/full
- FinalSpark Neuroplatform: https://www.frontiersin.org/articles/10.3389/frai.2024.1376042/full
- Balci et al. critique: https://doi.org/10.1016/j.neuron.2023.02.009
- Watmuff et al. drug screening: https://doi.org/10.1038/s42003-025-08194-6
- Alam El Din et al., organoid LTP/LTD: https://doi.org/10.1038/s42003-025-08632-5
- Brown & Varghese review (2026): https://doi.org/10.1038/s43588-026-01012-x
- Spichak, "Biocomputing: Beyond the Hype" (2026): https://doi.org/10.2196/100949
- Starting an SBI lab (Patterns 2025): https://doi.org/10.1016/j.patter.2025.101232
- Hala Point, NorthPole chip, Loihi video/audio, SpiNNaker2, Akida: see Sources.
- Active inference theory: https://arxiv.org/abs/2004.08128, https://arxiv.org/abs/2407.20292, https://arxiv.org/abs/2412.10425, https://arxiv.org/abs/2311.10215, μPC https://arxiv.org/abs/2505.13124, PCX https://arxiv.org/abs/2407.01163

---

## 5. Implications for AWOS

Mapped to the Gatekeeper pipeline (replay → local small model → verification gate → bounded repair → cloud escalation) and to local computer use.

### Adopt now (cheap, low risk)
- **Joules per verified task as a first-class metric.** Log `powermetrics` (or wall-plug) energy per episode on the M-series host for each path: replay, local small model, local MoE, cloud. Estimate cloud energy per token, calibrated against Google's 0.24 Wh median and the 0.42–29 Wh range. Without this, the "watt" in the objective is a guess. Count retries and failed attempts in the denominator.
- **Minimise data movement in the agent loop.** This principle comes straight from the 35x communication finding. Keep stable prompt prefixes (T7b `AWOS_STABLE_PREFIX`), reuse KV and prefixes on the local server, send diffs instead of whole files, trim tool output, and keep the small model resident and 4-bit (the NorthPole lesson on commodity unified memory).
- **Event-driven, energy-proportional host.** Wake on fs-watch, git hooks, or the inbox instead of polling. Unload the model on idle, and measure the reload latency this adds. Batch consolidation ("sleep") into windows when the box is already awake. Set an idle-power target (for example under 10 W at the wall) and measure it.

### Test next (A/B on the existing issue set)
- **Semantic-entropy-probe escalation gate.** Train a linear probe on the local coder's hidden states (MLX/llama.cpp) to predict verification pass/fail on the 73-issue set. Report AUROC, and dollars and seconds saved by escalating before tests run when the probe says "likely fail". This is the clearest local-only advantage in the territory.
- **Information-gain repair ladder.** Replace the fixed repair order with: sample K hypotheses, then choose the probe (test, file read, log grep) with the highest expected disagreement among them. Measure solve rate per dollar and find where the K-sample cost breaks even.
- **Predictive postconditions for computer use.** Extend T10 postconditions: before an action, a small model predicts the resulting screen or DOM delta, and a mismatch triggers repair or escalation. Compare against raw model confidence as an escalation trigger.
- **Surprise-gated memory writes.** Store routines and episodes only where verification passed *and* the trajectory contained a surprise boundary. Compare routine reuse against storing every success.
- **Routine-tier model architecture.** Evaluate hybrid-linear-attention (Qwen3-Next class) and ternary or low-bit small models for routine steps by gate pass rate and J/episode, against the current local model. Expect MoE to stay the default worker.

### Watch
- Purchasable compute-near-memory accelerators that support 7B+ models (NorthPole-class, SpiNNcloud), once they have independent MLPerf-style tokens/J at a stated batch size.
- Milliwatt event front-ends (Innatera Pulsar, Akida, TSP1) as an always-on trigger for local computer use (audio wake, screen change).
- Independent replications of AXIOM or VERSES results, and any persistent (over 1 day) learning in organoids.

### Ignore
- Wetware or organoid compute, spiking-LLM energy claims, formal active-inference controllers, predictive-coding training, and vendor "Nx vs GPU" multipliers with no measured joules at matched accuracy.

### Transferable design patterns
- **CL API semantics** (declarative, transactional admission, deterministic ordering, explicit sync over a drifting substrate) are a good model for driving unreliable local models and computer-use actions behind a gate.
- **Plasticity without consolidation does not compound.** The organoids forget in 45 minutes. This is a useful argument for writing durable memory only from verified evidence.
- **Report A/B results at the budget actually run.** The 70-episode bio-vs-RL result shows how a comparison flips with budget. AWOS vs Aider should be reported at matched per-issue budgets, with tuned baselines.

### On running more than 8 parallel agents (the triggering user question)
Two pieces of evidence from this territory are relevant:
1. **Shared budgets bind before the agent cap does.** In this run, every scout lost web search because all parallel agents share one 200-call session budget (the scouts cite `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`; not verified here). Adding agents without per-agent or larger shared tool budgets just starves each agent.
2. **Parallelism is cheap only when most units are idle** (the under-1% active-neurons lesson). For AWOS's own runtime, agents that mostly wait on tests and tools can scale well past 8 if they share one continuously batched local model server with a shared prefix cache. If many agents decode at the same moment on one Mac, they saturate memory bandwidth and KV-cache RAM, and extra agents add latency and watts but no throughput. Measure the throughput and energy curve at 8, 16, and 32 concurrent agents before raising any cap.

---

## 6. Open questions worth exploring next

1. What are the measured J/token and J/verified-task for 0.5B, 9B, and 35B-A3B models under MLX on the owner's M-series host at batch 1, and for the cloud path? Is local actually lower energy per *success*?
2. How well (AUROC) does a semantic-entropy probe on the local coder predict gate pass/fail? Does gating on it beat "always run tests, then escalate" on dollars and seconds?
3. Where is the break-even for information-gain probe selection, given the cost of sampling K hypotheses?
4. Can a small local world model predict post-action UI state accurately enough to serve as a computer-use verifier?
5. For long agent transcripts, how much decode energy goes to weight bandwidth versus KV traffic? Would hybrid linear attention save more than further quantisation?
6. What do the throughput, latency, and energy curves look like as concurrent agents go from 8 to 32 on one local server with continuous batching and a shared prefix cache? Which limit binds first: KV RAM, bandwidth, or cloud rate limits?
7. Will any compute-near-memory accelerator that supports 7B+ models become purchasable in 2026–27 with independent benchmarks?
8. Is there any task where biological cultures beat a well-tuned, sample-efficient silicon learner on accuracy per joule, and can culture learning persist beyond 45 minutes?
9. **Re-run with search:** 2025–26 LLM-plus-active-inference work (IWAI 2025), Loihi's roadmap, the Dampfhoffer et al. SNN-energy study, and the paywalled Balci, NorthPole Science, and CL1 platform papers were all unchecked because of the exhausted search budget.

---

## 7. Sources

- https://pmc.ncbi.nlm.nih.gov/articles/PMC9747182/
- https://doi.org/10.1016/j.celrep.2026.116984
- https://pmc.ncbi.nlm.nih.gov/articles/PMC12320521/
- https://en.wikipedia.org/wiki/Cortical_Labs
- https://www.frontiersin.org/articles/10.3389/fsci.2023.1017235/full
- https://www.frontiersin.org/articles/10.3389/frai.2024.1376042/full
- https://doi.org/10.1038/s42003-025-08194-6
- https://doi.org/10.1038/s42003-025-08632-5
- https://arxiv.org/abs/2602.11632
- https://github.com/Cortical-Labs/cl-api-doc/
- https://doi.org/10.1016/j.neuron.2023.02.009
- https://doi.org/10.1038/s41928-023-01069-w
- https://corticallabs.com/research
- https://doi.org/10.1038/s43588-026-01012-x
- https://doi.org/10.2196/100949
- https://doi.org/10.1016/j.patter.2025.101232
- https://arxiv.org/abs/2503.18002
- https://research.ibm.com/blog/northpole-llm-inference-results
- https://research.ibm.com/blog/northpole-ibm-ai-chip
- https://www.intel.com/content/www/us/en/newsroom/news/intel-builds-worlds-largest-neuromorphic-system.html
- https://arxiv.org/abs/2509.05276
- https://arxiv.org/abs/2406.02528
- https://github.com/ridgerchu/matmulfreellm
- https://github.com/lava-nc/lava
- https://spinncloud.com/
- https://arxiv.org/abs/2401.04491
- https://open-neuromorphic.org/neuromorphic-computing/hardware/
- https://brainchip.com/akida-generations/
- https://arxiv.org/abs/2310.03251
- https://arxiv.org/abs/2304.04640
- https://doi.org/10.1073/pnas.2008173118
- https://doi.org/10.1073/pnas.2107022118
- https://pubmed.ncbi.nlm.nih.gov/12628181/
- https://arxiv.org/abs/2312.12456
- https://arxiv.org/abs/2310.17157
- https://arxiv.org/abs/2312.11514
- https://arxiv.org/abs/2404.02258
- https://arxiv.org/abs/2404.16710
- https://arxiv.org/abs/2508.15734
- https://arxiv.org/abs/2505.06371
- https://arxiv.org/abs/2505.09598
- https://arxiv.org/abs/2410.16144
- https://coefficientgiving.org/research/how-much-computational-power-does-it-take-to-match-the-human-brain/
- https://arxiv.org/abs/2504.13171
- https://arxiv.org/abs/2402.03271
- https://arxiv.org/abs/2508.21184
- https://arxiv.org/abs/2406.15927
- https://arxiv.org/abs/2407.09450
- https://arxiv.org/html/2505.24784
- https://arxiv.org/abs/2505.13124
- https://arxiv.org/abs/2407.01163
- https://arxiv.org/abs/2411.06559
- https://arxiv.org/abs/2412.10425
- https://arxiv.org/abs/2311.10215
- https://arxiv.org/abs/2004.08128
- https://arxiv.org/abs/2407.20292
- https://github.com/infer-actively/pymdp
- https://github.com/ReactiveBayes/RxInfer.jl

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

*Method: arXiv search pages and abs pages, Hacker News Algolia, GitHub API, Hugging Face API. The arXiv export API and Semantic Scholar returned HTTP 429, so arXiv coverage comes from arxiv.org/search result pages (newest first) plus abs-page abstracts. Paywalled and vendor pages were not read in full. arXiv IDs below are as returned by those pages.*

### New since the chart (dated, with URLs; most important first)

1. **Hidden-state probes for early agent abort, with a recall guarantee (2026-07).** "Doomed from the Start" trains linear probes on internal activations that predict agent-episode failure from the first interaction round, then builds a calibrated abort cascade. It saves 1.5-8.8x more compute than the best single-gate baseline at a 90% recall target, on TextCraft and WebShop with 3B/7B/1.7B models. https://arxiv.org/abs/2607.06503. *Why it matters:* this is direct support for the chart's "semantic-entropy-probe escalation gate" test, but for agent episodes rather than single answers. It supplies a method (recall-controlled cascade) for the "never abort a run that would have passed" constraint. Read it before designing the probe experiment.
2. **Negative result for semantic entropy probes on code (2026-07-31).** Of five uncertainty methods on three small code LLMs, multi-sample P(True) correlated best with correctness and semantic entropy probes gave only weak correlation. Uncertainty-driven self-correction lowered Pass@1 in 5 of 6 configurations (-3 to -10 pp); only verification-based correction reliably helped (+6 to +26 pp on HumanEval). https://arxiv.org/abs/2608.14659. *Why it matters:* this weakens the chart's "clearest local-only advantage" framing for SEP on coding. The probe should be trained on pass/fail of the AWOS verification gate (as the chart proposes), not assumed to transfer from NL semantic entropy. The result also backs AWOS's verify-first design.
3. **Agent inference energy is far worse than per-prompt figures (2026-08-31).** Profiling on 2x RTX PRO 6000 Blackwell: GPU-only telemetry misses 41-45% of system energy, and a sequential agent workload uses 63x more system energy per output token than saturated serving (no batching, context growth, tool idle time). https://arxiv.org/abs/2609.29707. HN also carries a Bloomberg item, "Open-weight AI agents can use 10k x more energy than simple queries" (2026-09-04, paywalled, not read): https://news.ycombinator.com/item?id=49561361. *Why it matters:* the chart's "tens of Wh per 30-step episode" is an unmeasured extrapolation from per-prompt numbers. Measure at the wall (not GPU or SoC counters alone) and treat batching and idle time as first-class terms.
4. **Multi-agent energy cost in software-engineering tasks (2026-10-02).** Multi-agent designs used on average 6.36x the energy and 6.07x the time of non-agentic baselines; single-agent or non-agentic setups made up 59 of 66 Pareto-optimal configurations. https://arxiv.org/abs/2610.03010. *Why it matters:* supports one excellent worker (Stage 1) and a joules-per-verified-task metric.
5. **Measured-energy routing (2026-09-19).** A learned router trained on per-query measured latency, power and GPU energy across a model pool improves the accuracy-energy tradeoff, with a sharp "phase transition" among routers. https://arxiv.org/abs/2609.23085. *Why it matters:* a published recipe for energy-aware routing in the Gatekeeper cascade, using measured joules as training signal.
6. **"Sparsity Ceiling" for spiking nets (2026-07-29).** Event-driven sparsity pays off by task: feed-forward perception sparsifies to 5% firing, but a recurrent LM cannot go below about 50%; attention sparsifies but pays with a KV-cache memory wall. https://arxiv.org/abs/2607.26648. *Why it matters:* independent support for the chart's "spiking LLM energy claims do not transfer" row.
7. **Spiking and neuromorphic LLM papers keep arriving, still with proxy energy (2026-08/09).** TTFS spiking LLM at 1.5B explicitly reports a "spike-count proxy ... rather than a measurement on neuromorphic hardware" (https://arxiv.org/abs/2609.05151). An event-driven sparse linear-attention model *projects* 37x throughput and 16x lower power vs an edge GPU (https://arxiv.org/abs/2608.30439). SymbolicLight V2 gives FPGA numbers (0.044 J/token estimated, 82.8% of gross card energy is loaded idle; https://arxiv.org/abs/2609.09772). *Why it matters:* the chart's "estimated, not measured" caution holds. Idle-power dominance echoes the chart's energy-proportional host point.
8. **NorthPole scale-out paper (2025-11-19, after the chart's cited blog).** 288 cards in 18 servers, 30 kW, 115 peta-ops int4, runs 3 instances of an 8B model with 28 users at 2.8 ms inter-token latency. https://arxiv.org/abs/2511.15950. *Why it matters:* the chart's "NorthPole serves 3B at under 1 ms/token on 16 cards" is the small configuration; the scale-out is a 30 kW research prototype, so the "not buyable, not for the box" verdict stands.
9. **Active inference as context acquisition for agents (2026-08-23 on HN).** Frames clarifying questions and tool calls as expected-information-gain decisions under token cost; benchmarks frontier models on optimal question asking. https://arxiv.org/abs/2608.19202 (HN: https://news.ycombinator.com/item?id=49405247). *Why it matters:* same family as UoT and BED-LLM and consistent with the chart's "information gain, not a free-energy controller" stance. No coding-agent evidence.
10. **Cortical Labs productised access.** "Cortical Cloud" page (HN 2026-05-13) advertises deploying code to CL1 units from Jupyter via a Python SDK: https://corticallabs.com/cloud, with an open `cl-sdk` repo (last push 2026-06-11): https://github.com/Cortical-Labs/cl-sdk. A 2026-04-30 survey of "synthetic biological intelligence" notes the lack of commercial platforms until cloud-integrated BNNs: https://arxiv.org/abs/2604.27933. *Why it matters:* cheaper experimentation, but the page makes qualitative claims only, so the "ignore wetware" call stands.
11. **Lava still stale.** Latest release v0.10.0 (2024-08-08); repo last pushed 2026-05-13. Source: `gh api repos/lava-nc/lava`. *Why it matters:* confirms the chart's "no Loihi software momentum" point.

### Corrections (chart claim -> source)

- Chart: Loihi 2 result is "370M model at 405 mJ/token ... about 2-3x better than a Jetson Orin Nano." -> The arXiv abstract (https://arxiv.org/abs/2503.18002) states "up to 3x higher throughput with 2x less energy" based on "preliminary results". The 405 mJ/token figure is not in the abstract and was not re-checked in the body. Treat it as unverified.
- Chart: UoT's "+38%" -> source says "average performance improvement of 38.1% in the rate of successful task completion" on medical diagnosis, troubleshooting and 20 Questions (https://arxiv.org/abs/2402.03271). The chart's mapping to coding-repair is an extrapolation; none of those tasks involve code.
- Chart: Google "0.24 Wh median Gemini prompt" is right, but omits the paper's year-over-year claim of a 33x energy reduction (https://arxiv.org/abs/2508.15734). The figure is a moving first-party median and not a stable benchmark.
- Chart: SEP described as the "clearest local-only advantage" -> see item 2; independent code-generation evidence is negative for SEPs specifically.
- Other corrections: none found.

### Confirmed claims (briefly)

- UoT 38.1% average completion gain: confirmed (abstract).
- Google 0.24 Wh median text prompt: confirmed (abstract).
- WebDreamer: "competitive, while being 4-5 times more efficient, with tree search" on VisualWebArena and works on real sites: confirmed (https://arxiv.org/abs/2411.06559). Follow-on world-model web agent work (https://arxiv.org/abs/2602.15384) reports only small absolute gains (+1.8% VisualWebArena), so treat predict-then-verify gains as modest.
- SpikingBrain is a linear/hybrid-linear model with spiking neurons, trained on MetaX GPUs, not a neuromorphic-hardware result: confirmed (https://arxiv.org/abs/2509.05276).
- Lava's last release v0.10.0 in Aug 2024: confirmed via GitHub API.
- The PNAS Levy and Calvert "35x" claim: the Crossref abstract was retrieved but its text did not state the ratio, so this was not independently re-checked here; its Significance statement says a neuron's computation cost is off the best possible bits per joule by about 10^8.

### Still unverified

- NorthPole "72.7x" and the "under 1 ms/token on 16 cards" figures (IBM blog not re-fetched), Hala Point 15 TOPS/W, SpiNNcloud 18x/78x.
- The "45-minute" organoid forgetting replication, the Cell Reports 2026 paper, CL1 price and 30 W figures, and the Singapore deployment (no primary source re-read).
- EM-LLM, BED-LLM, MoD and LayerSkip numbers; the SpikingBrain MMLU gap of 8-10 points.
- Bloomberg's "10k x" agent-energy claim (paywalled; only HN title seen).
- Whether any 2026 paper replicates AXIOM or VERSES results. No arXiv hit appeared in the searches run, but searches were few and the export API was rate limited.
- No new purchasable compute-near-memory accelerator was found for 7B+ models.
