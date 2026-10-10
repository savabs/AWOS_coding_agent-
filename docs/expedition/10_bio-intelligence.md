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
