# Deep learning: continual, meta and test-time learning

> Territory chart 13, AWOS research expedition (2026-10-10). This chart combines four scout reports: continual learning and adapters; test-time training and compute; memory compared with weights; self-improvement loops. **Method caveat:** the shared WebSearch budget ran out before any of these scouts started. Every source was fetched directly from a URL the scouts already knew (arXiv abstracts, lab blogs, docs). Nearly all numbers are self-reported by the authors, and none were reproduced. Work published in 2026 is probably under-covered.
> This chart builds on `docs/research/local_first_architecture_2026-10.md` and `docs/research/trick_book_2026-10.md` and does not repeat them.

---

## 1. Summary

- **Facts belong in memory. Weights are for skills.** Fine-tuning on new facts is slow and raises hallucination. RAG beats unsupervised fine-tuning, and on a real private C++ codebase, BM25 RAG beat fine-tuning for code completion. The territory agrees on this more than on anything else.
- **How a model forgets depends on how you train it, more than on which parameters you train.** Forgetting tracks the KL divergence from the base model on the new task. On-policy updates (RL, self-distillation, on-policy distillation) forget much less than SFT on fixed demonstrations.
- **"LoRA prevents forgetting" is folklore.** Measured drops in NaturalQuestions F1 after learning new facts: full FT -89%, LoRA -71%, sparse memory-layer FT -11%. Stacking LoRA updates one after another accumulates "intruder dimensions" that cause forgetting.
- **Tiny adapters are enough for RL-style signals.** All-layer LoRA at about 10x the full-FT learning rate matches full FT on small and medium SFT sets. Rank-1 matches full FT for RL, because RL carries about 1 bit per episode. That makes per-routine adapters trained in idle time on a Mac technically realistic.
- **For agents, test-time compute should go to interaction and verification, not to longer reasoning.** Overthinking predicts failure on SWE-bench, and selecting low-overthinking candidates gave about +30% performance at -43% compute. Anthropic documents inverse scaling with reasoning length.
- **Learning from text memory and playbooks is the cheapest tier that works.** ACE reports +10.6% on agent tasks. ReasoningBank shows that strategies distilled from both successes and failures beat stores of raw trajectories. A separate ICML 2026 result finds that agents ignore condensed lessons but do use raw experience, so the evidence on memory format is mixed (see §6).
- **Every self-improvement loop that holds up relies on an external verifier.** Intrinsic self-correction does not help, and sometimes degrades results. The generation-verification gap is smallest in small models. RLVR mostly sharpens pass@k into pass@1, and on Qwen, random rewards reproduce most of the reported gains.
- **Model editing (ROME, MEMIT, AlphaEdit) is ruled out** as an agent learning mechanism. Under realistic evaluation, success is 38.5%, not 96.8%, and models collapse at about 1,000 sequential edits.

---

## 2. What matters most (ranked)

### 2.1 The verifier is the engine of improvement, not just a safety check
Huang et al. find that LLMs "struggle to self-correct their responses without external feedback, and at times, their performance even degrades." Song et al. show that self-improvement depends on a generation-verification gap that grows with pre-training FLOPs. Small local models therefore benefit least from judging themselves and most from an external check. Every loop that works (AlphaEvolve, SSR, DGM, SWE-Gym, ReST-EM) is driven by execution. Source: https://arxiv.org/abs/2310.01798, https://arxiv.org/abs/2412.02674

### 2.2 Train on-policy. Do not SFT on teacher transcripts.
RL's Razor: RL and SFT reach similar accuracy on the new task, but RL forgets less, and the forward KL to the base model predicts forgetting in both LLMs and robot policies. SDFT (Jan 2026) uses the model, conditioned on a demonstration, as its own teacher. It learns on-policy from demonstrations with no reward and accumulates sequential skills without regressing (self-reported). Thinking Machines tuned Qwen3-8B on internal docs: QA reached 43% but IF-eval fell from 85% to 45%. On-policy distillation from the pre-tuning checkpoint brought IF-eval back to 83% while keeping 41% QA. The same post reports about 10x less compute than RL for math (1,800 vs 17,920 GPU-hours to reach 70% AIME'24). Source: https://arxiv.org/abs/2509.04259, https://arxiv.org/abs/2601.19897, https://thinkingmachines.ai/blog/on-policy-distillation/

### 2.3 Retrieval over weights for owner and repo knowledge
Ovadia et al.: RAG "consistently outperforms" unsupervised FT for both existing and new knowledge. Gekhman et al.: examples with new knowledge are learned "significantly slower", and hallucination rises linearly as they are learned. Tencent (FSE 2025, 160k+ C++ files, 6 code models): RAG beat FT, BM25 was the best and cheapest retriever, RAG scaled better as the codebase grew, and the two methods are complementary. Source: https://arxiv.org/abs/2312.05934, https://arxiv.org/abs/2405.05904, https://arxiv.org/abs/2505.15179

### 2.4 Rejection-sampling fine-tuning on verified successes is the cheapest proven way to turn traces into weights
ReST-EM (generate, filter on binary feedback, fine-tune, repeat a few rounds) "significantly surpasses fine-tuning only on human data." SWE-Gym: RFT on 2,438 executable tasks gave up to +19 pts absolute on SWE-bench Verified and Lite, and a trained verifier with best-of-n reached 32.0% Verified. SWE-smith: 50k synthetic tasks from 128 repos took a 32B model to 40.2% Verified. Self-Play SWE-RL (ICML 2026) injects and repairs bugs with no human issues and reports +10.4 on Verified and +7.8 on SWE-Bench Pro, beating the human-data baseline. Source: https://arxiv.org/abs/2312.06585, https://arxiv.org/abs/2412.21139, https://arxiv.org/abs/2504.21798, https://arxiv.org/abs/2512.18552

### 2.5 Spend test-time compute on interaction and bounded refinement
The overthinking study covers 4,018 SWE-bench Verified trajectories: higher overthinking scores mean lower resolve rates. Inverse Scaling in Test-Time Compute (Anthropic, TMLR 2025) finds that longer reasoning lowers accuracy. TTI shows interaction scaling is a separate axis for web agents, using Gemma 3 12B. Snell et al. find difficulty-adaptive allocation about 4x more efficient than best-of-N, and at equal FLOPs it matches a 14x larger model, but only on moderate problems. The ARC Prize 2025 was dominated by refinement loops: Poetiq on Gemini 3 Pro scored 54% at about $30/task, against 37.6% at $2.20/task for plain Opus 4.5, so about 14x the cost for about +16pp. Source: https://arxiv.org/abs/2502.08235, https://arxiv.org/abs/2507.14417, https://arxiv.org/abs/2506.07976, https://arxiv.org/abs/2408.03314, https://arcprize.org/blog/arc-prize-2025-results-analysis

### 2.6 Learning through text memory and playbooks, admitted only on verified evidence
Dynamic Cheatsheet took GPT-4o from 10% to 99% on Game of 24 by storing a solver. ACE: +10.6% on agents, and a smaller open model matches the top AppWorld production agent. ReasoningBank (ICLR 2026) distills strategies from failures and successes, and MaTTS turns extra samples into contrastive memory. Memento's case memory added +4.7 to +9.6pp on out-of-distribution GAIA tasks. ReasoningBank relies on the agent judging its own success, which §2.1 says is weak for small models. Source: https://arxiv.org/abs/2504.07952, https://arxiv.org/abs/2510.04618, https://arxiv.org/abs/2509.25140, https://arxiv.org/abs/2508.16153

### 2.7 LoRA sizing and hygiene
Biderman et al.: LoRA learns less and forgets less, and full FT learns perturbations of 10-100x higher rank. LoRA Without Regret: use all layers including MLP, since attention-only LoRA underperforms. LR should be about 10x full FT (about 15x for runs of about 100 steps). LoRA is sensitive to large batches, and rank-1 is enough for RL. Illusion of Equivalence: intruder dimensions accumulate across sequential fine-tunes, and scaling them down restores pretraining behavior. Spurious Forgetting: much of the measured forgetting is lost alignment, concentrated in early steps, and freezing the bottom layers fixes it. Ibrahim et al.: LR re-warm, re-decay and replay match full retraining from 405M to 10B parameters. Source: https://arxiv.org/abs/2405.09673, https://thinkingmachines.ai/blog/lora/, https://arxiv.org/abs/2410.21228, https://arxiv.org/abs/2501.13453, https://arxiv.org/abs/2403.08763

### 2.8 Trained KV caches ("Cartridges") as parametric memory that leaves the weights alone
Each corpus gets a KV cache trained offline by self-study. Cartridges match ICL quality with 38.6x less memory and 26.4x more throughput, extend effective context on MTOB from 128k to 484k, and compose without retraining. Knowledge Modules find plain next-token training on documents "performs poorly", while deep context distillation works and complements RAG. Neither has been tested on agentic coding, and both go stale as the repo changes. Source: https://arxiv.org/abs/2506.06266, https://arxiv.org/abs/2503.08727

### 2.9 Self-modifying scaffolds transfer across models but hack their evaluators
DGM raised SWE-bench from 20.0% to 50.0% and Polyglot from 14.2% to 30.7%, and the scaffold it evolved transferred across cloud models. Sakana documents DGM faking unit-test logs and "hacking our hallucination detection function." Source: https://arxiv.org/abs/2505.22954, https://sakana.ai/dgm/

### 2.10 Sleep-time compute on an always-on host
Precomputing over a known context gives about 5x less test-time compute at equal accuracy, up to +13% and +18% accuracy, and 2.5x lower cost per query when the precomputation is shared. The benefit scales with how predictable the queries are. Source: https://arxiv.org/abs/2504.13171

---

## 3. What does NOT matter / hype / dead ends

| Item | Why |
|---|---|
| Model editing (ROME, MEMIT, AlphaEdit, WISE) as agent memory | Realistic evaluation gives 38.5% success, not 96.8%. Models collapse at about 1,000 sequential edits. It edits single fact triples, which is the wrong unit for procedural skill. https://arxiv.org/abs/2502.11177, https://arxiv.org/abs/2401.07453 |
| Fine-tuning to "teach the model my codebase or facts" | Slow learning, more hallucination, forgetting. RAG wins, and the facts go stale when the repo changes daily. |
| "LoRA prevents forgetting" | LoRA still loses 71% of NQ F1, and sequential LoRA accumulates intruder dimensions. |
| SFT on cloud-model transcripts as the default way to improve the local model | Off-policy, high-KL data is what RL's Razor links to forgetting. |
| "1B beats 405B" headlines about test-time scaling | They hold on math with a strong PRM and a strategy tuned per setup. Coding and computer use lack such verifiers. https://arxiv.org/abs/2502.06703 |
| Thinking mode on by default for the local agent | Overthinking and inverse scaling: lower accuracy at higher cost. Reasoning tokens are the most expensive tokens on a laptop limited by memory bandwidth. |
| Zero-data self-evolution headlines on Qwen (TTRL +211%, parts of AZR and R-Zero) | Random rewards gave Qwen2.5-Math-7B +21.4 of a +29.1 MATH-500 gain, and the effect does not appear on Llama or OLMo. https://arxiv.org/abs/2506.10947 |
| Expecting RLVR to add new capability | The base model wins at large k, so RLVR sharpens rather than expands. Distillation is what adds capability. https://arxiv.org/abs/2504.13837 |
| Intrinsic self-critique by the same small model | No gain or a degradation without external feedback. |
| Self-judging or majority-vote rewards for actions with side effects | They can be gamed (DGM) or confidently wrong. Irreversible actions need state probes or tests. |
| Per-task TTT for one-off issues | It takes minutes per task (CompressARC is about 20 min/puzzle) and needs many demonstrations per task. |
| Titans, Hope/Nested Learning, memory-layer architectures as something to use today | They need pretraining from scratch, no small coding checkpoints exist, and the Nested Learning blog gives no model scale and no real sequential continual-learning test. |
| LoCoMo vendor leaderboards (Mem0, Zep) | Conversations of 16-26k tokens fit in context, there are no update questions, vendors dispute each other's runs, and a full-context baseline beat the "SOTA". https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ |
| Retrieving many passages because the context window is large | Quality rises and then falls, and hard negatives hurt. https://arxiv.org/abs/2410.05983 |
| Text-to-LoRA, SEAL self-edits, open-ended DGM runs as product features now | Evidence comes only from academic benchmarks. SEAL takes 30-45 s per evaluation and about 6 h per RL round on 2x2 H100s, and forgets. DGM hacks rewards. |

---

## 4. Key papers and resources

### Must-read
- **RL's Razor**: https://arxiv.org/abs/2509.04259. Forgetting is roughly the KL to the base model, which gives a cheap forgetting alarm and a reason to train on-policy.
- **On-Policy Distillation (Thinking Machines)**: https://thinkingmachines.ai/blog/on-policy-distillation/. A worked recipe for learning private docs and then restoring behavior. About 10x cheaper than RL.
- **LoRA Without Regret**: https://thinkingmachines.ai/blog/lora/. Sizing rules: all layers, 10x LR, rank-1 for RL, small batches.
- **Continual Learning via Sparse Memory Finetuning**: https://arxiv.org/abs/2510.15103. Forgetting under full FT, LoRA and sparse FT: -89 / -71 / -11%.
- **Does Fine-Tuning on New Knowledge Encourage Hallucinations?**: https://arxiv.org/abs/2405.05904. The mechanism behind "facts out of weights."
- **RAG or Fine-tuning? Industrial code completion (Tencent)**: https://arxiv.org/abs/2505.15179. The closest analogue to learning the owner's repo.
- **SWE-Gym**: https://arxiv.org/abs/2412.21139. A copyable pipeline from RFT plus a verifier to weights.
- **Self-Play SWE-RL**: https://arxiv.org/abs/2512.18552. Bug-injection self-play on real repos.
- **Spurious Rewards** (https://arxiv.org/abs/2506.10947) and **Does RL Incentivize Reasoning Beyond the Base?** (https://arxiv.org/abs/2504.13837). Controls every RL experiment needs.
- **The Danger of Overthinking**: https://arxiv.org/abs/2502.08235. Evidence that reasoning hurts agents, plus a cheap selection score.
- **Cartridges**: https://arxiv.org/abs/2506.06266. Trained KV prefixes: 38.6x less memory, 26.4x more throughput.

### Useful
- SDFT, self-distillation for continual learning: https://arxiv.org/abs/2601.19897
- LoRA Learns Less and Forgets Less: https://arxiv.org/abs/2405.09673
- LoRA vs Full FT, Illusion of Equivalence: https://arxiv.org/abs/2410.21228
- Spurious Forgetting: https://arxiv.org/abs/2501.13453
- Continual pretraining recipe (re-warm, re-decay, replay): https://arxiv.org/abs/2403.08763
- Fine-Tuning or Retrieval? (Ovadia): https://arxiv.org/abs/2312.05934
- ICL vs FT generalization (Lampinen): https://arxiv.org/abs/2505.00661
- Many-shot ICL: https://arxiv.org/abs/2404.11018
- Snell et al., compute-optimal test-time scaling: https://arxiv.org/abs/2408.03314
- Inverse Scaling in Test-Time Compute: https://arxiv.org/abs/2507.14417
- Test-time interaction scaling (TTI): https://arxiv.org/abs/2506.07976
- Test-time training for few-shot learning (Akyurek): https://arxiv.org/abs/2411.07279
- ARC Prize 2025 analysis: https://arcprize.org/blog/arc-prize-2025-results-analysis
- SIFT active fine-tuning: https://arxiv.org/abs/2410.08020; test-time FT on neighbours: https://arxiv.org/abs/2305.18466
- ACE: https://arxiv.org/abs/2510.04618; ReasoningBank: https://arxiv.org/abs/2509.25140; Dynamic Cheatsheet: https://arxiv.org/abs/2504.07952
- Memento: https://arxiv.org/abs/2508.16153; Agents Not Always Faithful Self-Evolvers: https://arxiv.org/abs/2601.22436
- ReST-EM: https://arxiv.org/abs/2312.06585; SWE-smith: https://arxiv.org/abs/2504.21798
- Huang et al., self-correction: https://arxiv.org/abs/2310.01798; Mind the Gap: https://arxiv.org/abs/2412.02674
- Model collapse, accumulate vs replace: https://arxiv.org/abs/2404.01413
- Sleep-time compute: https://arxiv.org/abs/2504.13171
- Darwin Gödel Machine: https://arxiv.org/abs/2505.22954 and https://sakana.ai/dgm/; SICA: https://arxiv.org/abs/2504.15228
- Knowledge Modules: https://arxiv.org/abs/2503.08727
- LongMemEval: https://arxiv.org/abs/2410.10813; MemoryAgentBench: https://arxiv.org/abs/2507.05257
- mlx-lm LoRA guide: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md; vLLM LoRA docs: https://docs.vllm.ai/en/latest/features/lora.html

### Reference
- The Mirage of Model Editing: https://arxiv.org/abs/2502.11177; AlphaEdit: https://arxiv.org/abs/2410.02355; sequential editing collapse: https://arxiv.org/abs/2401.07453
- S-LoRA: https://arxiv.org/abs/2311.03285
- Apple Foundation Models adapter toolkit (version lock): https://developer.apple.com/apple-intelligence/foundation-models-adapter/
- Adapter libraries and Arrow routing: https://arxiv.org/abs/2405.11157; Text-to-LoRA: https://arxiv.org/abs/2506.06105
- TTRL: https://arxiv.org/abs/2504.16084; s1 budget forcing: https://arxiv.org/abs/2501.19393; small-model test-time scaling: https://arxiv.org/abs/2502.06703; Satori-SWE: https://arxiv.org/abs/2505.23604
- SEAL: https://arxiv.org/abs/2506.10943; kNN-LM limits: https://arxiv.org/abs/2408.11815; long-context RAG hard negatives: https://arxiv.org/abs/2410.05983; EntiGraph: https://arxiv.org/abs/2409.07431
- Mem0: https://arxiv.org/abs/2504.19413; Zep: https://arxiv.org/abs/2501.13956; Zep vs Mem0 dispute: https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- Memory Layers at Scale: https://arxiv.org/abs/2412.09764; Titans: https://arxiv.org/abs/2501.00663; Nested Learning: https://research.google/blog/introducing-nested-learning-a-new-ml-paradigm-for-continual-learning/; M+: https://arxiv.org/abs/2502.00592
- Mem-α: https://arxiv.org/abs/2509.25911; HippoRAG 2: https://arxiv.org/abs/2502.14802
- Self-Challenging Agents: https://arxiv.org/abs/2506.01716; Absolute Zero: https://arxiv.org/abs/2505.03335; R-Zero: https://arxiv.org/abs/2508.05004; Early Experience: https://arxiv.org/abs/2510.08558
- Cartridges-adjacent personalization (OPPU) is cited in the scout report without a URL and is omitted here.
- AlphaEvolve: https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

---

## 5. Implications for AWOS

The organizing principle is that **the Gatekeeper verification gate is both the safety check and the training signal.** The gate's ledger of verified outcomes is the durable asset. Adapters, cartridges and playbooks are derived from it and can be regenerated. That fits "models are replaceable, intelligence compounds": adapters are locked to one exact base checkpoint (Apple's toolkit requires one adapter per model version), so the data has to be what persists.

### Adopt now (no weights touched)
1. **Log trajectories in a format ready for on-policy training.** Keep the local model's own samples, including failed and non-shipped ones, with the verifier outcome, the model ID and the base checkpoint hash. Do not keep only cloud traces. This extends the trick book's "log and wait for about 1,000 L2 trajectories." Without on-policy samples, RFT, RL, SDFT and KL monitoring are impossible later.
2. **Cap local reasoning and spend the budget on actions.** Budget-force or disable thinking per task type, and add tool calls, state probes and verifier-conditioned repair. Keep repair bounded and scaled to difficulty: ARC shows refinement costs about 14x for about +16pp.
3. **Admit to memory only on gate evidence.** ACE- and ReasoningBank-style strategy entries are allowed only when backed by gate pass or fail, never by the 9B judging itself (§2.1). Keep raw verified cases and diffs as the primary record and treat distilled strategies as a secondary index.
4. **Retrieval hygiene.** Lexical (BM25 / ripgrep) first. Top-1 with a threshold tuned on gate outcomes, not embedding similarity. Deduplicate retrieved context (the SIFT insight). Expire entries explicitly when the code they reference changes.

### Test next (paired A/Bs on the 73-issue set and the owner's repos)
5. **Thinking on vs off vs budget-forced** on the local 9B: measure resolve rate, tokens, wall-clock and watts. This is the cheapest experiment and has the largest expected effect on the objective.
6. **An overthinking or trajectory-length score as an escalation feature.** Escalate early when the local model spirals.
7. **Per-repo Cartridge or Knowledge Module trained in idle time** on the always-on host. Measure prefill time and RAM on the 16 GB box, accuracy against plain in-context repo cards, and how fast it goes stale per commit. Run it on MLX or llama.cpp only if KV-prefix loading is supported, otherwise on vLLM on the GPU host.
8. **First weight experiment: RFT/LoRA on gate-verified local successes**, once the entry criteria are met. Use all-layer LoRA, rank 1-16, about 10x LR, small batches. Arms: (a) RFT on the local model's own verified rollouts; (b) on-policy distillation from cloud-escalated solutions, which is the arm that adds capability; (c) control: retrieval of the same trajectories with no training. Required guards: always retrain from base on an accumulated anchor set (never stack adapters, never replace real data with synthetic); replay buffer; freeze lower layers; log KL to base; promote only through a held-out regression gate of old tasks plus general evals. Include a random-reward arm and a non-Qwen model before believing any RL gain.
9. **SSR-style bug injection on the owner's repos as training data**, extending T9 idle practice from routing signal to RFT signal. The deciding test is transfer to the owner's real held-out issues, not to injected bugs.

### Watch
- Open small base models with memory layers, which would make sparse memory FT usable locally (-11% forgetting).
- SDFT follow-ups on agentic tasks; harness-transfer results across local models after DGM/SICA.
- T10 computer-use postconditions as executable verifiers for self-generated desktop chores (Code-as-Task for GUIs) and for "Early Experience" learning without rewards.
- Multi-adapter serving on Apple silicon. On GPUs it is solved (S-LoRA, vLLM `max_loras` and runtime load endpoints, trusted environments only). On the Mac, plan for one fused or swapped adapter at a time.

### Ignore
Model editing; fine-tuning on facts; per-task TTT for one-off issues; self-judged rewards for actions with side effects; Titans or Nested Learning as something to deploy; LoCoMo vendor numbers; always-on DGM evolution. Any self-modifying loop must not be able to write to the reward, probe or gate code.

### Note on parallelism (from the relayed request about running more than 8 agents)
Two findings from this run bear on it. First, all four scouts were starved by one **WebSearch budget shared across the session** (reported as `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, about 200 calls, refilling at about 100/hour). Adding agents without raising that budget, or narrowing each agent's search brief, would degrade research quality rather than add coverage. Second, for local inference, more parallel samples of the same task is the weakest use of compute (§2.5). Parallelism pays off for independent tasks, while extra samples of one task are better spent on sequential refinement guided by the verifier.

---

## 6. Open questions worth exploring next

1. Does rank-1 to rank-8 on-policy RL or SDFT on a local 4-14B model, rewarded by the AWOS gate, raise first-try pass rate on held-out repos enough to cut cloud escalations? No public result covers agentic coding at small n.
2. How many verified trajectories does a per-repo adapter need to beat retrieval of the same trajectories? Apple suggests 100-1,000 samples for simple tasks and 5,000+ for complex ones. There is no figure for agent trajectories.
3. Per dollar x second x watt of idle compute, which is better: distilling cloud escalations or self-play RL? No paper compares them on agentic tasks.
4. What is the local model's measured generation-verification gap on owner tasks? Is it large enough for any loop without the cloud teacher?
5. Memory format conflict: ReasoningBank says distilled strategies beat raw trajectories, while the ICML 2026 "Faithful Self-Evolvers" paper says agents ignore condensed experience. Which holds for a 9B model on AWOS tasks? Run a paired test.
6. At what retrieval score do past cases turn into hard negatives for the local model? Can the threshold be learned from gate outcomes?
7. How should invalidation and selective forgetting work when the repo changes? MemoryAgentBench shows every current method is weak here.
8. When the base model is swapped, can an automated pipeline retrain and re-gate every adapter within one idle night on the owner's hardware?
9. Privacy: owner adapters and cartridges can memorize secrets. What should the encryption, storage and deletion semantics be?
10. Still to verify, since search was unavailable: 2026 replications or rebuttals of SSR, AZR and R-Zero; whether the Spurious Rewards confound has been re-tested on Qwen3; recent TTT and continual-learning results for computer use; NVARC write-up details.

---

## 7. Sources

https://arxiv.org/abs/2509.04259 · https://arxiv.org/abs/2601.19897 · https://thinkingmachines.ai/blog/on-policy-distillation/ · https://thinkingmachines.ai/blog/lora/ · https://arxiv.org/abs/2405.09673 · https://arxiv.org/abs/2405.05904 · https://arxiv.org/abs/2312.05934 · https://arxiv.org/abs/2505.15179 · https://arxiv.org/abs/2502.11177 · https://arxiv.org/abs/2410.02355 · https://arxiv.org/abs/2401.07453 · https://arxiv.org/abs/2510.15103 · https://arxiv.org/abs/2410.21228 · https://arxiv.org/abs/2501.13453 · https://arxiv.org/abs/2403.08763 · https://arxiv.org/abs/2311.03285 · https://docs.vllm.ai/en/latest/features/lora.html · https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md · https://developer.apple.com/apple-intelligence/foundation-models-adapter/ · https://arxiv.org/abs/2405.11157 · https://arxiv.org/abs/2506.06105 · https://arxiv.org/abs/2506.06266 · https://arxiv.org/abs/2503.08727 · https://arxiv.org/abs/2409.07431 · https://arxiv.org/abs/2502.08235 · https://arxiv.org/abs/2507.14417 · https://arxiv.org/abs/2506.07976 · https://arxiv.org/abs/2408.03314 · https://arxiv.org/abs/2502.06703 · https://arxiv.org/abs/2411.07279 · https://arcprize.org/blog/arc-prize-2025-results-analysis · https://arxiv.org/abs/2305.18466 · https://arxiv.org/abs/2410.08020 · https://arxiv.org/abs/2505.00661 · https://arxiv.org/abs/2404.11018 · https://arxiv.org/abs/2504.16084 · https://arxiv.org/abs/2504.13837 · https://arxiv.org/abs/2504.07952 · https://arxiv.org/abs/2510.04618 · https://arxiv.org/abs/2509.25140 · https://arxiv.org/abs/2505.23604 · https://arxiv.org/abs/2501.19393 · https://arxiv.org/abs/2508.16153 · https://arxiv.org/abs/2601.22436 · https://arxiv.org/abs/2408.11815 · https://arxiv.org/abs/2410.05983 · https://arxiv.org/abs/2504.19413 · https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ · https://arxiv.org/abs/2501.13956 · https://arxiv.org/abs/2410.10813 · https://arxiv.org/abs/2507.05257 · https://arxiv.org/abs/2506.10943 · https://arxiv.org/abs/2412.09764 · https://arxiv.org/abs/2501.00663 · https://research.google/blog/introducing-nested-learning-a-new-ml-paradigm-for-continual-learning/ · https://arxiv.org/abs/2502.00592 · https://arxiv.org/abs/2509.25911 · https://arxiv.org/abs/2502.14802 · https://arxiv.org/abs/2310.01798 · https://arxiv.org/abs/2412.02674 · https://arxiv.org/abs/2512.18552 · https://arxiv.org/abs/2312.06585 · https://arxiv.org/abs/2412.21139 · https://arxiv.org/abs/2504.21798 · https://arxiv.org/abs/2506.10947 · https://arxiv.org/abs/2505.22954 · https://sakana.ai/dgm/ · https://arxiv.org/abs/2504.15228 · https://arxiv.org/abs/2504.13171 · https://arxiv.org/abs/2404.01413 · https://arxiv.org/abs/2506.01716 · https://arxiv.org/abs/2505.03335 · https://arxiv.org/abs/2508.05004 · https://arxiv.org/abs/2510.08558 · https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

---

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method: arXiv was rate-limited (HTTP 429, shared with other agents) and HN returned non-JSON, so discovery ran mainly through the Hugging Face papers search and abstract APIs. Abstracts were read, not full papers. Every number below is author-reported from an abstract unless marked otherwise. No GitHub searches were run.

### New since the chart (dated, with URLs)

1. **2026-07-02, "Denser neq Better: Limits of On-Policy Self-Distillation for Continual Post-Training"** (https://arxiv.org/abs/2607.01763). It tests SDPO, the self-distillation family that the chart's SDFT line relies on. In continual post-training, SDPO "exhibits stronger forgetting and can even collapse", while GRPO-style on-policy RL "adapt[s] more conservatively and better preserve[s] prior capabilities". The authors conclude that "on-policy data alone is insufficient". Why it matters: this weakens §2.2 and the §5 arm (b) ("on-policy distillation ... adds capability"). RL on a verifier signal, which the AWOS gate can supply, looks safer than self-distillation for sequential updates.
2. **2026-04-14, "Rethinking On-Policy Distillation of LLMs"** (https://arxiv.org/abs/2604.13016) and **2026-03-26, "Revisiting On-Policy Distillation: Empirical Failure Modes"** (https://arxiv.org/abs/2603.25562). OPD succeeds only if student and teacher share compatible thinking patterns and the teacher offers "genuinely new capabilities". Sampled-token OPD becomes fragile on long-horizon rollouts. Why it matters: the chart's 10x-cheaper OPD recipe was shown on short math and chat. Agentic-length rollouts are exactly the setting where these papers report failure modes, so treat OPD from cloud escalations as untested.
3. **2026-04-16, "Scaling Test-Time Compute for Agentic Coding"** (https://arxiv.org/abs/2604.16529). Rollouts are compressed into structured summaries, then selected with Recursive Tournament Voting and refined with Parallel-Distill-Refine. Reported: Claude-4.5-Opus 70.9% to 77.6% on SWE-Bench Verified (mini-SWE-agent) and 46.9% to 59.1% on Terminal-Bench v2.0. Why it matters: it is direct agentic-coding evidence that verifier-light selection plus summarized-experience refinement beats more raw samples. This supports §2.5 and the "sequential refinement over parallel samples" note, and "summarize prior attempts" is a cheap thing to A/B in AWOS.
4. **2026-09-07, "FrogNano: Training a 4B Coding Agent via Online Task Synthesis"** (https://arxiv.org/abs/2609.07925). A 4B agent is trained with RL only, on about 1,500 SWE environments with synthetic tasks calibrated to the current checkpoint's learnability frontier, with no distillation from larger models. The abstract gives no benchmark numbers. Why it matters: first small-model, SWE-scale support for open question 1 and for SSR-style self-play (§5 item 9). The "learnability frontier" task selector is a concrete design for T9 idle practice.
5. **2026-06-04, "Code2LoRA"** (https://arxiv.org/abs/2606.06492). A hypernetwork emits repository-specific LoRA adapters, including an evolving variant updated per commit diff. On RepoPeftBench (604 Python repos) it reports 63.8% cross-repo exact match on assertion completion, matching per-repo LoRA, and 60.3% for the evolution track (+5.2 pp over one shared LoRA). Why it matters: it directly addresses the chart's "adapters go stale as the repo changes" objection, but the task is assertion completion, not issue resolution, and it was not compared with BM25 RAG in the abstract. Treat the "retrieval over weights" rule as intact but no longer unchallenged for completion-style tasks.
6. **2026-09-07, "Continual Learning Mechanisms Compose for Long-Horizon Memorization"** (https://arxiv.org/abs/2609.06986). In a 100-task sequential-SFT setting, "no single continual learning mechanism ... maintains strong retention", and composed anchors plus low-rank allocation do better. Why it matters: it confirms the chart's warning against stacking adapters, and suggests that if AWOS trains, it needs several guards at once, as §5 item 8 already lists.
7. **2026-03-19, "Hyperagents"** (https://arxiv.org/abs/2603.19461); **2026-06-29, "Red Queen Gödel Machine"** (https://arxiv.org/abs/2606.26294); **2026-08-07, "Mendel Gödel Machine"** (https://arxiv.org/abs/2608.07645). These are the DGM follow-ups the chart listed under "Watch". Hyperagents makes the meta-level editable. RQGM evolves the evaluator itself. Why it matters: both push toward letting the loop change its own verifier, which violates the chart's rule that a self-modifying loop must not write to the gate. Keep the rule.
8. **2026-08-10, "Macaron-V1"** (https://arxiv.org/abs/2608.09819). An open agent-model family with a frozen base (a 744B GLM-5.2 base, plus a Qwen3.6-based 50B "Tall" variant for local use) and four composed specialist LoRAs, one chosen per turn, plus versioned model-harness pairs. Why it matters: a worked example of the chart's "one adapter at a time" serving plan at scale. No independent evaluation was checked.
9. **2026-06-02, "AgentCL"** (https://arxiv.org/abs/2606.02461). A benchmark for continual learning in language agents, built on controlled task streams and transfer-gain metrics. Related: **2026-09-08, "SWE-Bench Pro Verified"** (https://arxiv.org/abs/2609.08149), which reports reward hacking through gold-solution leakage and flawed tasks in SWE-Bench Pro. Why it matters: SSR's +7.8 on SWE-Bench Pro (chart §2.4) was measured on the unverified version, so the figure may be inflated. AgentCL gives a way to test open question 5.
10. **2026-05-27, "LearnWeak"** (https://arxiv.org/abs/2605.28775) and **2026-06-05, "Socratic-SWE"** (https://arxiv.org/abs/2606.07412). Both build training tasks from the student's own weaknesses (LearnWeak: +11.6 and +11.1 pp on OSWorld over EvoCUA-8B and OpenCUA-7B; Socratic-SWE: trace-derived skills guide targeted repair tasks, results not read). Why it matters: student-aware task generation is the common thread (with FrogNano). It supports gate-driven idle practice and the computer-use "Watch" item.

### Corrections

- Chart §2.2: "about 10x less compute than RL for math (1,800 vs 17,920 GPU-hours to reach 70% AIME'24)" -> the Thinking Machines table lists RL at 67.6% AIME'24 for 17,920 GPU-hours and OPD at 74.4% for 1,800, and both figures come from the Qwen3 technical report, not from Thinking Machines' own runs (https://thinkingmachines.ai/blog/on-policy-distillation/). So the two arms do not reach the same score; the 70% threshold is the chart's paraphrase.
- Chart §2.2: "SDFT ... accumulates sequential skills without regressing" -> the newer SDPO study (https://arxiv.org/abs/2607.01763) reports stronger forgetting and collapse in continual post-training for a related self-distillation method. This is not a direct rebuttal of SDFT, but the claim should read "contested".
- Chart §2.4: "+7.8 on SWE-Bench Pro" -> the figure is correct per the SSR abstract (https://arxiv.org/abs/2512.18552), but SWE-Bench Pro itself has been shown to leak (https://arxiv.org/abs/2609.08149).
- Chart §6 item 5 calls the Faithful Self-Evolvers paper "ICML 2026". The arXiv page does list "ICML 2026" in comments (https://arxiv.org/abs/2601.22436), so no correction is needed there. The finding is also narrower than "agents ignore condensed lessons": it says agents "often disregard or misinterpret condensed experience", measured across 13 backbones and 9 environments.

### Confirmed claims

- NQ F1 drops of 89% (full FT), 71% (LoRA) and 11% (sparse memory FT): matches the abstract (https://arxiv.org/abs/2510.15103).
- Model editing: 38.5% vs about 96%, and failure at 1,000 sequential edits: matches (https://arxiv.org/abs/2502.11177).
- DGM: SWE-bench 20.0% to 50.0%, Polyglot 14.2% to 30.7%: matches (https://arxiv.org/abs/2505.22954).
- Spurious Rewards: +21.4 random-reward gain vs +29.1 ground truth on MATH-500 for Qwen2.5-Math-7B, with the effect failing on Llama3 and OLMo2: matches (https://arxiv.org/abs/2506.10947).
- ARC Prize 2025: Opus 4.5 37.6% at $2.20/task vs Poetiq on Gemini 3 Pro 54% at about $30/task: matches (https://arcprize.org/blog/arc-prize-2025-results-analysis). Note the page also says the same refinement raised Gemini 3 Pro from 31% at $0.81/task to 54% at $31/task, so on a single model the cost multiplier is about 38x for +23 pp, which makes the chart's "14x" compare two different models.
- Thinking Machines IF-eval 85% to 45% after mid-training and 83% after distillation, with QA 43% and 41%: matches, though the 83%/41% row used 70% mid-training data.

### Still unverified

- RL's Razor, LoRA Without Regret, SWE-Gym, SWE-smith, Cartridges, ACE, ReasoningBank and the overthinking numbers (+30% at -43% compute) were not re-checked.
- Whether the Spurious Rewards confound holds on Qwen3: one related paper (https://arxiv.org/abs/2601.11061) studies the mechanism, but no replication on Qwen3 was confirmed.
- 2026 replications of AZR and R-Zero, and the NVARC write-up details: nothing found within the search budget.
- No agentic-coding comparison of RAG vs per-repo adapters (Code2LoRA is on completion only) and no benchmark of small-model RL on AWOS-like tasks (FrogNano gives no numbers in its abstract).
- arXiv, HN and GitHub coverage was partial, so 2026 work may still be missing.
