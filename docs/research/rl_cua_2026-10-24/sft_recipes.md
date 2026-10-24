# SFT recipes for small web / computer-use agents (2024 – Oct 2026)

Date: 2026-10-24. Scope: what the literature says about supervised fine-tuning of 2–9B models
as web/computer-use agents, mapped onto the AWOS setup: Qwen3.5-2B/4B, LoRA r16/alpha32 on all
linear layers, lr 1e-4, 1 epoch, ~7.5k step examples, AX-tree text observation, one action per
turn with an optional `# reason` line. Current: Qwen 4B 61% vs Claude 87% on unseen synthetic apps.
Config read from `kaggle/rl-sft/train.py`.

Evidence labels:
- **STRONG**: several independent papers agree, or a controlled ablation at our scale.
- **MEDIUM**: one good ablation, or several papers in a related setting.
- **WEAK**: one paper, the abstract only, a different modality, or my own inference.

Numbers marked "(abstract)" were checked only against the abstract or a summary. Numbers marked
"(memory)" are from my training data and were not re-checked today, so verify them before citing.

---

## 0. TL;DR

1. **Quality beats quantity at our scale.** Judge/verifier filtering and diverse selection
   beat adding more rows. STRONG.
2. **Keep a short reason line before the action**, but write it in the student's own style
   and use it on every example. A3: removing reasoning traces cost 7.9pp on a 9B model.
   STRONG for "short reason helps", MEDIUM for "student style matters".
3. **On-policy correction data (DAgger-style) is the best tool against loops and early
   give-ups.** WebKD: a 4B student went from 30.4% to 41.1% when the teacher fixed the
   student's *own* first mistake. MEDIUM–STRONG.
4. **Diversity of sites, layouts and labels drives transfer more than volume.** STRONG.
5. **Hyperparameters:** 2 epochs is typical. LoRA lr is about 10x the full-FT lr, so 1e-4
   to 2e-4 at r16 on all linear layers is fine. Use held-out success to choose checkpoints,
   not loss. MEDIUM.

---

## 1. What most improves small-model web agents in SFT

### 1a. Data pipelines: key numbers

| Work | Model | Data | Result | Takeaway | Label |
|---|---|---|---|---|---|
| A3 / Structured distillation ([2604.07776](https://arxiv.org/html/2604.07776)) | Qwen3.5-9B (also 4B, 2B) | 2.3k judged trajectories, 16k steps, Gemini-3-Pro teacher with reduced thinking | WebArena 31.0 → 41.5%; 4B +11.1pp; 2B +6.1pp; WorkArena-L1 +18.2pp (unseen) | **No judge filter: 37.0% despite 40% more data. No reasoning traces: 33.6%.** Scaling 715 → 1.4k → 2.3k trajectories gave 37.0 → 40.2 → 41.5 (diminishing). | STRONG (controlled, our model family) |
| WebKD ([2610.19834](https://arxiv.org/html/2610.19834v1)) | Qwen3.5-4B/9B, Gemma-4-E4B | Behavior cloning (BC) on teacher data, then the teacher rewrites the student's failed suffix in the student's style | 4B: 41.1% vs 30.4% for the best baseline (m-GRPO) | Student-style edits 41.1 > teacher-style edits 33.0 > off-policy teacher takeover 17.5 > student-only edits 24.3 | MEDIUM (single paper, Oct 2026) |
| WEASEL ([2605.20291](https://arxiv.org/abs/2605.20291)) | Qwen2.5-7B, Gemma3-4B, Qwen3-8B | Selects a subset of AgentTrek/NNetNav steps by importance × diversity, and prunes the AX tree around the target | Better out-of-domain results with **9.7–12.5x less training compute** | Self-generated rationales replace expert traces for reasoning models ("style mismatch") | MEDIUM (abstract) |
| InSTA ([2502.06776v2](https://arxiv.org/html/2502.06776v2)) | Qwen3-1.7B | 150k live sites, Qwen3-235B teacher, LLM-judge filter | 11.5 → 56.9% on 3k held-out sites, above its 235B teacher | Site breadth plus a judge filter lets a 1.7B model beat its teacher | STRONG for breadth |
| Go-Browse ([2506.03533](https://arxiv.org/abs/2506.03533)) | Qwen2.5-7B | 10k successful trajectories / 40k steps, graph-search exploration, mixed teachers (GPT-4o-mini, Claude 3.7, Qwen-7B itself) | WebArena 21.7% | Structured exploration plus successes from a mix of teachers and the student itself | MEDIUM |
| NNetNav ([2410.02907](https://arxiv.org/abs/2410.02907)) | Llama-3.1-8B | Exploration, then hindsight instruction labelling | WebArena ~1 → 7.2% (controlled) vs 4.2% for instruction-first data | Labelling "what did this trajectory achieve" beats "generate a task, then try it" | MEDIUM |
| AgentHER ([emergentmind](https://www.emergentmind.com/topics/nnetnav)) | incl. Llama-3.1-8B | Relabels failed trajectories with the goals they actually reached | +7.1 to +11.7pp over success-only SFT | Failures are reusable data | WEAK (summary only) |
| Explorer ([2502.11357](https://arxiv.org/abs/2502.11357)) | 4B / 7B multimodal | 94k trajectories, 49k URLs, $0.28 each | State of the art on Mind2Web-Live at 4B/7B | URL diversity at scale | MEDIUM |
| Synatra ([NeurIPS'24](https://proceedings.neurips.cc/paper_files/paper/2024/hash/a6a6891cf1dfc64d664f086cf5976e93-Abstract.html)) | CodeLlama-7B | 100k synthetic demos from tutorials, $0.03 each | Beat GPT-3.5 on WebArena/Mind2Web (abstract) | Cheap indirect-knowledge synthesis works, but quality per example is low | MEDIUM |
| AgentTrek (memory) | Qwen2.5 7B/32B | Tutorial-guided replay, ~$0.55/trajectory | Gains on WebArena and ScreenSpot | Same family as Synatra | WEAK |
| OS-Genesis (memory) | Qwen2-VL-7B / InternVL | Reverse task synthesis: explore first, then write the task | AndroidWorld roughly doubles vs task-first data | Agrees with NNetNav: hindsight tasks > imagined tasks | MEDIUM |
| PAE (memory) | LLaVA-7B/34B | Proposer–agent–evaluator, autonomous RL | WebVoyager ~+10pp absolute on 34B | A verifier/evaluator in the loop is the key ingredient | MEDIUM |
| AutoWebWorld ([ICML'26 summary](https://atoms.dev/blog/icml-2026-autowebworld-verifiable-web-environments)) | 7B | 29 FSM-generated sites, 11.7k verified trajectories, $0.04 each | WebVoyager 27.4% | Synthetic, programmatically verifiable sites transfer to real ones | MEDIUM |
| Verified synthetic web envs ([2608.21898](https://arxiv.org/abs/2608.21898)) | PPO policies | 500 envs in 6 domains; verify-and-repair raises feasible tasks 48.6 → 94.8% | Better transfer to WebArena/WebShop/MiniWoB++ | **Broken or impossible synthetic tasks quietly poison training** | MEDIUM (abstract) |
| Fara-7B ([2511.19663](https://arxiv.org/abs/2511.19663)) | Qwen2.5-VL-7B | FaraGen: 145k verified trajectories, ~$1 each, 3 verifiers | Best 7B on WebVoyager / Online-Mind2Web (self-reported) | Multi-verifier filtering; the model learns to stop at "critical points" (like our `needs_user`) | MEDIUM |
| OpenCUA / AgentNet ([2508.09123](https://arxiv.org/html/2508.09123v3)) | 7B–72B VLMs | 22k human desktop trajectories plus reflective CoT | 72B: 45.0% OSWorld-Verified | State–action pairs alone plateau; reflective CoT 11.5 → 15.3%; cross-domain data 9.8 → 18.5% | STRONG (many ablations) |
| AgentTuning (memory) | Llama-2 7–70B | 1.9k agent trajectories mixed with general chat data (agent loss weight η≈0.2) | Agent-only training hurt held-out tasks; the mix kept generality | Mix in some general data | MEDIUM |
| WebRL (memory) | Llama-3.1-8B, GLM-4-9B | Self-evolving curriculum plus online RL with an outcome reward model | WebArena-Lite 4.8 → 42.4% (8B) | After SFT, RL with a verifier is the next big jump | STRONG |
| UI-TARS / Aguvis (memory) | 2–72B VLMs | Grounding stage, then planning with "inner monologue"; UI-TARS adds reflection/DPO on error-correction pairs | Inner-monologue and reflection ablations are positive | Screenshot-based; the lessons on reasoning and recovery carry over | MEDIUM |
| WebDancer, WebSynthesis (memory) | 7–32B | Deep-research ReAct / world-model-simulated MCTS trajectories | WebSynthesis: ~4k simulated trajectories rival much larger real datasets | Fits the "quality > quantity" pattern; WebDancer targets a different task type (research) | WEAK |

### 1b. Synthesis by question

| Question | Answer | Label |
|---|---|---|
| Quality vs quantity | At ≤20k steps, a verifier/judge filter and diversity-aware selection beat raw volume. A3: unfiltered data +40% made results worse (−4.5pp). WEASEL: a small selected subset beats the full set out of domain. Volume pays only when it also adds new sites (InSTA, Explorer, OpenCUA). | STRONG |
| Teacher distillation vs self-generated (RFT/STaR) | Teacher data raises the ceiling. Self-generated successes are on-policy and match the student's style, but only reinforce what it can already do. The best recent results combine the two: **the teacher corrects the student's own rollouts, in the student's style** (WebKD 41.1 vs 33.0 for teacher style vs 24.3 for student-only). Go-Browse mixed roughly 30% student-sampled successes with 70% teacher successes. | MEDIUM–STRONG |
| Mixing ratios | No clean study for web agents. AgentTuning used a general-data mix (agent loss weight about 0.2). OpenCUA used 35% general text (a small +1pp). Scripted demos are clean but low-diversity: treat them like "format" data and **cap their share**, since a model trained on them learns the script's habits. | WEAK–MEDIUM |
| Hindsight vs task-first synthesis | Exploring first and then labelling what was achieved (NNetNav, OS-Genesis, Go-Browse, Explorer) beats inventing tasks first. Relabelling failures (AgentHER) adds data cheaply. | MEDIUM |
| Step vs trajectory-level | Almost every system trains **one example per step** (goal + current observation + short history → next action), sampled from whole successful trajectories. Trajectory-level filtering (keep only verified successes) matters more than the unit of loss. WEASEL selects at step level and gets a 10x saving. | MEDIUM |
| Observation format | Text-only small models: the AX tree is the standard (WebArena, BrowserGym, Go-Browse, A3, WEASEL). **Pruning the AX tree** (WEASEL) cuts cost and noise. Screenshots matter for visual or pixel-only UIs, which is not our case. | MEDIUM |
| History length | Past actions as short text are enough. OpenCUA: 3 to 5 images gave only marginal gains, and longer text history gave "no benefit and reduced training efficiency". Keep the last ~5 actions plus a one-line summary, not past observations. | MEDIUM |
| Action space | Small, typed, element-indexed actions (`click(n)`, `fill(n,"x")`) are the norm for text agents. Fara and UI-TARS add explicit stop/hand-off actions (like our `needs_user`). Recovery primitives (back, scroll, wait) are cheap and reduce dead ends. | MEDIUM |

---

## 2. Reasoning traces for 2–9B agents: help or hurt?

| Evidence | Finding | Label |
|---|---|---|
| A3 ([2604.07776](https://arxiv.org/html/2604.07776)), Qwen3.5-9B | Removing reasoning traces: WebArena 41.5 → 33.6 (−7.9pp). A teacher with a **reduced** thinking budget gave better data (higher teacher success, more compact traces). | STRONG (our family) |
| OpenCUA ([2508.09123](https://arxiv.org/html/2508.09123v3)), 7B | Reflective CoT 15.3 vs short CoT 11.5. Training on mixed levels (L1/L2/L3) gave 18.5 vs 13.1 for L2 only. Inference with the mid-length L2 format was best (18.5 vs L1 16.9). | STRONG |
| WebKD, 4B | Rationales in the *teacher's* style lose 8pp vs the student's own style. A KL anchor on rationale tokens helps (+2.6pp). | MEDIUM |
| WEASEL | Expert traces hurt reasoning-native models through style mismatch, so it uses self-generated rationales instead. | MEDIUM (abstract) |
| TTI "Thinking vs Doing" ([2506.07976](https://arxiv.org/abs/2506.07976)), Gemma-3-12B | Gains come from **more interaction steps** (explore, backtrack) rather than longer per-step thinking. | MEDIUM |
| FireAct / Agent-FLAN (memory) | ReAct-format fine-tuning beats action-only. Agent-FLAN warns that format is learned fast while reasoning is learned slowly, and that format overfitting causes hallucinated actions. | MEDIUM |

**Verdict for AWOS.** A *short* reason (one line, ≤25 tokens) before the action helps small
models. Long CoT costs tokens and latency and gives little extra at 2–4B. The main risks are
(a) inconsistency (some examples have the reason line and some don't) and (b) style mismatch
(Claude's phrasing on Claude rows next to scripted phrasing on scripted rows). Fix both by
regenerating every reason line in one template. STAR-style rationalization works for this:
give the student the gold action and ask it "why". Templates that fit the failure modes:
`# goal needs <X>; <control n> is <label> -> fill` or `# page unchanged after click(n) -> try <alt>`. MEDIUM.

---

## 3. Epochs, LoRA, learning rates, plateau

| Item | What worked | Source | Label |
|---|---|---|---|
| Epochs | 2 epochs (A3, 9B full fine-tune, 16k steps). OpenCUA used 3 epochs for multi-image samples. 1 epoch is usually under-trained at <20k examples. | A3, OpenCUA | MEDIUM |
| LR (full FT) | 1e-5 (A3), 2e-5 (OpenCUA-7B) | same | STRONG |
| LR (LoRA) | Best LoRA lr ≈ **10x the full-FT lr**, roughly independent of rank thanks to the 1/r scaling. That puts our r16 at 1e-4 to 2e-4. | [LoRA Without Regret](https://thinkingmachines.ai/blog/lora/), [TRL guide](https://huggingface.co/docs/trl/main/en/lora_without_regret) | MEDIUM–STRONG |
| Targets | All linear layers, MLP included. Attention-only LoRA underperforms even at matched parameter count. We already do this (including GatedDeltaNet projections). | LoRA Without Regret | STRONG |
| Rank | Rank only binds when the dataset exceeds adapter capacity. At 7.5k examples (~1–2M target tokens), r16–32 should not be the bottleneck. | LoRA Without Regret | MEDIUM |
| Batch | LoRA is somewhat more sensitive than full FT to large batches, and raising rank does not fix this. Our effective batch of 16 is fine. | LoRA Without Regret | MEDIUM |
| Loss as a signal | Training/validation loss **poorly predicts** agent success. Flash-generated data had lower loss but performed worse. | A3 | MEDIUM |
| Plateau | A3: +3.2pp from 715 → 1.4k trajectories, then +1.3pp to 2.3k. OpenCUA: 3k → 10k in-domain trajectories +72% relative. Gains continue as long as **new domains** are added and flatten within a fixed domain. At our ~1k trajectories / 7.5k steps we are probably on the steep part for *new app types* and the flat part for *more of the same*. | A3, OpenCUA | MEDIUM |
| Overfitting signs | Exact-match action accuracy on held-out steps rises while end-to-end success on unseen apps falls. Replies copy scripted phrasing. `done` comes earlier, or the model repeats a memorized click order on new layouts. Agent-FLAN: hallucinated or invalid actions rise. | Agent-FLAN, A3, inference | WEAK–MEDIUM |

---

## 4. What made agents transfer to unseen sites

| Factor | Evidence | Label |
|---|---|---|
| Breadth of sites | InSTA (150k sites → +45pp on held-out sites). Explorer (49k URLs). OpenCUA: adding Windows/Mac data to Ubuntu data doubled Ubuntu results (9.8 → 18.5). | STRONG |
| Shared primitives, not site tricks | A3 trained on 6 WebArena sites and still gained +18pp on WorkArena. The authors credit shared form, table and search primitives. | MEDIUM |
| Diverse selection | WEASEL balances diversity across states, sites and interaction patterns, and improves out-of-domain results at 10x less compute. | MEDIUM |
| Verified, feasible tasks | Verified synthetic environments (feasible tasks 49 → 95%) give better transfer. AutoWebWorld's FSM-verified sites transfer to WebVoyager. | MEDIUM |
| Hindsight-labelled goals | Goals match what the UI actually allows (NNetNav, OS-Genesis), which reduces "misread goal" training noise. | MEDIUM |
| Label/ARIA noise, distractors | No clean web-agent ablation found. Standard domain-randomization logic applies: paraphrase labels, shuffle layouts, add decoy buttons and missing or duplicate ARIA names. WebKD shows unseen *interface versions* stay within 5pp when training is on-policy. | WEAK |
| On-policy training | WebKD (gap under 5pp from seen to unseen versions) and WebRL/TTI (RL) generalize better than pure BC on teacher data. | MEDIUM |

---

## 5. Recommendations for next week (30 Kaggle T4x2 GPU-hours)

Budget math from `train.py`: 2 epochs on 5.9k rows took ~9h, so **~0.75 GPU-h per 1k
row-epochs** at a ~1.1k-token prompt. One epoch on 7.5k rows is ~5.7h. Cutting prompt length
is a direct multiplier on everything else.

Ranked by expected gain per GPU-hour. Each item costs about one training run; the cost in
brackets is the GPU budget.

| # | Experiment | Why (evidence) | Cost | Expected gain | Measure |
|---|---|---|---|---|---|
| 1 | **Clean and rebalance the data (CPU) + prune the AX tree, then retrain 1 epoch.** Keep only verified successes (re-run the app checker on every trajectory). Drop trajectories with loops or redundant steps. Dedupe near-identical scripted steps. Cap scripted demos at ≤30% of rows. Prune the AX tree to the ~40 controls nearest the target or goal terms, plus a "N more controls" line. | A3 judge (−4.5pp without it), WEASEL (10x compute saving), verified envs | ~3h (shorter prompts) | +3–6pp, and every later run gets cheaper | Success on unseen apps (≥3 seeds, Wilson CI); held-out step exact-match; tokens/step |
| 2 | **One consistent short reason line on every row**, regenerated in a single template. Use STaR rationalization by the 4B itself, with Claude as a fallback when the 4B's reason contradicts the action. | A3 (−7.9pp without traces), OpenCUA, WebKD/WEASEL (style) | ~3h | +3–8pp; fewer wrong-control errors | Wrong-control rate; rate of reasons that contradict the action |
| 3 | **On-policy correction round (DAgger / WebKD-lite).** Roll out the new 4B on *training* apps, find the first bad step in each failure (loop, wrong control, early `done`/`needs_user`), and have Claude give the corrected action plus a one-line reason from that exact state, in our template. Add ~1–2k correction steps and retrain. | WebKD 30.4 → 41.1 at 4B; recovery-data papers (RoTS, RS-Claw) | ~2h rollouts + ~4h train | **+5–10pp, aimed at loops and early give-ups** | Loop rate (same action on an unchanged observation twice); premature-stop rate; mean steps on successes |
| 4 | **LR/epoch sweep on the cleaned set:** 2 epochs at lr 2e-4 vs 1 epoch at 1e-4. Keep r16 on all linear layers. Save mid-run checkpoints and pick by unseen-app success, not loss. | LoRA w/o Regret (10x rule), A3 (2 epochs, loss is a poor signal) | ~5h | +1–4pp | Success per checkpoint; gap between seen-app and unseen-app success (overfitting) |
| 5 | **Diversity augmentation in the app generator (CPU), then retrain.** Paraphrase labels and synonyms, shuffle layout and order, add decoy and disabled buttons, add ARIA noise (missing, duplicate or placeholder-only names), add goals phrased indirectly. Add new app *types*, not more instances of old ones. | InSTA, OpenCUA cross-domain, verified envs; ARIA noise itself is WEAK | ~5h | +2–6pp on unseen apps | Unseen-app success split by perturbation type; misread-goal rate |
| 6 | **Hindsight relabelling of failed rollouts** (the free by-product of #3). Relabel each failure's goal as "what it actually achieved" when the checker confirms it, and add those as successes. | NNetNav, AgentHER | ~0 extra (folds into #3/#5) | +1–3pp | Same as above |
| — | Reserve: reruns, final eval of the best stack, a 2B check of the same recipe | | ~5h | | |

Not this week (higher cost or weaker evidence per GPU-hour on T4s):
- Online RL (WebRL/TTI-style GRPO). This is the big next step once SFT plateaus. STRONG evidence,
  but it needs fast rollouts.
- Step-DPO on wrong-control pairs. WEAK–MEDIUM evidence.
- QLoRA on a 9B model. A 9B model in fp16 does not fit a 16GB T4.
- Long CoT.
- Screenshots.

### How to measure (keep it fixed for every run)

1. **Fixed unseen-app suite, ≥100 tasks, 3 seeds, Wilson 95% CI.** At n=100, ±9pp is noise, so
   do not accept a gain under ~5pp without a rerun.
2. **Failure taxonomy per run:** wrong control / loop / premature stop or `needs_user` / goal
   misread / invalid action. Compute it automatically from traces: loop means the same action
   on an unchanged observation; premature means `done` while the checker fails.
3. **Cheap proxy while training:** greedy exact-action match on held-out *unseen-app* steps
   (already logged by `train.py`). Check that it ranks checkpoints the same way end-to-end
   success does before trusting it (A3 warns that loss does not).
4. **Cost:** tokens/step, GPU-h/run, latency/step on the laptop. The objective is work per
   dollar·second·watt.
5. **One change per run where the budget allows.** Otherwise stack in the order #1 → #2 → #3 and
   keep each intermediate checkpoint so the gains can be attributed.

---

## Sources

A3 structured distillation https://arxiv.org/abs/2604.07776 ·
WebKD https://arxiv.org/abs/2610.19834 ·
WEASEL https://arxiv.org/abs/2605.20291 ·
InSTA https://arxiv.org/abs/2502.06776 ·
Go-Browse https://arxiv.org/abs/2506.03533 ·
NNetNav https://arxiv.org/abs/2410.02907 ·
Explorer https://arxiv.org/abs/2502.11357 ·
Synatra https://arxiv.org/abs/2409.15637 ·
OpenCUA https://arxiv.org/abs/2508.09123 ·
Fara-7B https://arxiv.org/abs/2511.19663 ·
Verified synthetic web envs https://arxiv.org/abs/2608.21898 ·
VeriEnv https://arxiv.org/abs/2603.10505 ·
SynthAgent https://arxiv.org/abs/2511.06101 ·
TTI https://arxiv.org/abs/2506.07976 ·
LoRA Without Regret https://thinkingmachines.ai/blog/lora/ ·
RoTS (GUI error recovery, ICML'26) https://proceedings.mlr.press/v306/bu26b.html ·
From memory, not re-checked today: AgentTuning (2310.12823), Agent-FLAN (2403.12881), FireAct
(2310.05915), WebRL (2411.02337), PAE (2412.13194), OS-Genesis (2412.19723), AgentTrek
(2412.09605), UI-TARS (2501.12326), Aguvis (2412.04454).
