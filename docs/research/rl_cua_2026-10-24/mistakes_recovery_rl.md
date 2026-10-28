# Learning from mistakes, recovery training, and online RL for small CUA agents

Date: 2026-10-24. Scope: published work 2024 to Oct 2026, read for AWOS's setup:
Qwen3.5-2B/4B + LoRA, 2x T4 16 GB (fp16, no bf16), ~30 GPU-hours/week on Kaggle,
accessibility-tree text observations, closed action set, executable verifiers, a scripted
oracle per task, Claude-distilled successes, and thousands of diagnosed own-failures.

Evidence tags: **STRONG** = peer-reviewed or replicated across several papers, with
numbers checked in the paper text. **MEDIUM** = one paper, numbers read from the paper,
not replicated. **WEAK** = abstract or secondary summary only, or my own estimate.

## Bottom line

1. **Our setup already has the best ingredient in the literature: a deterministic
   environment with an oracle.** Most papers spend their compute on finding the step
   where a run went wrong (MCTS, Monte Carlo rollouts, or a VLM judge). We can get that
   step almost for free by replaying a failed run and asking the oracle at each state.
2. **The cheapest real gain is first-divergence step pairs (DPO + NLL), plus recovery
   splices, trained on top of SFT.** Expect +3 to +9 points over SFT, from 1 to 3
   iterations, before overfitting sets in. **STRONG** for the direction, **MEDIUM** for the size.
3. **Online GRPO helps the most in the papers (+12 to +16 points over SFT at 3B to 4B),
   but every published recipe used 8 or more A100/H100/B200 GPUs.** On 2x T4 it is only
   feasible as a small run: a 2B model, short horizons, LoRA, tasks pre-filtered to
   0 < pass rate < 1. Do it last, after the offline steps stop giving gains.
4. **Behaviour cloning (SFT) first is not optional.** WebAgent-R1-Zero, which ran RL with
   no warm-up, got slightly worse. **MEDIUM**

## 1. Learning from mistakes (offline: preference and negative-aware training)

| Method | Base model | Benchmark | Result | How pairs were built | Tag |
|---|---|---|---|---|---|
| ETO (ACL 2024) | Llama-2-7B | WebShop / SciWorld / ALFWorld | WebShop 63.1 SFT → 67.4; SciWorld seen 53.0 → 65.0 | Expert success vs. the agent's own failure on the same task; trajectory-level DPO | MEDIUM |
| IPR (EMNLP 2024) | Llama-2-7B | WebShop / InterCodeSQL / ALFWorld | Average 60.6 SFT → 66.4 ETO → 69.4 IPR; WebShop 71.3 | The agent re-acts from each expert prefix; 5 Monte Carlo rollouts score each step; the step counts as an error if its reward is more than τ below the expert's; loss = trajectory DPO + step DPO + SFT | MEDIUM |
| Agent-R (2025) | Llama-3.1-8B | WebShop / SciWorld / TextCraft | Average 70.71 vs. ETO 65.12 (+5.59) | MCTS; the actor finds the first error; the bad prefix is spliced onto a good sibling branch (details in section 2) | MEDIUM |
| Agent Q (2024) | Llama-3-70B | OpenTable (real site) | 18.6% → 81.7% zero-shot; 95.4% with search at test time | MCTS plus an AI self-critique ranks the branches at each node; off-policy step-level DPO | MEDIUM |
| EvoCUA (Jan 2026) | Qwen3-VL-8B / 32B | OSWorld | 32B reaches 56.7%; the offline DPO stage adds +3.21 (+3.02 on OpenCUA-72B) | Critical deviation step against a successful reference; an action-correction pair at step t, plus a reflection pair at step t+1 | MEDIUM |
| NAT / Learning From Failure (NAACL 2025) | 7B-class | Math, multi-hop QA, strategy QA | Beats SFT, DPO and PPO (abstract) | Failed runs kept in SFT, each tagged with a "success" or "failure" prefix; at inference, condition on "success" | WEAK |
| KTO (STEVE, 2025) | UI grounding VLM | Windows desktop | Positive numbers reported; I did not check them | Unpaired good and bad steps; suited to data that is mostly failures | WEAK |
| Divergence-point DPO (Jun 2026) | not stated | tau2-bench | 0.338 → 0.355 from only 161 pairs | State matching, falling back to longest common prefix; drops near-identical pairs | WEAK |
| WebRL / OpenWebVoyager | 8B / VLM | WebArena-Lite / open web | Use failures as new curriculum tasks or for filtered SFT, not as negatives | Judge-filtered successes | MEDIUM |

### What the papers agree on

- **Step-level pairs beat whole-trajectory pairs, but only when the error step is
  located accurately.** ETO's step-level DPO, with the error step inferred from the final
  reward alone, collapsed to 8.3 on WebShop at lr 1e-6 / β 0.1. Even tuned to lr 1e-7 /
  β 0.5 it reached only 62.8, below the trajectory-level 67.4. IPR fixed this with Monte
  Carlo step values and won. **Lesson for AWOS:** use the oracle to locate the error step,
  never a guess. **MEDIUM**
- **Gains peak after 1 to 4 iterations, then decline.** ETO: 2 iterations, and only 1 on
  ALFWorld, where later rounds fell below SFT. IPR: peak at iteration 3 or 4. Causes given:
  a fixed expert set, low pair diversity, and overfitting. Hold out a fixed eval set and
  stop at the first drop. **STRONG** (3 papers)
- **Add an NLL/SFT term to the chosen side.** Pure DPO lowers the likelihood of the chosen
  answer, especially when starting from an SFT checkpoint trained on those same answers.
  DPO+NLL (RPO, Pang et al., NeurIPS 2024) fixes this; IPR sums DPO and SFT with equal
  weight. For agents, this means the policy can drift to strange actions that are neither
  the chosen nor the rejected one. **STRONG**
- **Settings that worked at 7-8B:** β 0.1 (0.5 on noisier tasks), lr 1e-6 for full
  fine-tuning (IPR searched 1e-5 to 5e-5). For LoRA, start around 5e-6 to 2e-5, keep an NLL
  weight of about 1.0, and train 1 epoch per iteration. The LoRA numbers are my
  extrapolation. **WEAK**
- **Pitfalls to watch:**
  - *Verbosity or length hacking:* DPO favours longer chosen answers. Keep the reasoning
    format fixed, or cap it.
  - *Over-early termination:* EvoCUA found that too many success signals make the agent
    stop too soon, and too few make it never stop. Balance `done` actions between chosen
    and rejected sides.
  - *Off-policy drift:* EvoCUA argues pairs should be on-policy, so rebuild them from the
    current model every iteration.
  - *Judge reward hacking:* OpenWebRL saw it with a weak 8B judge. We avoid this risk
    because our verifiers are executable code.
  - **MEDIUM**

## 2. Recovery and self-correction training

| Work | Model | What the recovery data looks like | Effect | Tag |
|---|---|---|---|---|
| Agent-R | Llama-3.1-8B | Bad prefix up to the first error the actor itself can spot, then a revision thought (1 of 10 hand-written templates), then the good sibling branch | Revision-task average 46.75 vs. 35.67 when revising only at the end of the run, and 13.08 for ETO. Fewer repeated-action loops. Mixing revision runs with optimal runs beats optimal runs alone; adding non-optimal "good" runs hurts | MEDIUM |
| GUI-Reflection (NeurIPS 2025) | InternVL2.5-8B | Built from successful trajectories with no humans: find the first wrong step, keep the prefix, and generate (a) the correct action before the error and (b) reflection plus correction after it. Then an online reflection loop with a success-rate curriculum | 34.5% on AndroidWorld, competitive for its size (8B) | WEAK |
| EvoCUA | 8B / 32B | Reflection pair at step t+1. Rejected: carrying on blindly after the error. Chosen: notice the unexpected screen, then plan a fix | Part of the +3.2 from the DPO stage | MEDIUM |

**Does it help small models?** Every paper with numbers used 7-8B or larger. No paper I
found isolates recovery data at 2-4B. The mechanism (fewer loops, earlier error
detection) matches our diagnosed failure types: loops and giving up early. Treat it as a
likely win that we must test ourselves. **WEAK** for 2-4B.

**How to make recovery data with our deterministic environment and oracle.** This is our
design, built from the papers above.

1. **Find the real divergence.** For each failed run, replay it step by step. At each
   state s_t, ask the oracle "can I still reach the goal from here, and how many steps
   does it take?". The first step where the agent's action makes the goal unreachable, or
   raises the oracle's remaining-step count, is t\*. This is exact, and it is cheaper than
   IPR's 5 rollouts per step.
2. **Step pair (for DPO).** At the prompt for s_t\*: chosen = the oracle's action, with a
   short rationale distilled from Claude; rejected = the agent's action. Drop pairs whose
   actions are equivalent, for example two controls with the same effect. That makes one
   clean pair per failure.
3. **Recovery splice (for SFT).** Keep the prefix up to and including the wrong action, and
   the real observation that followed. Then add a short reflection ("the dialog did not
   change, so I clicked the wrong control"). Rotate about 10 to 20 templates, or have
   Claude write one per failure type. Finish with the oracle's continuation from that
   actual state.
   - **Put zero loss on the wrong action.** Only the reflection and the recovery steps
     get trained, so the model never learns to make the error. Use the same masking for
     inherited failed prefixes.
4. **Synthetic perturbations for the failure types we have diagnosed.** On oracle
   successes, inject one realistic error, then splice in the recovery:
   - *wrong control:* swap in a sibling element;
   - *gave up early:* insert a premature `done`/`fail` as the rejected side, with the
     chosen side continuing;
   - *loop:* repeat the previous action twice, then reflect and break out;
   - *unsafe:* chosen side = the safe alternative or a request for confirmation.
   The deterministic replay guarantees the observation after the error is real.
5. **Mixing ratio.** Agent-R mixed about 20% general data. Start with roughly 60% clean
   oracle or Claude successes, 25% recovery splices, and 15% general or format data. Check
   that the success rate on clean tasks does not drop.

## 3. Online RL for agents of 9B or smaller

| Work | Model | Algorithm | Compute | Gain | Key recipe points | Tag |
|---|---|---|---|---|---|---|
| WebAgent-R1 (EMNLP 2025) | Qwen2.5-3B; Llama-3.1-8B | Multi-turn GRPO, binary outcome reward | 8x A100-80G, full fine-tune | 3B: 6.1 prompt → 20.0 BC → **33.9**. 8B: 8.5 → 20.6 → **44.8** (WebArena-Lite) | BC on 9.46K trajectories first; KL 0.001; lr 1e-6; batch 16; old observations compressed; loss on action tokens only. Zero-start RL did not help | MEDIUM |
| WebRL (ICLR 2025) | Llama-3.1-8B | Off-policy RL plus an outcome reward model; new tasks generated from failures | multi-GPU (not stated) | 4.8 → 42.4 | Failures seed new curriculum tasks; KL guards against policy drift | STRONG (peer-reviewed, reproduced as the baseline in R1) |
| WEBSERV (Oct 2025) | Qwen3-4B-Thinking | GRPO + dynamic filtering | **64x H200**, about 12 min/step, 100 steps | 24.5 → 54.1 SFT → 57.3 RL (+3.2, one run) | Group size 12; clip 0.2 / 0.28; KL 0.001; lr 1e-6; SFT on Claude-4.5 trajectories carries most of the gain | MEDIUM |
| OpenWebRL (Jun 2026) | Qwen3-VL-4B | MM-GRPO, no KL, no entropy term | **about 300 B200 GPU-hours**, 54K trajectories | 39.3 → 52.0 SFT (412 trajectories!) → **68.4** | 15-step then 30-step horizon curriculum; drop groups with identical rewards; 2 PPO epochs (3 overfits); +reward only if well formatted and successful, − on repeated format errors; a 1.9K SFT warm start did worse after RL than 0.4K | MEDIUM |
| AgentGym-RL (ICLR 2026) | Qwen2.5-3B / 7B | PPO/GRPO with ScalingInter-RL | multi-GPU | 7B: +33.65 average over the base model; WebArena 26% | Grow the turn budget over training. A fixed 10-turn budget collapsed; a fixed 5-turn budget hit a ceiling | MEDIUM |
| ARPO (2025) | UI-TARS-1.5-7B | GRPO + replay buffer | multi-GPU | OSWorld 23.5 → 29.9 (plain GRPO 26.0) | Keep a task only if pass@16 > 0; if a group all fails, swap in a stored success; −1 for an invalid action | MEDIUM |
| DigiRL (NeurIPS 2024) | 1.3B VLM | Offline RL, then offline-to-online | up to 64 emulators | AitW 17.7 → 67.2 (filtered BC 57.8) | A small model works when the environment runs fast in parallel; VLM evaluator | STRONG |
| ZeroGUI (2025) | UI-TARS-7B | Online RL; VLM generates tasks and rewards | multi-GPU | OSWorld +2.5 (+14% relative) | Generated tasks raise pass@k; training at test time raises consistency | WEAK |
| UI-R1 / GUI-R1 (2025) | Qwen2.5-VL-3B | GRPO, rule-based action reward, single step | small | UI-R1-3B: ScreenSpot +18 to 22, AndroidControl +11 to 13, from 136 to 2K samples | Reward = action type + arguments + format; very data-efficient for one-step grounding | MEDIUM |

MiMo was not checked in this pass.

### Can this run on 2x T4?

- **Precision is in our favour.** "Defeating the Training-Inference Mismatch via FP16"
  (Oct 2025) shows fp16 removes most of the mismatch between the rollout engine and the
  trainer that bf16 causes in GRPO, LoRA included. T4s only have fp16. Watch for overflow
  with bf16-trained Qwen weights (keep LayerNorm and logits in fp32). **MEDIUM**
- **Memory.** Unsloth reports GRPO with LoRA on a single free T4 for models up to 8B,
  sharing weights with vLLM. That is a vendor claim. **WEAK**
- **Throughput.** The published runs generated 50-77K tokens per trajectory, 200
  trajectories per step, on 64 H200s. That is far beyond 2x T4. Our advantages:
  - text accessibility tree, so no images;
  - millisecond resets in the synthetic apps;
  - short tasks.

  Estimated budget per rollout: about 8 turns, about 150 output tokens per turn, prefix
  caching on. At that size, one T4 serving a 2B model in vLLM fp16 should give roughly
  500-1,000 rollouts per hour, while the other T4 trains the LoRA. That is about 10-20K
  rollouts per 30-hour week, the same order as OpenWebRL's 54K. 4B is roughly half that.
  **WEAK** (my estimate; measure it before committing.)
- **Open risk.** Qwen3.5 uses Gated DeltaNet linear attention. I found no confirmation that
  vLLM's kernels for it run on Turing (sm_75) GPUs. Test this first. If they don't,
  generation falls back to HF generate, which is about 5-10x slower and makes online RL
  impractical. **WEAK**
- **Minimum hardware for a comfortable run.** One 24-48 GB Ampere-or-newer GPU (L4,
  A10, or a 4090/5090-class card) running a 4B model. The heavy published recipes need
  8x A100 or more.

### Reward design and filtering for AWOS

- **Outcome:** +1 if the verifier passes, 0 otherwise. Partial credit invites hacking;
  keep it binary as WebAgent-R1 and WEBSERV do.
- **Format:** −1 for an unparseable or out-of-set action, and end the episode (ARPO,
  OpenWebRL).
- **Safety:** −1 for any action the verifier flags as unsafe, regardless of the outcome.
  It must outweigh success, or the agent learns that unsafe shortcuts pay.
- **Loops:** do not add a step penalty (it encourages giving up early). Instead cap
  repeated identical actions and score that run as a failure.
- **Filtering:** before RL, run pass@8 per task and keep only tasks with 0 < pass < 1.
  During RL, drop groups with identical rewards (DAPO / OpenWebRL). Re-check pass rates
  every week as the model improves.
- **All-fail groups:** inject the oracle trajectory as one group member (ARPO-style
  replay), or a recovery splice.
- **Curriculum:** short horizon first (8 turns), then 15 (ScalingInter-RL, OpenWebRL).
  Use group size 6-8 on our budget, not 12-16. Clip 0.2 / 0.28, KL 0-0.001, no entropy
  bonus.

## 4. Best gain per GPU-hour with ~30 T4x2 hours/week

| Rank | Step | Approx. T4x2 hours | Expected gain | Why this order |
|---|---|---|---|---|
| 0 | **Build first (CPU only):** oracle-replay divergence finder, pair and splice generator, frozen held-out eval set (seen and unseen apps), pass@k task filter | 0 | enables everything | The oracle replaces the MCTS and Monte Carlo cost that dominates ETO, IPR and Agent-R |
| 1 | SFT on oracle + Claude successes + recovery splices (wrong action masked) | 3-6 | largest single gain in every paper: BC carries 50-90% of the total (R1 3B: +14 of +28; WEBSERV: +30 of +33) | Everything else builds on this checkpoint |
| 2 | First-divergence step DPO + NLL (β 0.1, 1 epoch), on-policy pairs from the current model, 2-3 rounds with eval gating | 2-4 per round | +3 to +9 over SFT (ETO, IPR, EvoCUA +3.2) | Cheap; uses the thousands of failures we already have; directly targets the wrong-control and unsafe failure types |
| 3 | Synthetic perturbation recovery (loops, giving up early) folded into steps 1 and 2 | 1-2 | fewer loops and early stops (Agent-R); unproven at 2-4B | Targets the two failure types that DPO pairs alone handle poorly |
| 4 | Feasibility spike: vLLM + Qwen3.5 on a T4, rollouts/hour, fp16 GRPO for 50 steps on 2B with LoRA | 4-6 | go/no-go decision | Check the risky assumption before spending a full week |
| 5 | Online GRPO on 2B (then 4B), tasks filtered to 0 < pass < 1, dynamic filtering, oracle injection into all-fail groups, short to long horizon | 15-25/week | +3 (WEBSERV) to +16 (OpenWebRL) over SFT/DPO | Highest ceiling, but the most compute and the most fragile; only worth it once offline gains plateau |

**Order:** 0 → 1 → 2 (+3) → 4 → 5.

**Gates:**
- Each step must beat the last on the frozen held-out eval by at least 2 points, with a
  95% CI that excludes 0.
- Stop DPO iterations at the first drop.
- Count unsafe actions separately; reject any checkpoint that increases them, even if its
  success rate is higher.

## Sources

- ETO: https://arxiv.org/html/2403.02502 · https://github.com/Yifan-Song793/ETO
- IPR: https://arxiv.org/html/2406.11176 · https://aclanthology.org/2024.emnlp-main.93
- Agent-R: https://arxiv.org/html/2501.11425
- Agent Q: https://arxiv.org/abs/2408.07199
- EvoCUA: https://arxiv.org/html/2601.15876
- RPO / IRPO (DPO+NLL): https://arxiv.org/abs/2404.19733
- NAT / Learning From Failure: https://arxiv.org/abs/2402.11651 · https://aclanthology.org/2025.naacl-long.378
- KTO: https://arxiv.org/abs/2402.01306 · STEVE: https://arxiv.org/abs/2503.12532
- Divergence-point DPO: https://arxiv.org/abs/2606.23112 · Hierarchical preference learning: https://arxiv.org/abs/2510.03253
- GUI-Reflection: https://huggingface.co/papers/2506.08012
- WebAgent-R1: https://arxiv.org/html/2505.16421v2
- WebRL: https://arxiv.org/abs/2411.02337
- WEBSERV: https://arxiv.org/html/2510.16252
- OpenWebRL: https://arxiv.org/html/2606.02031
- AgentGym-RL: https://arxiv.org/abs/2509.08755
- ARPO: https://arxiv.org/html/2505.16282v1
- DigiRL: https://arxiv.org/abs/2406.11896
- ZeroGUI: https://arxiv.org/abs/2505.23762
- UI-R1: https://arxiv.org/html/2503.21620v4 · GUI-R1: https://arxiv.org/html/2504.10458v4
- OpenWebVoyager: https://arxiv.org/abs/2410.19609
- DAPO: https://arxiv.org/abs/2503.14476
- FP16 for RL: https://arxiv.org/abs/2510.26788
- Unsloth memory-efficient RL: https://docs.unsloth.ai/new/memory-efficient-rl
- Qwen3.5 on vLLM: https://recipes.vllm.ai/Qwen/Qwen3.5-4B
