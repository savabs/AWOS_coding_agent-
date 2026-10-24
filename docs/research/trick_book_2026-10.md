# AWOS Trick Book: round 2

*Scope: local-first Gatekeeper, a 9B-class 4-bit model on a 16 GB M5, cloud DeepSeek V4 Flash on escalation, coding as the testbed and local computer use as the goal. Inputs: 20 deep reads, three red-team passes, four gap reports and a catalogue of about 95 further papers. Nearly every number below is self-reported by the paper's authors, and most come from a single run. Where the red team cut a claim down, this document uses the reduced claim. RL and fine-tuning items are marked **[DEFERRED]**: they need owner approval and are listed only as references or as baselines for choosing a model.*

---

## 1. Bottom line: the 10 highest-value tricks

| # | Trick | Expected gain | Cost |
|---|---|---|---|
| 1 | **Paired small-n statistics plus power planning.** Use exact McNemar, a Dirichlet paired posterior, and 3 paired repeats per task. | Stops false KEEPs. C2.1 (26/38 vs 20/38) is probably unresolved, p≈0.11–0.18. | Half a day, $0 |
| 2 | **Copy-constrained SEARCH block, constrained only inside the edit block, plus a fuzzy-apply ladder.** | 7B search/replace apply rate is 0.44 today (Diff-XYZ). Target ≥0.95. Solve rate gains less. | 1–2 days |
| 3 | **Loop breaker.** A self-match of ≥64 tokens, or a repeat of ≥4 lines, aborts the output, cuts it back and resamples once. | Removes most repetition-to-output-cap failures. | Hours |
| 4 | **V0+ static gate.** py_compile, ruff F-rules and identifier resolution run when an edit is applied. | SWE-agent loses 3pp without its linter, and 51.7% of its trajectories trip the linter. Costs milliseconds and no LLM call. | 1 day |
| 5 | **Skeleton or 100-line viewer instead of whole-file reads.** | Agentless locates better with skeletons (58.3% vs 53.7%) and uses about 85% fewer tokens. Aimed at the "26 turns reading" failure. | 1 day |
| 6 | **Sanitized arbiter plus discriminating-test triage.** | R2E-Gym: the judge is fooled by "the fix works"; at most 20% of generated tests discriminate and up to 10% are toxic. Expect fewer false accepts. | A prompt change plus about 50 LOC |
| 7 | **Stable-prefix KV cache plus cache-stable block masking,** and fix `estimate_cost()` to price cache hits. | Prefill 85 s → 14 s (AutoDroid-V2). DeepSeek cache hits cost about a tenth. | 1 day |
| 8 | **Compile verified tools and routines.** Flash builds a parameterized tool or Routine once, it is verified on held-out parameters plus a state probe, and the local 9B only fills in arguments. | Per step, Qwen3-8B goes from 35% to 81% when following a Routine. Expect more like +15–20pp end to end on recurring chores. | 3–5 days |
| 9 | **Deterministic postconditions plus a sink policy for computer use.** | Rule checkers reach 83.8% precision vs about 69% for LLM judges. A policy DSL cut attack success from 41% to 2% (Progent). | 3–5 days |
| 10 | **Idle-time practice.** Inject SWE-smith-style bugs into the owner's repos overnight, and let the local 9B attempt them, scored by a hidden oracle. | Gives a per-repo capability map for routing, plus strong evidence for promoting skills. No training. | About $2.50 per repo, plus idle watts |

---

## 2. The Trick Book

### 2.1 Replay and compile (T0 / T0.5 / L1)

**Verified tool compilation (LATM, ASI and SkillWeaver treated as one work item).**
- **What it does:** a strong model turns successful runs into parameterized functions. A function is admitted only if it re-executes successfully on held-out parameters; in ASI only 15.6% are accepted.
- **Evidence:** ASI's ablation on Shopping scored unverified text 32.6, verified text 39.0 and verified program 40.1. So verification is what helps; program form adds about +1pp. In LATM, weak tool users succeed when the arguments are near the surface of the request, but GPT-3.5 failed 0/5 at *making* tools. SkillsBench (independent): curated skills +16pp overall, +4.5pp for software engineering, and self-generated skills help nothing.
- **Red team:** promising, but expect skills to break tasks that previously worked; one analysis found 59% of the gross gain cancelled out this way.
- **AWOS use:**
  - Flash is the maker, offline.
  - Promotion needs a lower 95% Beta bound of at least 0.9. Note that 3/3 successes only gives a lower bound of 0.47.
  - Each skill carries preconditions: repo fingerprint and lockfile hash.
  - The program form matters for one reason: it enables zero-LLM replay, which saves dollars, seconds and watts.
  - Harden the existing `scaffold/agent/live_tool_synth.py` rather than building something new.

**Routine-guided L1 tier (Routine, Blueprint-First, COPE).**
- **What it does:** a numbered plan where every step names its tool; the small model only fills in parameters.
- **Evidence:** Qwen3-8B 35% → 81% and Qwen2.5-7B 15% → 50–60%, but this is teacher-forced, per-step AST matching on one private HR domain. Distractor Routines cost about 20pp. Blueprint-First doubled TravelPlanner pass rate.
- **Red team:** promising. The baseline was a bare prompt, so the result mixes "having any plan" with the Routine format.
- **AWOS use:**
  - Retrieve the top-1 Routine or none, never several.
  - The kernel enforces step-to-tool matching and runs a post-state probe after each step.
  - The goal is always passed verbatim alongside the Routine, never rewritten by it. This is the round-1 lesson.
  - Routines carry procedures, not fixes.

**Agentic Plan Caching (APC).**
- **Evidence:** on cache hits, 3–8B planners reach 94–99% of GPT-4o; overall cost falls 50%. Distilled templates beat raw traces (85.5 vs 72). Hit rates collapse on non-recurring work (GAIA).
- **Red team:** promising, but templates were admitted using an oracle, and accuracy dropped from 91 to 85.5.
- **AWOS use:** it competes with LATM for the same slot. Measure how often the workload actually repeats before choosing.

### 2.2 Local attempt and decoding

**Constrain only the edit block (CRANE).** Grammar is on inside the edit delimiters and reasoning stays free. Qwen2.5-Math-7B went from 29% to 38%. Tam et al.'s finding that "format hurts" mostly goes away when reasoning is left unconstrained. **Copy-constrained SEARCH** is a new AWOS twist: a line trie over the target file guarantees the SEARCH text matches. XGrammar or llguidance masking costs under 50 µs per token. Cost: an mlx-lm logits processor or a llama.cpp lazy grammar.

**Fuzzy-apply ladder.** Try in order: exact match, whitespace-normalised, relative indentation, then difflib ≥0.9 with a unique anchor. Aider reported about 9x more edit errors without flexible patching (from memory, not re-checked). Risk: an edit lands in the wrong place, so V2 must stay on.

**Loop breaker.** DRY-style penalties fight SEARCH copying, so use detection, not penalties, and only outside the edit block. The suffix-tree self-match length is a nearly free signal. Pre-register: false-positive rate ≤2% on runs that passed the gate.

**Model and quantization rules** (gap report: *Quantization Hurts Reasoning*, ACBench):
- W4 loses 1.3–2.1% at 7–8B, while W3 loses up to 14.5% and makes outputs longer.
- 4-bit KV quantization (QuaRot) cost **−58.9%** at 7B.
- Agent tasks lose 10–15% after quantization, and JSON output is more fragile than text.
- R1-distills collapse as agents (PDDL 33% → 1%).
- Rules: 4-bit floor (8-bit if it fits), no 3-bit, KV quantization only as a test arm, text edit format, local context capped at 16–24k.

### 2.3 Localization and context

**Skeleton or bounded viewer** (Agentless, SWE-agent ACI). Results: 100-line window 18.0%, full file 12.7%, 30-line window 14.3%. Files over about 300 lines open as an AST skeleton, with `show(symbol)` for bodies. This replaces an LLM scout.

**Deterministic narrowing before any LLM** (BLAgent, IssueExec, SweRank-small, catalogue). Use AST chunks prefixed with the file path, plus BM25 or a 137M embedder, plus test-trace localization. IssueExec: the existing tests cover 97% of gold files, and plugged into Agentless it raised resolved issues by +17.7%. The same output can scope V2.

**Observation masking, kept but upgraded** (Complexity Trap). It halves cost with no significant change in solve rate; LLM summarizers make trajectories about 15% longer. AWOS already ships `KEEP_TURNS=8`. Add three things:
- **Block masking:** keep the prefix byte-stable for K turns so caching works.
- **Pinning:** never mask the last read of a file that is later edited, or the last failing test.
- **Neutral stub text:** the current "re-read the file if you need it" may invite re-reads.

**Local scout** (Al Awad & Ivanov). Red team: weak. There is no baseline without an LLM and no end-to-end result, and it would take 2–5 minutes on the M5. Run only the free Stage-1 Hit@3 comparison.

### 2.4 Gate and verification

**V0+ static gate:** described in §1. Cap rejections at 2, because SWE-agent shows 23.4% of failures are loops on syntax errors.

**R2E-Gym lessons, without training.**
1. Regression filter first. On its own, regression plus judge reaches 47.4%.
2. Rank candidates on discriminating tests only. Drop tests every candidate fails and tests that fail with crashes or import errors.
3. The arbiter sees only the issue, the diff and the test logs: no thoughts and no success claims. A judge given the patch alone scored 37.6 vs 42.8 with the full trajectory, and the attention analysis shows it keys on self-praise.

The red team says the value is in hardening a single candidate. At N≈1–3 there is little to choose between.

**Differential-input arbitration (S\*).** The selector alone adds about +2pp over an LLM judge; most of S\*'s gain is coverage from N=16. Red team: weak. Use it only where AWOS already holds two candidates that behave differently (local vs cloud, or original vs repair). First count how often that happens in the ledgers.

**Flaky-grader guard** (Monkeys). 11.3% of SWE-bench Lite has flaky tests. Re-run V2 three times with a majority vote before trusting a pass or fail that decides an escalation.

### 2.5 Cascade and local–cloud collaboration

**COPE middle rung.** Before escalating the whole attempt, Flash writes a short guideline and the local 9B executes it. MBPP: 66.4% vs 64.0% for the large model alone, at about 75% lower cost. Untested on repo patches.

**MinionS-style chunked extraction.** The cloud writes extraction jobs and the local model runs them over file chunks: 5.7x cheaper while recovering 97.9% of quality. This fits the large-file reading failure and the privacy goal.

**Uncertainty triggers (ReDAct).** Uncertainty beats *random* deferral by only 1–4pp, AUC is 0.68–0.71, and the "small" models were ≥70B. Code-uncertainty work also shows that mean token probability hides the decisive tokens. Run only an offline Phase 0 on logged logprobs. Use the REPLACE span, with min-token or max-entropy aggregation, and require it to beat edit size as a predictor.

**Beta-posterior routing.** Keep a per-(task class, model) posterior of verified solve rate. Escalate when P(θ_local ≥ threshold) is low.

### 2.6 Memory and idle-time self-improvement

**Admission rule** (the experience-following study, SkillOps, *Do Agent Optimizers Compound?*, and AWOS's own experience-store failure). Admit only on strong evidence, delete entries whose retrieval correlates with failures, and run regression control inside the loop. In the Terminal-Bench study, GEPA prompts grew from 5 to 195 lines of overfit lessons; only the regression-controlled optimizer compounded (76.4% vs 66.0%).

**Idle-time practice** (SWE-smith, BugPilot, ACH, Self-Challenging CaT, Sleep-time Compute).
- Overnight, inject bugs into the owner's repos. SWE-smith: about 23 minutes and $2.47 per repo; keep only bugs that flip a passing test.
- The local 9B attempts them with a known answer. Results feed (a) the capability map, (b) skill promotion and (c) sleep-time repo cards. Sleep-time compute reports about 5x less test-time compute when queries are predictable.
- Training on this data is **[DEFERRED]**.

**ACE playbook.** Red team: weak at ≤70B (+1–2pp), and the playbooks run 10–100K tokens. Keep only its design discipline: delta updates (+17.0 vs +3.6 for monolithic rewrites), with counters credited from strong evidence only.

**GEPA / MIPROv2** on Qwen3-8B: +12.4 and +6.3 aggregate. These are cheap prompt optimizers, but only safe with a held-out regression gate.

### 2.7 Host and efficiency

- **Prefix and KV reuse** (Prompt Cache: 8–60x faster time-to-first-token; SGLang RadixAttention; KVFlow). On the M5, use MLX or llama.cpp prompt caches for the system prompt, repo card and App Card. This is probably a bigger lever than speculation.
- **Model-free n-gram or suffix speculation** (SuffixDecoding; EfficientEdit reports up to 10x on edits). Red team: on Apple-silicon quantized Metal, the best case was 1.61x and 3 of 5 configurations were *slower*. Run the temp-0 A/B and require byte-identical output. Draft-model methods compete for memory bandwidth (cross-family MLX: no gain on diverse prompts).
- **Energy accounting** (GreenBench): 3–4B models use up to 62% less energy per token than 7–9B models. That supports trying a 3B-first ladder, given ACBench's warning that Qwen2.5-3B loses up to 50% on workflow tasks.

### 2.8 Computer use

- **API-first action ladder** (UFO2, CoAct-1, TinyAgent). CoAct-1 averages 10.15 steps vs 15 for GUI-only agents. UFO2's API gain is +3–4 tasks out of 49. Round 1 already adopted this direction. Speculative batching changed success rate by about 0, so skip it.
- **Compressed AX observation** (A11y-Compressor). Tokens drop to about 22% of the raw tree. The redundancy-only arm is neutral on success; the full pipeline gained +5.1pp on a 32B model. Replace its pixel-band app rules with native AXSheet/AXDialog roles. The t vs t−1 diff doubles as a no-progress signal (open item G).
- **Getter+comparator postconditions** (OSWorld, macOSWorld, AndroidWorld templates). Flash writes `probe()`, `assert()` and 1–2 alternatives once. A probe is kept only if it fails on the starting state and passes on the verified end state, which is the same rule as V1.
- **Pixel rung only:** a fixed-crop re-ground. UI-Zoomer's adaptive crop is within 0.5pp of a fixed crop and needs 9 decodes.
- **Stuck monitor** (step-level cascade, catalogue): 93.9% accuracy from a 149M classifier, and 61% lower cost at −1.9pp. The classifier needs training **[DEFERRED]**; a heuristic version can be tried now.

### 2.9 Safety

- **Deterministic sink policy keyed on provenance** (CaMeL, Progent, AgentSpec). Rules cover egress, push, writes outside the worktree, sends and credentials. The full CaMeL split costs weak planners 21pp and is out for local use.
- **Inverted privacy split.** The cloud planner sees handles; a local tool-less parser sees private data.
- **Replay as a trusted fixed program.** Injected data cannot add steps.
- **Transactional snapshots** (APFS clones; the fault-tolerant sandboxing paper: 100% rollback at about 1.8 s overhead).
- **Irreversible-action gate.** macOSWorld deceptive pop-ups distracted agents 58–72% of the time, and LLM guards top out around 80% (OSGuard). Use allowlists and confirmations, not model resistance.

### 2.10 Evaluation

- Use Wilson/Beta intervals, exact McNemar, and a clustered Beta-Binomial by repo.
- Prediction noise dominates data noise (*Measuring all the noises*), so run 3 paired repeats per task.
- Report pass^k (tau-bench), not just pass@1.
- At n=28, only gains of about 25pp are detectable. Grow the set with SWE-rebench fresh tasks and probe contamination with issue-only file guessing (*SWE-Bench Illusion*: 76% vs 53%).
- Report **oracle pass@k minus selected solves** as the measure of gate precision.

---

## 3. Smart combinations

1. **Practice → verified compile → replay → Beta promotion.** Idle cycles on the always-on box generate tasks with known answers. Flash compiles successes into tools or Routines. Each one is promoted to T0 only when its Beta lower bound reaches 0.9. *Why it should work:* the evidence is strong (hidden oracle, held-out parameters), which fixes the experience store's failure, and each repetition pushes work toward zero-LLM replay. *Risk:* injected bugs may not resemble the owner's real chores, and a library can grow while a stale skill breaks quietly. Mitigate with precondition fingerprints and demotion after one failed probe.

2. **Copy-constrained SEARCH + n-gram speculation + loop breaker.** The constraint forces SEARCH text to be a verbatim copy. Verbatim copies are exactly what a prompt-lookup drafter accepts in long runs, and the same suffix structure exposes self-repetition. One data structure covers apply rate, speed and loop kills. *Risk:* Metal serial verification may cancel the speedup, so keep the constraint even if speculation loses.

3. **Escalation as a free experiment.** On escalation, keep the failed local candidate and the cloud candidate. If they differ in behaviour on V1, write one distinguishing test and run it. A sanitized arbiter decides from real outputs. Every escalation then updates the per-class local-was-right posterior that drives routing. *Risk:* there may be too few divergent pairs to learn from, so count them first.

4. **COPE rung + Routine format + verbatim goal + MinionS for large files.** Flash writes a tool-named guideline; the local model executes it step by step, with chunked extraction when files are large. Most tokens stay local and private. *Risk:* this is round 1's planner-rewrite failure in a new form if the guideline replaces the goal. Pre-register "goal verbatim, guideline added".

5. **Injection-safe computer-use replay.** API-first ladder, compressed AX, Flash-written postconditions, a sink policy, and a quarantined local parser for untrusted UI text. A replayed routine becomes a fixed trusted program checked by a deterministic probe. *Risk:* "data requires action" chores, where the next step depends on the content read, cannot be fixed programs and must fall back to the guarded worker.

6. **Stable prefix everywhere.** Repo card, system prompt and block masking all keep a byte-stable prefix. That gives local KV reuse (seconds and watts) and DeepSeek cache-hit pricing (dollars) together. *Risk:* none to quality, but measurement needs the `estimate_cost` fix first.

---

## 4. Traps

- **Local best-of-k (repeated sampling).** The 56% figure is oracle coverage at k=250. With imperfect verifiers, resampling cannot reduce false positives (Stroebl et al., ICLR 2026), and weak models have more of them. AWOS's best-of-3 result already showed this. Use pass@k only as a gate-precision diagnostic.
- **Live tool creation by a small model.** GPT-5-Nano fell from 44% to 14% (Live-SWE-agent). The gains need frontier models.
- **LLM summarization compactors.** They save nothing on short runs and make trajectories longer.
- **Large evolving playbooks on a 9B model** (ACE, Dynamic Cheatsheet). Gains are about 1–2pp at 70B, and context rot (Chroma) hurts small models most.
- **Draft-model speculation on the M5** (EAGLE, cross-family drafts). Memory-bandwidth contention; no gain on diverse prompts.
- **LLM-judged admission to memory or skills** (APC's oracle, ASI's self-judge, SkillWeaver's unmeasured reward model). This is what sank the experience store.
- **3-bit weights, default KV quantization, R1-distill local models.** See the numbers in §2.2.
- **LSP navigation tools.** In a measured negative result, they used 6–118% *more* tokens than grep, and models picked them in 0–6% of localization calls.
- **Hard localization gates.** Scout F1 is about 0.5, so too many correct edits would be blocked.
- **Speculative GUI batching, hybrid vision fusion, adaptive zoom.** They cut steps or add complexity with no net change in success rate.
- **Fuzzy embedding cache matching.** It is about 1000x slower and lower in accuracy than exact match in APC.
- **Prompt optimizers without regression control.** They overfit into lists of task-specific paths and error strings.

---

## 5. Experiment queue (pre-registered; ordered by value/cost)

**Shared rules for every experiment:**
- Same-day controls and paired task IDs.
- 3 repeats per task.
- **KEEP** only if P(Δ>0) ≥ 0.95 and exact McNemar p ≤ 0.05, within the stated cost budget.
- **REJECT** if P(Δ>0) ≤ 0.2.
- Otherwise **INCONCLUSIVE**, and the response is to extend n, not to ship.

**Datasets:**
- **I28:** the 28 real issues, extended to 66+ with SWE-rebench fresh tasks.
- **BK:** backupd-style series.
- **R40:** a routine-task set of recurring families with held-out parameters and 20% near-misses.
- **C10:** a 10-chore macOS computer-use set with deterministic probes.

| # | Experiment | Hypothesis / arms | Data | Primary metric | Decision rule | Box |
|---|---|---|---|---|---|---|
| E0 | Statistics re-audit | Do past KEEP/REJECT calls survive paired analysis? Arms: raw-count rule vs paired rule | Existing ledgers (re-run unpaired controls for about $1) | Count of decisions that change label | Any flip → add a task-set expansion item to MASTER_PLAN and freeze the forward rule | none |
| E1 | Edit robustness | A current; B1 copy-constrained SEARCH; B2 loop abort; B3 fuzzy ladder; B all three | I28 + BK, local 9B 4-bit | Apply rate, then solve rate | Keep B if apply rate ≥0.95, cap hits ≤ half of A, solve rate non-inferior (−1) | M5 |
| E2 | Local model shortlist | Qwen3-8B 4-bit / 8-bit / +8-bit KV; Qwen2.5-Coder-7B 4-bit; a 3B arm; Flash control | I28, one-shot + gate | Solved ÷ ($·s·Wh) with escalations priced | Pick the best objective value; reject any arm with a format-error rate more than 2x the best | M5 (30B-A3B and Devstral-24B arms need a 48–64 GB box) |
| E3 | V0+ gate + skeleton viewer | A V0; B V0+ (ruff, py_compile, identifier resolution); C B + skeleton/100-line viewer on the multi-turn path | I28 + BK | Solve rate; turns to first edit | Keep B if solves non-inferior and the false-accept rate drops; keep C if turns to first edit fall ≥30% | M5 |
| E4 | Offline selection replay + Phase 0 | On frozen pools: current arbiter vs sanitized judge vs discriminating-test triage vs hybrid; plus REPLACE-span logprob AUC | I28 pools, hidden tests as ground truth | False-accept rate; oracle gap; AUC | Adopt sanitization if it gains ≥2 issues; drop the hybrid if the oracle gap is under 2; continue the logprob trigger only if AUC ≥0.70 and it beats edit size | M5, $0 |
| E5 | Stable prefix + speculation | A none; B prompt cache + block masking; C B + `ngram-simple`; D B + `ngram-cache` | I28 prompts, temp 0 | Median decode/prefill seconds; billed $ with cache pricing | Keep if ≥1.3x faster or ≥20% cheaper, byte-identical output, no category slower by >5% | M5 |
| E6 | COPE middle rung | A escalate the whole attempt; B Flash guideline + local execution, then escalate; C random-rung control | I28 + BK | Solved ÷ ($·s) | Keep B if solves ≥ A−1 and cloud spend falls ≥30% | M5 + Flash |
| E7 | Verified tools / Routine tier | Prerequisite: 2–4 weeks of logs showing ≥20% intent repetition. Arms: local direct; local + top-1 Routine; + LATM tool with JSON arguments; tool without held-out validation; Flash direct | R40 | End-to-end hidden-check success; false replays | Keep if ≥ local direct +15pp, within 5pp of Flash, false replay ≤1%, near-miss guard catches ≥8/10 | M5 + about $2 Flash |
| E8 | Idle practice → routing | A static routing; B routing from a per-repo capability posterior built from overnight injected bugs | Owner repos + I28 held out | Escalation rate at equal solves | Keep if escalations fall ≥25% with solves ≥ A−1 | M5 overnight |
| E9 | Computer-use ladder + verifier | Arms: AX-only vs API-first + compressed AX; verifier: stored postcondition vs local 7B judge vs Flash judge (human labels) | C10 expanded to 40 | Probe-verified success; verifier precision | Keep API-first if ≥+2 tasks/40 with fewer calls; postcondition is the default gate if precision ≥ judge +10pp | M5 (Lume VM); a VLM pixel rung would need the bigger box |

**Power warning:** E1–E6 on I28 alone can only detect gains of about 25pp. Grow to 66+ tasks before treating any result under 15pp as conclusive.

---

## 6. Reading list

**Test-time compute and selection**
- S\*: https://arxiv.org/abs/2502.14382 (distinguishing-input selection; the gain is mostly coverage)
- Large Language Monkeys: https://arxiv.org/abs/2407.21787 (repeated sampling; oracle coverage)
- Limits of Resampling: https://arxiv.org/abs/2411.17501 (imperfect verifiers cap best-of-k)
- CodeMonkeys: https://arxiv.org/abs/2501.14723 (serial plus parallel compute; selection gap)
- Scaling TTC Optimally: https://arxiv.org/abs/2408.03314 (difficulty-adaptive budget)
- When to Solve/Verify: https://arxiv.org/abs/2504.01005 (self-consistency beats generative verifiers at low budgets)
- Can 1B beat 405B: https://arxiv.org/abs/2502.06703 (strategy depends on model size)
- Scaling TTC for Agents: https://arxiv.org/abs/2506.12928 (list-wise verification; selective reflection)
- Self-Certainty BoN: https://arxiv.org/abs/2502.18581 (logprob-based selection)
- Thinking vs Doing: https://arxiv.org/abs/2506.07976 (scale interaction steps; RL part deferred)
- s1: https://arxiv.org/abs/2501.19393 (budget forcing)
- BATS: https://arxiv.org/abs/2511.17006 (budget-aware tool use)

**Gate and verification**
- R2E-Gym: https://arxiv.org/abs/2504.07164 (hybrid verifier; judge bias)
- Model Cascading for Code: https://arxiv.org/abs/2405.15842 (self-tests drive escalation)
- AgentRewardBench: https://arxiv.org/abs/2504.08942 (judge vs rule-checker precision)
- Trust or Escalate: https://arxiv.org/abs/2407.18370 (calibrated judge cascade)
- Online-Mind2Web / WebJudge: https://arxiv.org/abs/2504.01382 (7B judge)
- Autonomous Evaluation and Refinement of Digital Agents: https://arxiv.org/abs/2404.06474 (judge-driven retry)
- LDB: https://arxiv.org/abs/2402.16906 (trace feedback; weak below 15B)
- Monitor-Guided Decoding: https://arxiv.org/abs/2306.10763 (LSP-constrained identifiers)

**Decoding and edit format**
- CRANE: https://arxiv.org/abs/2502.09061 (constrain only the answer block)
- Let Me Speak Freely: https://arxiv.org/abs/2408.02442 (format vs reasoning)
- .txt rebuttal: https://blog.dottxt.ai/say-what-you-mean.html (prompt-matched rerun)
- Diff-XYZ: https://arxiv.org/abs/2510.12487 (7B apply rates by format)
- XGrammar: https://arxiv.org/abs/2411.15100 (fast grammar masking)
- DOMINO: https://arxiv.org/abs/2403.06988 (token-aligned constraints)
- RPG: https://arxiv.org/abs/2505.10402 (structural repetition in code)

**Localization and context**
- Agentless: https://arxiv.org/abs/2407.01489 (skeletons; fixed pipeline)
- SWE-agent: https://arxiv.org/abs/2405.15793 (ACI, linter, viewer)
- Complexity Trap: https://arxiv.org/abs/2508.21433 (masking beats summarization)
- Al Awad & Ivanov: https://arxiv.org/abs/2608.29675 (small read-only scout)
- BLAgent: https://arxiv.org/abs/2605.17965 (path-prefixed AST RAG)
- IssueExec: https://arxiv.org/abs/2607.17286 (test-trace localization)
- SweRank: https://arxiv.org/abs/2505.07849 (137M retriever; trained)
- LocAgent: https://arxiv.org/abs/2503.09089 (code graph; fine-tuned)
- CoSIL: https://arxiv.org/abs/2503.22424 (call-graph search)
- OrcaLoca: https://arxiv.org/abs/2502.00350 (prioritized search)
- AutoCodeRover: https://arxiv.org/abs/2404.05427 (AST search APIs plus SBFL)
- SWE-Fixer: https://arxiv.org/abs/2501.05040 (two-call pipeline; trained)
- RepoGraph: https://arxiv.org/abs/2410.14684 (ego-graph context)
- LSP token study: https://arxiv.org/abs/2608.13568 (negative result for LSP)
- CodeNav: https://arxiv.org/abs/2406.12276 (weak models fail at search)
- ACON: https://arxiv.org/abs/2510.00615 (optimized compression)
- Context-Folding: https://arxiv.org/abs/2510.11967 (branch/fold; RL)
- AgentFold: https://arxiv.org/abs/2510.24699 (folding; trained)
- Context Rot: https://research.trychroma.com/context-rot (length degrades accuracy)
- EffGen: https://arxiv.org/abs/2602.00887 (prompt compression helps small models)

**Cascade and local–cloud**
- ReDAct: https://arxiv.org/abs/2604.07036 (uncertainty deferral)
- COPE: https://arxiv.org/abs/2506.11578 (plan-guided cascade)
- Minions: https://arxiv.org/abs/2502.15964 (local chunked extraction)
- AutoMix: https://arxiv.org/abs/2310.12963 (self-verify router)
- Hybrid LLM: https://arxiv.org/abs/2404.14618 (learned router)
- Advisor Models: https://arxiv.org/abs/2510.02453 (GRPO advisor; deferred)
- Faster Cascades: https://arxiv.org/abs/2405.19261 (speculative cascades)
- PAPILLON: https://arxiv.org/abs/2410.17127 (privacy delegation)
- Step-level CUA cascade: https://arxiv.org/abs/2604.27151 (stuck monitor)

**Replay, skills and planning**
- LATM: https://arxiv.org/abs/2305.17126 (strong makes, weak uses)
- ASI: https://arxiv.org/abs/2504.06821 (re-execution-verified skills)
- SkillWeaver: https://arxiv.org/abs/2504.07079 (practice, then honed APIs)
- APC: https://arxiv.org/abs/2506.14852 (plan template cache)
- Routine: https://arxiv.org/abs/2507.14447 (tool-named plans for small models)
- Blueprint First: https://arxiv.org/abs/2508.02721 (deterministic workflow engine)
- HEXIS: https://arxiv.org/abs/2609.30123 (skills compiled to EFSMs)
- AgentRR: https://arxiv.org/abs/2505.17716 (record/replay with check functions)
- AWM: https://arxiv.org/abs/2409.07429 (workflow memory)
- AppAgentX: https://arxiv.org/abs/2503.02268 (shortcut actions)
- SkillOps: https://arxiv.org/abs/2605.13716 (skill-library maintenance)
- 138K SKILL.md audit: https://arxiv.org/abs/2608.08453 (91.8% defective)
- SkillsBench: https://arxiv.org/abs/2602.12670 (curated vs self-generated skills)
- Agent Skill Framework: https://arxiv.org/abs/2602.16653 (small models choose skills poorly)
- TroVE: https://arxiv.org/abs/2401.12869 (toolbox growth)
- ToolMaker: https://arxiv.org/abs/2502.11705 (repo-to-tool)
- Live-SWE-agent: https://arxiv.org/abs/2511.13646 (live tools; hurts small models)
- CodeAct: https://arxiv.org/abs/2402.01030 (code as action)
- TinyAgent: https://arxiv.org/abs/2409.00608 (Mac function calling; fine-tuned)
- ReWOO: https://arxiv.org/abs/2305.18323 (observation-free plans)
- LLMCompiler: https://arxiv.org/abs/2312.04511 (DAG tool calls)
- Plan-and-Act: https://arxiv.org/abs/2503.09572 (planner/executor split)
- AdaPlanner: https://arxiv.org/abs/2305.16653 (assert-based refinement)
- Efficient Agents: https://arxiv.org/abs/2508.02694 (cost-of-pass ablations)

**Memory and self-improvement**
- ACE: https://arxiv.org/abs/2510.04618 (delta playbooks)
- Dynamic Cheatsheet: https://arxiv.org/abs/2504.07952 (adaptive memory)
- Experience-following study: https://arxiv.org/abs/2505.16067 (selective add/delete)
- Self-Generated ICL: https://arxiv.org/abs/2505.00234 (curated own trajectories)
- ReasoningBank: https://arxiv.org/abs/2509.25140 (strategy memory)
- Sleep-time Compute: https://arxiv.org/abs/2504.13171 (idle precomputation)
- SWE-smith: https://arxiv.org/abs/2504.21798 (bug synthesis)
- BugPilot: https://arxiv.org/abs/2510.19898 (realistic bugs)
- ACH (Meta): https://arxiv.org/abs/2501.12862 (mutation-guided tests)
- Self-Challenging: https://arxiv.org/abs/2506.01716 (verified self-tasks)
- Learn-by-Interact: https://arxiv.org/abs/2501.10893 (backward relabelling)
- SSR: https://arxiv.org/abs/2512.18552 (self-play; deferred)
- GEPA: https://arxiv.org/abs/2507.19457 (reflective prompt evolution)
- MIPROv2: https://arxiv.org/abs/2406.11695 (instruction plus demo search)
- Optimizers Compound?: https://arxiv.org/abs/2607.14004 (regression control required)
- Meta-Harness: https://arxiv.org/abs/2603.28052 (harness search)
- BetterTogether: https://arxiv.org/abs/2407.10930 (prompts plus fine-tuning; deferred)
- TextGrad: https://arxiv.org/abs/2406.07496 (textual feedback)

**Host and efficiency**
- SuffixDecoding: https://arxiv.org/abs/2411.04975 (model-free speculation)
- Lossless but Not Free: https://arxiv.org/abs/2607.17283 (Apple-silicon speculation often slower)
- EfficientEdit: https://arxiv.org/abs/2506.02780 (edit-aware drafting)
- Lookahead Decoding: https://arxiv.org/abs/2402.02057 (draft-free parallel decoding)
- EAGLE-3: https://arxiv.org/abs/2503.01840 (draft heads)
- Cross-family MLX speculation: https://arxiv.org/abs/2604.16368 (no gain when bandwidth-bound)
- SGLang: https://arxiv.org/abs/2312.07104 (radix KV, compressed FSM)
- Prompt Cache: https://arxiv.org/abs/2311.04934 (modular KV reuse)
- Hydragen: https://arxiv.org/abs/2402.05099 (shared-prefix attention)
- KVFlow: https://arxiv.org/abs/2507.07400 (workflow-aware KV eviction)
- KIVI: https://arxiv.org/abs/2402.02750 (2-bit KV quantization)
- Quantization Hurts Reasoning?: https://arxiv.org/abs/2504.04823 (W3 and KV-quant damage)
- ACBench: https://arxiv.org/abs/2505.19433 (compressed models as agents)
- SWE-Gym: https://arxiv.org/abs/2412.21139 (7–32B baselines)
- Devstral-Small: https://huggingface.co/mistralai/Devstral-Small-2505 (24B, needs a 32 GB box)
- Qwen3-Coder-30B-A3B: https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct (bigger-box candidate)
- GreenBench: https://arxiv.org/abs/2608.28667 (energy per token on Apple silicon)
- Apple runtimes study: https://arxiv.org/abs/2511.05502 (MLX vs llama.cpp)
- EET: https://arxiv.org/abs/2601.05777 (experience-driven early stop)
- PASTE: https://arxiv.org/abs/2603.18897 (speculative tool calls)
- Speculative Actions: https://arxiv.org/abs/2510.04371 (next-action speculation)
- Speculate with Memory: https://arxiv.org/abs/2607.12236 (memory speculator)
- SWE-Replay: https://arxiv.org/abs/2601.22129 (trajectory prefix reuse)
- DSP: https://arxiv.org/abs/2509.01920 (adaptive speculation; RL)
- Speculative Interaction Agents: https://arxiv.org/abs/2605.13360 (async I/O; SFT)

**Computer use**
- UFO2: https://arxiv.org/abs/2504.14603 (API-first, Windows)
- CoAct-1: https://arxiv.org/abs/2508.03923 (code before clicks)
- AutoDroid-V2: https://arxiv.org/abs/2412.18116 (script over an App Card)
- A11y-Compressor: https://arxiv.org/abs/2605.00551 (AX-tree compression)
- UI-Zoomer: https://arxiv.org/abs/2604.14113 (adaptive zoom)
- Iterative Narrowing: https://arxiv.org/abs/2411.13591 (crop loop)
- ScreenSpot-Pro: https://arxiv.org/abs/2504.07981 (high-resolution grounding)
- GUI-Actor: https://arxiv.org/abs/2506.03143 (coordinate-free head)
- GTA1: https://arxiv.org/abs/2507.05791 (action-level TTS)
- Fara-7B: https://arxiv.org/abs/2511.19663 (7B computer-use agent)
- UI-Venus-1.5: https://arxiv.org/abs/2602.09082 (small GUI models)
- GUIPruner: https://arxiv.org/abs/2602.23235 (visual token pruning)
- ShowUI: https://arxiv.org/abs/2411.17465 (UI token selection)
- Agent S2: https://arxiv.org/abs/2504.00906 (mixture of grounders)
- OSWorld: https://arxiv.org/abs/2404.07972 (checker functions)
- macOSWorld: https://arxiv.org/abs/2506.04135 (macOS tasks plus pop-ups)
- AndroidWorld: https://arxiv.org/abs/2405.14573 (parameterized state checks)

**Safety**
- CaMeL: https://arxiv.org/abs/2503.18813 (capability-tagged interpreter)
- Enterprise CaMeL: https://arxiv.org/abs/2505.22852 (deployability additions)
- Design Patterns: https://arxiv.org/abs/2506.08837 (six injection patterns)
- Progent: https://arxiv.org/abs/2504.11703 (privilege DSL)
- AgentSpec: https://arxiv.org/abs/2503.18666 (runtime rules)
- VeriSafe: https://arxiv.org/abs/2503.18492 (logic-based action verification)
- MELON: https://arxiv.org/abs/2502.05174 (masked re-execution)
- Spotlighting: https://arxiv.org/abs/2403.14720 (input marking)
- DeAction: https://arxiv.org/abs/2602.08995 (off-task action guard)
- OSGuard: https://arxiv.org/abs/2606.15034 (guards around 80% accurate)
- OS-Harm: https://arxiv.org/abs/2506.14866 (safety judge, F1 0.79)
- Transactional Sandboxing: https://arxiv.org/abs/2512.12806 (snapshot rollback)
- DeltaBox: https://arxiv.org/abs/2605.22781 (millisecond checkpoints)
- GoEX: https://arxiv.org/abs/2404.06921 (undo-based runtime)

**Evaluation**
- Don't Use the CLT: https://arxiv.org/abs/2503.01747 (small-n intervals)
- All the Noises: https://arxiv.org/abs/2512.21326 (paired repeats)
- tau-bench: https://arxiv.org/abs/2406.12045 (pass^k)
- ABC Checklist: https://arxiv.org/abs/2507.02825 (benchmark flaws)
- SWE-rebench: https://arxiv.org/abs/2505.20411 (fresh tasks)
- SWE-Bench Illusion: https://arxiv.org/abs/2506.12286 (memorization probe)

*Confidence:* high for the statistics, the static gate, the quantization rules and the deterministic-safety items. Medium for edit constraints, prefix caching and verified compilation. Low for any transfer of headline numbers to a 9B 4-bit model on the M5 before E1–E3 have been run. Several catalogue entries were checked from abstracts only, because web-search budgets ran out, and none of them were deep-read.