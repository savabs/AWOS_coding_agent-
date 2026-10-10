# Mathematics of decisions, psychology and cognition

> Expedition chart 14. Territory: decision theory, mathematical psychology, metacognition/calibration, resource-rational analysis — as applied to Gatekeeper's act / retry / ask / escalate decisions.
> Method caveat: the session's shared WebSearch budget (200 calls) was exhausted before or early during all four scout runs. Findings rest on directly fetched arXiv/ACL/PMC abstract pages plus canonical literature cited from memory (marked *[not re-fetched]*). All numbers are author-reported, from abstracts; none were reproduced. 2026 work is under-sampled.

---

## 1. Summary

- **Gatekeeper is already a selective-prediction cascade; the math for its knobs exists and is mature.** Tier order (Pandora's box / cost-of-pass), gate thresholds (conformal risk control), retry counts (optimal stopping / SPRT), and routing exploration (Thompson sampling) all have closed-form or near-closed-form rules computable from data AWOS already logs.
- **Executable verification dominates self-assessment.** Verbalized confidence predicts failure at AUROC ~0.52–0.61 ([Xiong et al.](https://arxiv.org/abs/2306.13063)); LLM judges show cognitive biases in ~40% of comparisons ([CoBBLEr](https://arxiv.org/abs/2309.17012)) and prefer their own outputs ([Panickssery et al.](https://arxiv.org/abs/2404.13076)). Tests and state probes stay the primary signal.
- **Calibration is learnable and cheap, but not by prompting.** ~1,000 graded examples plus a LoRA/probe beat prompting baselines ([Kapoor et al.](https://arxiv.org/abs/2406.08391)). AWOS's gate ledger produces those labels for free.
- **Local weights are a structural advantage for metacognition.** Hidden-state probes and logit-based early exit (24% fewer tokens, [Zhang et al.](https://arxiv.org/abs/2504.05419); 19–80% shorter CoT, [DEER](https://arxiv.org/abs/2504.15895)) are only possible when you own the model. Cloud APIs cannot offer this.
- **But probes and thresholds don't transfer.** Error detectors fail across datasets ([Orgad et al.](https://arxiv.org/abs/2410.02707)); P(IK) loses calibration on new tasks ([Kadavath et al.](https://arxiv.org/abs/2207.05221)). Everything must be per (model version, task class) with recalibration.
- **Adaptive stopping beats fixed N everywhere.** 2–8x sample savings at <0.1% accuracy loss ([Adaptive-Consistency](https://arxiv.org/abs/2305.11860), [ESC](https://arxiv.org/abs/2401.10480)); overthinking on SWE-bench lowers solve rate, and selecting low-overthinking trajectories gives ~+30% solved / −43% cost ([Cuadron et al.](https://arxiv.org/abs/2502.08235)).
- **"Ask the owner" is a high-value but badly triggered action.** Interaction lifts underspecified SWE tasks by up to 74%, yet models can't tell when a task is underspecified ([Ambig-SWE](https://arxiv.org/abs/2502.13069)); reasoning fine-tuning degrades abstention by 24% ([AbstentionBench](https://arxiv.org/abs/2506.09038)). Triggers must be structural, not self-judged.
- **Psychology's transferable content is math, not metaphor.** DDM/SPRT stopping, value of computation, prospect-theory loss shapes (KTO), and habit/deliberation arbitration transfer. "System 1/System 2" branding, active inference, and bias catalogues do not change designs.

---

## 2. What matters most (ranked)

**1. Tier order = ascending cost per verified pass (Pandora's box / Cost-of-Pass).**
Treat each tier (replay, local, local+repair, cloud) as a box with cost c (dollars, seconds and Wh folded into one number) and verified-success probability p. Weitzman's reservation index reduces, for binary payoff, to: open in ascending c/p and stop at the first verified pass *[Weitzman 1979, not re-fetched; reduction derived by scout]*. Cost-of-Pass defines the same quantity, "expected monetary cost of generating a correct solution", and finds light models win on basic tasks and reasoning models on complex ones. **Caveat:** tiers are correlated (hard tasks fail everywhere), so p_cloud must be estimated *conditional on local failing*. Source: https://arxiv.org/abs/2504.13359

**2. Gate/escalation thresholds via conformal risk control, with a finite-sample guarantee.**
Choose the accept threshold on a labelled calibration set so expected false-accept ≤ α, tight to O(1/n) ([Conformal Risk Control](https://arxiv.org/abs/2208.02814)). Trust or Escalate guarantees >80% human agreement at ~80% coverage using cheap models first ([Jung et al.](https://arxiv.org/abs/2407.18370)); Conformal LM calibrates stop and reject rules for sampling ([Quach et al.](https://arxiv.org/abs/2306.10193)); KnowNo asks for help when the conformal set holds more than one plan ([Ren et al.](https://arxiv.org/abs/2307.01928)). Valid only under exchangeability, so it must be per class and recalibrated after model swaps.

**3. Retry vs escalate is optimal stopping, and adaptive stopping dominates fixed N.**
With a real verifier, coverage grows log-linearly with samples: SWE-bench Lite went from 15.9% at 1 sample to 56% at 250 ([Large Language Monkeys](https://arxiv.org/abs/2407.21787)). Difficulty-adaptive compute is more than 4x more efficient than best-of-N, and a small model beats a 14x larger one, *but only where the small model already has non-trivial success* ([Snell et al.](https://arxiv.org/abs/2408.03314)). Rule: keep sampling locally while P(pass next | k failures)/c_local > p_cloud/c_cloud, using the per-class Beta posterior. This is a two-boundary DDM/SPRT (accept vs escalate) *[Bogacz et al. 2006, https://doi.org/10.1037/0033-295X.113.4.700, not re-fetched]*.

**4. Pre-attempt difficulty prediction (skip doomed local attempts).**
Snell's boundary implies that when the predicted local pass rate is ~0, local compute and repair are pure waste. Learned marginal-benefit predictors cut compute by up to 50% at no quality loss, or add up to 10% quality at a fixed budget ([Damani et al.](https://arxiv.org/abs/2410.04707)). Source: https://arxiv.org/abs/2408.03314

**5. A learned deferrer, not one confidence cutoff.**
Confidence-threshold deferral is optimal only in homogeneous cascades. It breaks when the downstream model is a specialist, labels are noisy, or the distribution shifts ([Jitkrittum et al.](https://arxiv.org/abs/2307.02764)). All three hold for AWOS: cloud is not uniformly better than local+replay on owner chores, flaky tests run around 11% (trick book), and owner work drifts. For generative output, mean log-prob is length-biased; learned rules over token-level features and embeddings win ([Gupta et al.](https://arxiv.org/abs/2404.10136)). Google's "Gatekeeper" confidence-tuning loss trains the small model itself to defer ([Rabanser et al.](https://arxiv.org/abs/2502.19335)). Note the name collision with AWOS's architecture.

**6. Owner-specific learned confidence (probes / LoRA on ~1k ledger outcomes).**
About 1,000 graded examples beat prompting baselines ([Kapoor et al.](https://arxiv.org/abs/2406.08391)); confidence tokens beat verbalized confidence and logits for routing ([Self-REF](https://arxiv.org/abs/2410.13284)). Semantic Entropy Probes recover semantic entropy from a single generation instead of 5–10x sampling ([Kossen et al.](https://arxiv.org/abs/2406.15927)). Code models are uncalibrated out of the box; Platt scaling helps, depending on data ([Spiess et al.](https://arxiv.org/abs/2402.02047)).

**7. Value of computation as the unifying objective.**
Pick the computation maximizing E[utility] − cost and stop when no VOC is positive; myopic/learned VOC (BMPS) is near-optimal even counting metareasoning overhead ([Callaway et al.](https://arxiv.org/abs/1711.06892); [Lieder & Griffiths 2020](https://cocosci.princeton.edu/papers/liederresource.pdf)). A VOC-penalized LLM reward cut tokens 20–37% at preserved performance ([De Sabbata et al.](https://arxiv.org/abs/2410.05563)). SMART reports 24% fewer tool calls with +37% performance from selective tool use (abstract-level, via the decision-theory scout). For AWOS: reward = verified success − λ·($ + s + Wh).

**8. Separate "is the task well-posed?" from "is my answer right?".**
Reasoning modes improve correctness calibration (33/36 settings, Yoon et al., per the metacognition scout) but worsen abstention on underspecified inputs by 24% ([AbstentionBench](https://arxiv.org/abs/2506.09038)). Models fabricate missing tool arguments rather than ask (NoisyToolBench, via Ambig-SWE scout). Source: https://arxiv.org/abs/2502.13069

**9. Exploration must be built into the harness.**
LLMs show no directed exploration ([Binz & Schulz](https://arxiv.org/abs/2206.14576)). Greedy routing on a lower confidence bound locks a class to cloud after a few early failures. Thompson sampling on per-class Beta posteriors ([tutorial](https://arxiv.org/abs/1707.02038) *[not re-fetched]*), using idle-time practice as free exploration, fixes that. Online cascade learning matched LLM accuracy at ~90% lower cost on streams ([Nie et al.](https://arxiv.org/abs/2402.04513)).

**10. Reward design must credit abstention and keep calibration.**
Binary-graded scoring teaches models to guess ([Kalai et al.](https://arxiv.org/abs/2509.04664)). Plain RL erodes calibration; adding a Brier term fixes it with no accuracy loss ([RLCR](https://arxiv.org/abs/2507.16806)). KTO learns from binary desirable/undesirable signals and matches DPO from 1B to 30B ([KTO](https://arxiv.org/abs/2402.01306)). That is exactly the gate's pass/fail stream.

**11. Budget awareness and overthinking signals (near-free).**
Agents without budget awareness hit a ceiling; a budget tracker in context improves cost-performance ([BATS](https://arxiv.org/abs/2511.17006)). The overthinking score works as a stuck signal and tie-breaker ([Cuadron et al.](https://arxiv.org/abs/2502.08235)).

**12. Bayesian optimal stopping for AWOS's own evaluations.**
Hierarchical sequential measurement removed 57–97% of planned trials while reaching the same conclusions across 9 settings, with warnings about drift and adaptive item selection ([optstop, 2026](https://arxiv.org/abs/2608.14425)). Directly relevant to underpowered A/Bs such as 64% vs 61% (n.s.) on 28 issues.

---

## 3. What does NOT matter / hype / dead ends

| Thing | Why not |
|---|---|
| Verbalized confidence ("rate 1–10") as a gate | AUROC ~0.52–0.61 and systematic overconfidence ([Xiong](https://arxiv.org/abs/2306.13063)); beaten by probes and confidence tokens ([Kapoor](https://arxiv.org/abs/2406.08391)). Log it, never gate on it raw. |
| A single global confidence threshold | Calibration fails out of distribution ([Kadavath](https://arxiv.org/abs/2207.05221)); thresholds fail under shift ([Jitkrittum](https://arxiv.org/abs/2307.02764)). |
| Full POMDP / exact VOC planning | Intractable over LLM text state ([Rahnev critique](https://pmc.ncbi.nlm.nih.gov/articles/PMC7702215)). Myopic/learned approximations are near-optimal (BMPS). |
| Per-step mid-trajectory routing | Prior AWOS research found poor mid-trajectory AUROC; the VOI of a partial trajectory is low next to a decisive attempt-boundary verifier. Use step-risk aggregation ([SAUP](https://arxiv.org/abs/2412.01033), +20% AUROC) only as a *feature*. |
| Fixed-N self-consistency / best-of-N | Superseded by adaptive stopping. Heavy self-consistency and debate "rarely justify the costs" ([Cost-of-Pass](https://arxiv.org/abs/2504.13359)). |
| Scaling LLM-as-judge / GenRM when tests exist | GenRM needs up to 8x compute just to match self-consistency ([Singhi et al.](https://arxiv.org/abs/2504.01005)). Spend on attempts or executable checks. |
| EIG question simulation for open-ended coding | UoT's +38.1% ([UoT](https://arxiv.org/abs/2402.03271)) comes from enumerable hypothesis spaces; simulating futures for each question is too costly for routine work. |
| "System 1/2" prompts and branding | Behavior is fragile to small perturbations ([Binz & Schulz](https://arxiv.org/abs/2206.14576)); SwiftSage's switch was heuristic, with no cost metrics reported ([SwiftSage](https://arxiv.org/abs/2305.17390)). The learned, measured gate is the contribution. |
| Active inference / free-energy as architecture | No 2023–2026 evidence found (searches were unavailable) that it beats Bayesian decision theory or RL on agent benchmarks; its useful content is already in VOC and stopping rules. |
| LLM introspection as a basis for metacognition | ~20% success at best; "failures of introspection remain the norm" ([Anthropic 2025](https://transformer-circuits.pub/2025/introspection/index.html)). |
| Generic "truthfulness direction" probes | Do not generalize across datasets ([Orgad](https://arxiv.org/abs/2410.02707)). |
| ECE as headline metric | Decisions are thresholded; measure false-accept at the operating point and AURC. |
| Aleatoric/epistemic decomposition for agents | It loses meaning in interactive settings (Kirchhof et al., ICML 2025 position paper, per the scout); underspecification is the actionable axis. |
| Bigger model = knows its limits | Scale gives minimal abstention benefit ([AbstentionBench](https://arxiv.org/abs/2506.09038)). |
| Literal cognitive-model ports (Thinker-DDM, Centaur) | Lightly validated ([Thinker-DDM](https://aclanthology.org/2025.alta-main.4/)) or aimed at predicting humans ([Centaur](https://arxiv.org/abs/2410.20268)). Take the math, leave the simulation. |
| Math-benchmark when-to-think RL as proof for agents | Thinkless/AdaptThink/DEER gains are on GSM8K/MATH/AIME. Agentic transfer is shown only by Cuadron (selection, not training) and Paglieri (Crafter). Treat it as a hypothesis. |
| RL-trained when-to-ask policies now | ReHAC ([link](https://arxiv.org/abs/2402.12914)) needs human-agent collaboration logs AWOS does not yet have. |

---

## 4. Key papers and resources

### Must-read
- **Cost-of-Pass**: https://arxiv.org/abs/2504.13359. Expected cost per correct solution; the operational form of Pandora tier ordering.
- **Conformal Risk Control**: https://arxiv.org/abs/2208.02814. Recipe for a false-accept-bounded gate threshold. Learn-then-Test (arXiv 2110.01052 *[not re-fetched]*) covers multiple thresholds.
- **Trust or Escalate**: https://arxiv.org/abs/2407.18370. The closest template for a guaranteed accept-or-escalate cascade.
- **When Does Confidence-Based Cascade Deferral Suffice?**: https://arxiv.org/abs/2307.02764. When a simple threshold is enough and when a learned deferrer is needed.
- **Large Language Models Must Be Taught to Know What They Don't Know**: https://arxiv.org/abs/2406.08391. The ~1k-label data requirement for a confidence head.
- **Scaling LLM Test-Time Compute Optimally**: https://arxiv.org/abs/2408.03314. The local-vs-cloud boundary: small model plus compute wins only at moderate difficulty.
- **Large Language Monkeys**: https://arxiv.org/abs/2407.21787. Coverage-vs-samples curves with and without verifiers; the inputs to the retry stopping rule.
- **Adaptive-Consistency**: https://arxiv.org/abs/2305.11860. Training-free stopping, up to 7.9x savings, including on code.
- **The Danger of Overthinking (SWE-bench)**: https://arxiv.org/abs/2502.08235. Cognitive economy measured in agentic coding; open trajectories.
- **Ambig-SWE**: https://arxiv.org/abs/2502.13069. Ask-vs-act measured on SWE tasks.
- **AbstentionBench**: https://arxiv.org/abs/2506.09038. Reasoning fine-tuning damages abstention.
- **LLMs Know More Than They Show**: https://arxiv.org/abs/2410.02707. The key negative result: probes do not transfer.

### Useful
- Language Model Cascades: Token-level Uncertainty: https://arxiv.org/abs/2404.10136
- Gatekeeper: confidence tuning for cascades (Google): https://arxiv.org/abs/2502.19335
- Semantic Entropy Probes: https://arxiv.org/abs/2406.15927
- Reasoning Models Know When They're Right (probe early exit, −24% tokens): https://arxiv.org/abs/2504.05419
- DEER, training-free early exit: https://arxiv.org/abs/2504.15895
- DeepConf (up to 84.7% fewer tokens, self-reported): https://arxiv.org/abs/2508.15260
- Self-certainty Best-of-N: https://arxiv.org/abs/2502.18581
- ESC, early-stopping self-consistency: https://arxiv.org/abs/2401.10480
- Self-Calibration (confidence distilled into one pass): https://arxiv.org/abs/2503.00031
- Conformal Language Modeling: https://arxiv.org/abs/2306.10193
- KnowNo, Robots That Ask For Help: https://arxiv.org/abs/2307.01928
- Self-REF confidence tokens: https://arxiv.org/abs/2410.13284
- Learning How Hard to Think: https://arxiv.org/abs/2410.04707
- Rational Metareasoning for LLMs: https://arxiv.org/abs/2410.05563
- BMPS, Learning to select computations: https://arxiv.org/abs/1711.06892
- BATS, budget-aware tool use: https://arxiv.org/abs/2511.17006
- When To Solve, When To Verify: https://arxiv.org/abs/2504.01005
- Online Cascade Learning: https://arxiv.org/abs/2402.04513
- KTO: https://arxiv.org/abs/2402.01306
- RLCR (Brier-augmented RL): https://arxiv.org/abs/2507.16806
- Why Language Models Hallucinate: https://arxiv.org/abs/2509.04664
- Knowing When to Stop (Bayesian eval stopping, 2026): https://arxiv.org/abs/2608.14425
- Learning When to Plan: https://arxiv.org/abs/2509.03581
- Uncertainty of Thoughts: https://arxiv.org/abs/2402.03271 · BED-LLM: https://arxiv.org/abs/2508.21184

### Reference
- Lieder & Griffiths, Resource-rational analysis: https://cocosci.princeton.edu/papers/liederresource.pdf (DOI https://doi.org/10.1017/S0140525X1900061X); critique: https://pmc.ncbi.nlm.nih.gov/articles/PMC7702215
- Bogacz et al., Physics of optimal decision making (DDM = SPRT): https://doi.org/10.1037/0033-295X.113.4.700 *[not re-fetched]*
- Daw, Niv & Dayan, uncertainty-based arbitration: https://www.nature.com/articles/nn1560 *[not re-fetched]*
- Shenhav et al., expected value of control: https://doi.org/10.1016/j.neuron.2013.07.007 *[not re-fetched]*
- Thompson Sampling tutorial: https://arxiv.org/abs/1707.02038 *[not re-fetched]*
- Know Your Limits (abstention survey): https://arxiv.org/abs/2407.18418
- Stop Overthinking survey: https://arxiv.org/abs/2503.16419
- LM-Polygraph UQ benchmark: https://arxiv.org/abs/2406.15627
- Kadavath et al., LMs (Mostly) Know What They Know: https://arxiv.org/abs/2207.05221
- Xiong et al., confidence elicitation benchmark: https://arxiv.org/abs/2306.13063
- Thermometer / code calibration (Spiess et al.): https://arxiv.org/abs/2402.02047
- CoBBLEr: https://arxiv.org/abs/2309.17012 · Self-preference: https://arxiv.org/abs/2404.13076
- SAUP: https://arxiv.org/abs/2412.01033 · Conformal factuality: https://arxiv.org/abs/2402.10978 · ReHAC: https://arxiv.org/abs/2402.12914
- s1 budget forcing: https://arxiv.org/abs/2501.19393 · Do NOT Think That Much: https://arxiv.org/abs/2412.21187
- SwiftSage: https://arxiv.org/abs/2305.17390 · Binz & Schulz: https://arxiv.org/abs/2206.14576 · Centaur: https://arxiv.org/abs/2410.20268 · Thinker-DDM: https://aclanthology.org/2025.alta-main.4/

---

## 5. Implications for AWOS

Mapped onto the Gatekeeper ladder: **replay → local attempt → gate → bounded repair → cloud escalation**, plus a new **ask-owner** arm.

### Adopt now (no training; data already logged)
1. **Per-class c/p tier ordering.** For each task class, keep Beta posteriors of verified pass per tier and cost per attempt (with $, s and Wh converted to one scalar). Order tiers by ascending c/p. Estimate p_cloud from tasks where local had already failed. The trick book's "escalation as free experiment" is the data source.
2. **Adaptive stopping in bounded repair and local pass@k.** Replace fixed N with: stop on a gate pass; escalate when P(pass next | k fails)/c_local < p_cloud/c_cloud. Add Adaptive-Consistency-style agreement stopping where there is no test.
3. **Conformal accept threshold.** On the ledger, choose the threshold so that false-accept ≤ α (for example 5%) per (model version, task class). Recalibrate on a rolling window. Report false-accept at the operating point and AURC, not ECE.
4. **Thompson sampling instead of greedy routing.** Prevent classes from locking to cloud, and schedule idle-time practice on the classes with the highest posterior uncertainty. The existing `strategy_weights.json` is the place to start.
5. **Budget tracker in the agent prompt** (remaining $, seconds, steps), and an **overthinking/action ratio** as a free stuck-and-escalate signal.
6. **Structural ask-owner triggers.** Missing required arguments, more than one candidate surviving the gate (KnowNo-style), conflicting acceptance criteria, or an irreversible sink (send, delete, pay). Price each question by owner latency, and consider batching questions into a digest while the agent proceeds on reversible best guesses.
7. **Bayesian sequential stopping for AWOS's own A/Bs** (pre-registered), to stop burning runs on underpowered comparisons.

### Test next (A/B on the 73-issue set and the ledger)
- **Hidden-state probe on the local model** (MLX, 4-bit) at the end of each SEARCH/REPLACE block. Compare it against edit size, static-gate warnings and token-entropy quantiles. Measure AUROC for predicting gate pass. This is a local-only advantage.
- **Pre-attempt difficulty predictor.** Skip local when predicted p_local ≈ 0, and measure net $·s·Wh saved.
- **Small learned deferrer** (logistic or GBDT) over gate signals, probe score, token-entropy quantiles, files touched and step-risk aggregates (SAUP-like), against the c/p rule alone.
- **Thinking on/off measured on both axes:** correctness calibration *and* underspecification detection.
- **Logit/confidence-based early abort** of local generations (DEER/DeepConf style), measuring seconds and Wh saved.
- **Habit arbitration for replay.** Gate replay on tracked reliability. On surprise (state-probe mismatch, a changed file hash, a UI-tree diff), fall back to the deliberate path and lower the routine's trust.
- **Dynamic pricing of local compute** (idle ≈ electricity only; owner active = latency plus contention), following the opportunity-cost model of effort.

### Watch (in scope, unproven for coding or computer use)
- **KTO fine-tuning** of the local model on binary gate outcomes. Any self-training reward must include a **Brier/calibration term** (RLCR) and credit correct escalation, or the worker learns to guess and routing silently breaks.
- **Learned think/plan switches** (Thinkless, AdaptThink, Paglieri) and a VOC-penalized reward (De Sabbata) with reward = success − λ·($+s+Wh).
- **Gatekeeper confidence-tuning loss** (Rabanser) for the local worker.
- **EIG question selection** only for small discrete computer-use ambiguities (which window, which file, which account).
- **Conformal back-off for memory writes:** store the weaker claim the evidence certifies, which turns "memory only from strong evidence" into a formal rule.

### Ignore
Verbalized confidence as a gate, global thresholds, POMDP solvers, per-step routing as a decision point, LLM-judge scaling, System-1/2 prompts, active-inference architectures, and introspection.

### On the relayed question (running more than 8 agents in parallel)
This territory bears on it in two ways. (a) **The expedition's own binding constraint was not the agent count.** All four scouts report that the shared 200-call WebSearch budget ran out before or early in their runs, so later agents worked without search. Raising concurrency without raising per-session tool quotas (the scouts name `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`) and budgeting each agent's share (BATS logic applied to the harness itself) would just starve more agents. (b) **For parallel attempts inside AWOS**, the evidence says the right number is adaptive, not a fixed cap. Adaptive-Consistency, ESC and DeepConf show the marginal (k+1)-th sample has rapidly falling value once candidates agree. Launch more parallel attempts while candidates disagree or confidence is low, and stop early otherwise.

---

## 6. Open questions worth exploring next

1. **Tier correlation:** what is P(cloud passes | local failed the gate) per class? The Pandora ordering is only optimal with this conditional, and current logs may not record both arms on the same task.
2. **Conformal sample size under label noise:** how many jobs per class does an α=5% false-accept bound need when hidden-test labels are ~11% flaky? Kapoor's ~1k figure is for QA, not coding or desktop tasks.
3. **Exchangeability under drift:** which window and recalibration cadence (weighted or adaptive conformal) keeps guarantees honest as repos, routines and the local model change?
4. **Probe quality on a quantized local MoE:** does a residual-stream probe on a 4-bit Qwen3.6-35B-A3B predict patch pass/fail? Quantization effects are unstudied in the sources found.
5. **Risk-sensitive gating for irreversible computer-use sinks:** CVaR or worst-case gates rather than expected-utility thresholds. No 2024–2026 agent paper testing this was found (search was unavailable).
6. **Cost of an owner question:** latency-weighted or attention-weighted? Daily digest with reversible defaults?
7. **Exchange rate inside VOC:** how to convert $, s and Wh into one scalar, and should it vary with owner presence?
8. **Bandit sample efficiency:** is a contextual bandit over {replay, local, local+repair, cloud} viable at tens of tasks per week, or does it need pooled cross-owner priors?
9. **Does the overthinking score** predict failure for small local models as well as for frontier reasoners?
10. **Coverage gap:** a follow-up search pass is needed for 2026 work on agent abstention, ask-vs-act benchmarks and bandit routers. All four scouts were search-starved.

---

## 7. Sources

- https://arxiv.org/abs/2504.13359
- https://arxiv.org/abs/2208.02814
- https://arxiv.org/abs/2307.01928
- https://arxiv.org/abs/2307.02764
- https://arxiv.org/abs/2404.10136
- https://arxiv.org/abs/2406.08391
- https://arxiv.org/abs/2502.13069
- https://arxiv.org/abs/2402.03271
- https://arxiv.org/abs/2508.21184
- https://arxiv.org/abs/2407.21787
- https://arxiv.org/abs/2408.03314
- https://arxiv.org/abs/2410.05563
- https://arxiv.org/abs/2402.04513
- https://arxiv.org/abs/1707.02038
- https://arxiv.org/abs/2407.18418
- https://arxiv.org/abs/2412.01033
- https://arxiv.org/abs/2402.10978
- https://arxiv.org/abs/2402.12914
- https://arxiv.org/abs/2305.11860
- https://arxiv.org/abs/2401.10480
- https://arxiv.org/abs/2508.15260
- https://arxiv.org/abs/2502.18581
- https://arxiv.org/abs/2412.21187
- https://arxiv.org/abs/2608.14425
- https://arxiv.org/abs/2306.10193
- https://arxiv.org/abs/2207.05221
- https://arxiv.org/abs/2309.17012
- https://arxiv.org/abs/2404.13076
- https://arxiv.org/abs/2402.01306
- https://arxiv.org/abs/2305.17390
- https://arxiv.org/abs/2206.14576
- https://arxiv.org/abs/2410.20268
- https://arxiv.org/abs/2501.19393
- https://aclanthology.org/2025.alta-main.4/
- https://doi.org/10.1017/S0140525X1900061X
- https://doi.org/10.1037/0033-295X.113.4.700
- https://arxiv.org/abs/2306.13063
- https://arxiv.org/abs/2407.18370
- https://arxiv.org/abs/2406.15927
- https://arxiv.org/abs/2504.05419
- https://arxiv.org/abs/2410.02707
- https://arxiv.org/abs/2410.13284
- https://arxiv.org/abs/2502.19335
- https://arxiv.org/abs/2506.09038
- https://arxiv.org/abs/2507.16806
- https://arxiv.org/abs/2509.04664
- https://arxiv.org/abs/2503.00031
- https://arxiv.org/abs/2402.02047
- https://arxiv.org/abs/2406.15627
- https://transformer-circuits.pub/2025/introspection/index.html
- https://arxiv.org/abs/1711.06892
- https://arxiv.org/abs/2502.08235
- https://arxiv.org/abs/2504.01005
- https://arxiv.org/abs/2509.03581
- https://arxiv.org/abs/2511.17006
- https://www.nature.com/articles/nn1560
- https://doi.org/10.1016/j.neuron.2013.07.007
- https://cocosci.princeton.edu/papers/liederresource.pdf
- https://arxiv.org/abs/2410.04707
- https://arxiv.org/abs/2504.15895
- https://arxiv.org/abs/2503.16419
- https://pmc.ncbi.nlm.nih.gov/articles/PMC7702215
