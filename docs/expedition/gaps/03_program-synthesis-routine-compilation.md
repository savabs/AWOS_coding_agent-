# Gap 03: Program synthesis and library learning (compiling verified traces into deterministic routines)

*Explorer chart, 2026-10-10. Method note: the session's shared web-search budget ran out before this explorer began, so the sources below were read directly from primary pages (arXiv abstracts and HTML, Microsoft Research) whose identifiers were already known. The ledger eval was run locally on `.awos/`. Nothing here repeats the trick book's AgentRR, HEXIS, Routine or LATM entries except where they are needed for contrast.*

## Summary

The Gatekeeper's cheapest rung, verified-routine replay, maps onto two mature research lines that the expedition charts never touched:

1. **Programming-by-example / library learning (PL side).** FlashFill/PROSE, anti-unification, version-space algebras, DreamCoder, Stitch, babble and LILO. These give exact algorithms for abstracting several concrete programs into one parameterised one, and they run in milliseconds to seconds.
2. **Skill induction as code (agent side).** AWM, ASI, SkillWeaver and Voyager. These show that programmatic skills beat text skills, and that verification at induction time is what makes them safe.

The local evidence is sobering, though. **The AWOS ledger cannot currently be compiled at all**, because it records task text, outcome and cost but not the action trace or diff. On the real-issue phase, the share of verified successes that a perfect replay cache could serve at zero inference is about **13% once one heavily rerun task is excluded**. All of that 13% comes from benchmark reruns, not owner recurrence. For genuinely new work the honest number is about 0%.

## What matters

**1. Abstraction is a solved sub-problem; picking the DSL is not.**
- **Anti-unification** (Plotkin/Reynolds, 1970) computes the least general generalisation of two terms. Constants that differ become variables. This is exactly "two diffs, one with `paginate` and one with `chunk`, become one routine with a `fn_name` hole".
- **babble** (POPL 2023) runs anti-unification over e-graphs, so semantically equal but syntactically different traces still merge. It reports "better compression orders of magnitude faster than the state of the art".
- **Stitch** (POPL 2023) does corpus-guided top-down search for the abstraction that compresses a corpus most. It is 3–4 orders of magnitude faster and uses about 100x less memory than DreamCoder's compressor, with equal or better compressivity, and it can stop early and still return something usable.
- **LILO** (ICLR 2024) puts an LLM synthesiser in front of Stitch and an LLM "AutoDoc" pass behind it, which names and documents each abstraction. The documentation measurably helps the synthesiser reuse the abstractions later.
- The pattern to copy is: **LLM proposes concrete solutions → symbolic compressor finds the shared skeleton → LLM names it**. The compressor is cheap and deterministic. The LLM is used only at induction time, never at replay.

**2. Parameter and precondition inference comes from the PBE toolkit.**
- **FlashMeta/PROSE** (Polozov & Gulwani, OOPSLA 2015) uses *witness functions*, the inverse semantics of each operator, to push example constraints down a DSL grammar. It keeps all consistent programs in a version-space algebra (VSA) and then ranks them.
- It has shipped in 10+ products (PowerShell, Azure OMS, Cortana), and it works because the DSL is narrow.
- For AWOS the useful idea is that **the VSA is the generalisation boundary**. If the version space built from k traces still contains several programs that disagree on a new input, the routine is ambiguous for that input. Ambiguity is a precondition failure and must escalate, not replay.
- Preconditions should be the conjunction of whatever was invariant across the source traces: file exists, symbol signature hash, test command, a regex on the issue text. AgentRR's "check functions" are a hand-written version of the same idea.

**3. Programs beat text, but only because they are verified.**
- **AWM** (Wang et al., 2024) induces *text* workflows. It reports a 24.6% relative gain on Mind2Web and 51.1% on WebArena.
- **ASI** (Wang, Gandhi, Neubig, Fried, 2025) induces *Python* skills. It gains 23.5 points over the baseline and 11.3 points over the text-skill variant, and uses 10.7–15.3% fewer steps.
- The key mechanism in ASI is induction-time verification. The agent re-solves the task using the new skill, and the skill is kept only if three checks pass: the task succeeds by an LLM judge, the new skill is actually called, and every skill call changes the environment.
- Only **15.6%** of ASI induction attempts pass, against 31.4% for AWM's text workflows. Strictness is the feature.
- **SkillWeaver** (2025) explores websites to synthesise skill APIs: +31.8% relative on WebArena and +39.8% on real sites. APIs written by a strong agent lift a weak agent by up to 54.3%. This is the "Flash compiles, local model or no model executes" split.

**4. Drift is the real failure mode, and it is semantic, not syntactic.**
- ASI's documented failure: `sort_by_listings()`, induced on one shop, drives a dropdown, but the target site opens a sidebar. The routine was semantically right and concretely inapplicable.
- In repos the same thing happens when a refactor renames a helper or changes a signature.
- Cheap drift detection before running:
  - (a) Fingerprint every precondition anchor: the AST hash of the touched symbol and its callers' signatures, or the AX-tree path and role for UI.
  - (b) Dry-run the routine and compare the predicted diff's anchor lines against the current file. A mismatch means do not apply.
  - (c) After applying, run the same verification gate as any other rung. Replay never skips the gate.
- (a) and (b) cost milliseconds. None of the sources gives a measured drift rate over time for code repos. That is an open question below.

**5. The cost advantage is real but bounded by the verification gate.**
- **Measured locally on this machine:** launching a trivial Python routine (interpreter, regex edit, file write) has a median of about **30 ms** over 10 runs.
- **Small-model call:** decode speed is bandwidth-bound (7B Q4 runs at about 14 tok/s on M1 and about 83 tok/s on M4 Max; see chart 06). A typical 300-token edit plus a few thousand prefill tokens therefore takes several to tens of seconds on the always-on box.
- **Energy per token:** Samsi et al. (2023) measured about **3–4 J per output token** for LLaMA-65B on datacenter GPUs. Small local models are lower, but no source here gives a measured J/token for the AWOS M5 setup.
- **Implication:** a replay saves roughly 2–3 orders of magnitude of model time. However, the verification gate (the pytest run) still costs about 0.5–2 s, so end-to-end replay is only about 5–20x cheaper than a local attempt, not 1000x. Measure joules per verified task with `powermetrics`, as chart 16 already requires.

## What does not matter (for AWOS now)

- **Full DreamCoder wake-sleep.** Expensive, built for toy DSLs; Stitch and LILO replace it cheaply.
- **Library compressivity as a metric.** AWOS cares about *served-at-zero-inference and correct*, not minimum description length.
- **Bigger skill libraries.** ASI's 15.6% acceptance rate and the trick book's finding that distractor routines cost about 20pp both say a small, verified library beats a large one.
- **Text workflows as the replay format.** They still need a model at runtime, so they belong to the routine-guided L1 tier, not the zero-inference rung.

## Key resources

- https://arxiv.org/abs/2504.06821: ASI. Read the verification criteria and the cross-website failure analysis. This is the closest blueprint for the routine compiler.
- https://arxiv.org/abs/2409.07429: AWM. The text-workflow baseline, with offline vs online induction.
- https://arxiv.org/abs/2504.07079: SkillWeaver. Exploration-driven API synthesis, and strong-to-weak transfer.
- https://arxiv.org/abs/2211.16605: Stitch. A fast, anytime abstraction learner. Its Rust implementation is usable as a black box.
- https://arxiv.org/abs/2212.04596: babble. Anti-unification over e-graphs, for merging traces that are equivalent but written differently.
- https://arxiv.org/abs/2310.19791: LILO. The LLM + Stitch + AutoDoc pipeline. AutoDoc is the cheap way to name routines.
- https://www.microsoft.com/en-us/research/publication/flashmeta-framework-inductive-program-synthesis/: FlashMeta/PROSE. Witness functions and VSAs. Use VSA ambiguity as the precondition signal.
- Background without a fetched URL: Gulwani, "Automating string processing in spreadsheets using input-output examples" (POPL 2011, FlashFill); Plotkin (1970) on anti-unification; Voyager (arXiv 2305.16291) for the code skill library in Minecraft.

## Eval: how much of the AWOS ledger could be served at zero inference?

Method: replay `.awos/reward_store.jsonl` in time order. A verified success counts as servable if an identical instruction (exact match), or the same instruction template (identifiers, strings, paths and numbers abstracted, as a stand-in for anti-unification), had already succeeded earlier.

| Slice | Verified successes | Exact repeat | Template repeat |
|---|---|---|---|
| Whole ledger (May–Oct, 3,152 episodes) | 1,667 | 1,277 (76.6%) | 1,304 (78.2%) |
| Real-issue phase (since 2026-09-01) | 224 | 89 (39.7%) | 93 (41.5%) |
| Real-issue phase, excluding one comment task rerun 70 times | 154 | 20 (13.0%) | about 24 (about 15.6%) |

How to read the table:
- The headline 77% is an artefact. Synthetic smoke tasks dominate the ledger, for example "add a median function" ×126 and "fix factorial" ×126.
- In the real-issue phase, every repeat is a benchmark A/B rerun of the same issue on a reset repo. Replay would pass those by construction, which is benchmark contamination, not owner value.
- Template abstraction adds only about 4 more tasks. Real bug-fix issues are not instances of a shared skeleton at the instruction level.

**Blocker:**
- `spans.jsonl` stores goal, action, file, model and tokens. `reward_store.jsonl` stores text, success and cost. **Neither stores the diff, the tool-call sequence or the pre-state hash.**
- So not one ledger entry can be compiled today. The real servable fraction for the current ledger is therefore **0%**, and the 13% figure is an upper bound on what recording the traces would unlock.

## Implications for AWOS: a routine compiler design

1. **Record (prerequisite, about 1 day).** For every verified success, persist the instruction, the pre-state fingerprint (repo HEAD plus AST hashes of the touched symbols, or the AX snapshot for UI), the tool-call trace, the final diff and the gate command. Without this the rung is empty.
2. **Cluster.** Group successes by intent embedding plus template, and wait for k ≥ 3 members. This is the trick book's ≥20% repetition prerequisite, applied per cluster.
3. **Abstract.** Anti-unify the diffs and traces at AST level. Start with plain first-order anti-unification over Python ASTs; add babble or Stitch only if clusters are syntactically diverse. Differing leaves become parameters, typed by the role they played (symbol, path, literal).
4. **Bind parameters.** Extract each parameter from the instruction and repo with a deterministic extractor: regex or AST lookup synthesised PBE-style from the k examples. Using a model here would defeat the purpose. If the extractor is ambiguous (more than one consistent binding, i.e. a VSA with more than one program), do not replay.
5. **Infer preconditions.** Take the invariants across the k pre-states: symbol exists, signature hash class, test file present, plus a cheap AST/AX anchor probe.
6. **Verify (ASI-style).** Replay on each held-out cluster member and on synthetic near-misses (R40's 20%). Promote only if every one passes the real gate, and only when its Beta lower bound is ≥ 0.9.
7. **Serve.** Check preconditions and fingerprint, apply, then run the gate. If anything fails, demote immediately and fall through to the local model. Have Flash name each routine (AutoDoc) so that the L1 tier can also retrieve it as a guide.

**Expected value now:** near zero on the coding benchmark, which is by design one-off issues. The compiler pays only on recurring owner chores (computer use, ops), so R40 and real owner logs are the right test, not SWE-style issues.

## Open questions

- What is the measured recurrence rate of owner tasks on an always-on box over 2–4 weeks? Nothing in the literature or the ledger answers this for personal computer use.
- How fast do code-repo routines drift? No source measures routine half-life against commit rate.
- Can a deterministic parameter extractor be synthesised reliably from 3 examples of natural-language instructions, or does binding need a small model? If it needs a model, the rung becomes "near-zero" inference, not zero.
- What are the joules per verified task for replay plus gate versus a local 8B attempt on the M5? This needs to be measured, not estimated.
- Does ASI's 15.6% acceptance rate hold for coding diffs, and what is the false-replay rate on near-misses?

## Sources

- Wang, Gandhi, Neubig, Fried. Inducing Programmatic Skills for Agentic Tasks. https://arxiv.org/abs/2504.06821 (HTML v2 for the verification and failure details)
- Wang, Mao, Fried, Neubig. Agent Workflow Memory. https://arxiv.org/abs/2409.07429
- Zheng et al. SkillWeaver. https://arxiv.org/abs/2504.07079
- Bowers et al. Top-Down Synthesis for Library Learning (Stitch), POPL 2023. https://arxiv.org/abs/2211.16605
- Cao et al. babble: Learning Better Abstractions with E-Graphs and Anti-Unification, POPL 2023. https://arxiv.org/abs/2212.04596
- Grand et al. LILO, ICLR 2024. https://arxiv.org/abs/2310.19791
- Ellis et al. DreamCoder. https://arxiv.org/abs/2006.08381
- Polozov, Gulwani. FlashMeta, OOPSLA 2015. https://www.microsoft.com/en-us/research/publication/flashmeta-framework-inductive-program-synthesis/
- AgentRR: Get Experience from Practice. https://arxiv.org/abs/2505.17716
- Samsi et al. From Words to Watts. https://arxiv.org/html/2310.03003
- Local: `.awos/reward_store.jsonl`, `.awos/spans.jsonl` (eval above); `docs/research/trick_book_2026-10.md`; `docs/expedition/06_local-computing.md`

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

*Method: arXiv API (newest first, several queries before it started rate-limiting), Hugging Face paper search, GitHub repo search, HN Algolia. Every abstract below was read from its arXiv abs page. GitHub and HN turned up nothing material: one small curated list (wmmthu/awesome-llm-agent-skills-papers) and no HN discussion of trace-to-routine compilation.*

### New since the chart (dated, with URLs; most important first)

1. **SpeedRunner, "Better, Faster, Stronger: Programmatic Skill Learning Best Reduces Agent Cost"** (2026-08-11). https://arxiv.org/abs/2608.11338. Argues directly that skills written as programs give the largest *cost* reduction, because they run action sequences deterministically. SpeedRunner is a coding agent that analyses trajectories and refactors skills. The paper also claims skills can be learned "even without replay or validation". *For AWOS:* this is the closest published match to the routine rung's cost goal. Its no-validation stance conflicts with ASI and with our gate, so keep the gate. Tested only in embodied environments.
2. **CODESKILL** (2026-05-25). https://arxiv.org/abs/2605.25430. The first skill-extraction result on **SWE-Bench Verified and Terminal-Bench 2** that this chart has seen: +11.03 pass rate over no-skill and +5.10 over the best memory baseline. *For AWOS:* the skills are procedural text that guides an agent, not replayable programs. So coding issues gain from the routine-guided L1 tier, not from zero-inference replay. This supports the chart's view that replay has near-zero expected value on SWE-style issues. The manager is trained with RL (deferred per VISION), but extraction alone can be copied.
3. **SKILL-DISCO** (2026-06-25). https://arxiv.org/abs/2606.26669. Distils parameterised control-flow subgraphs (PFSMs) from successful traces and compiles them into "callable, executable, and verifiable" skills. Gains on ALFWorld and WebArena, with fewer turns. *For AWOS:* the most direct "compile traces into routines" design so far. A PFSM is a stronger replay format than a flat diff when the routine has branches.
4. **Break It Down, Pass It On** (2026-08-20). https://arxiv.org/abs/2608.20274. In a controlled study, **task-level skills mostly *lower* performance below the no-memory baseline**, while subtask-level skills raise it. **Text skills transfer better than code skills.** It also proposes a skill-utility score that needs no execution. *For AWOS:* this qualifies §3 ("programs beat text"). Code wins when it is verified and reused in the same environment (ASI). For transfer across tasks, text does better. Routines should be induced at subtask granularity.
5. **ContinualSkillBench** (2026-08-04). https://arxiv.org/abs/2608.03874. Plain in-context learning "performs comparably to explicit skill maintenance on average". Weaker models build larger, more fragmented libraries. *For AWOS:* this backs "small verified library beats large". It also warns that most apparent skill gains may be context adaptation, not reuse.
6. **SkillGen** (2026-05-09). https://arxiv.org/abs/2605.10999. Treats a skill as an intervention. It compares the same instances with and without the skill and counts both repairs and regressions. *For AWOS:* this is the right promotion test for step 6 of the compiler. It is the paired A/B the bench already runs (T1), applied per routine.
7. **SkillCommit** (2026-08-15). https://arxiv.org/abs/2608.15165. Merges skills only after cross-instance replay plus a mechanism check, and commits an abstraction only if it "preserves the validated behavior of all constituent skills". *For AWOS:* the same rule as anti-unify-then-verify-on-all-k-members in compiler steps 3 and 6.
8. **SCAFFOLD** (2026-08-31). https://arxiv.org/abs/2609.05511. Parametric executable skills under a multi-instance abstraction constraint, compacted by MDL plus behavioural-equivalence checks. +11.1 to 17.2 points over the best skill baseline on WebArena, VisualWebArena and Online-Mind2Web. *For AWOS:* the chart's LILO/Stitch pattern now works for agents. Its distillation into weights is deferred.
9. **Code-based skills in NetHack ("Up and Down the Abstraction Ladder")** (2026-09-25). https://arxiv.org/abs/2609.31076. Skills "nearly triple game progression" and **cut inference cost per episode by 86%**. Keeping primitives alongside skills preserves a fallback. *For AWOS:* the first large measured cost cut from code skills. It also supports "always fall through to the model".
10. **Task Model Induction from computer-use traces** (2026-08-20, https://arxiv.org/abs/2608.20319) and **TeleTune** (2026-10-04, https://arxiv.org/abs/2610.05437). TMI separates interleaved tasks in raw screen and keyboard traces and reconstructs 74.9% of steps, with +30% held-out accuracy. TeleTune learns from logs that have no goals and cannot be replayed. *For AWOS:* these speak to the open question on owner-recurrence logs. Raw owner traces can be segmented into tasks before clustering.
11. **Runaway Reaction / CRIME** (2026-10-05). https://arxiv.org/abs/2610.05943. Skills that each pass vetting on their own can be combined into malicious behaviour. *For AWOS:* routines need composition-level sink policy (T10), not only checks on each routine.
12. **Smaller items:** Trace2Skill (2026-03-26, https://arxiv.org/abs/2603.25158) and SkillReducer (2026-03-31, https://arxiv.org/abs/2603.29919). SkillReducer found that over 60% of the body text in 55,315 public skills is non-actionable, and that trimming skills improves quality.

### Corrections

- "It is 3–4 orders of magnitude faster and uses about 100x less memory than DreamCoder's compressor" → the abstract says "2 orders of magnitude less memory" (https://arxiv.org/abs/2211.16605). These match. No correction.
- "ASI … gains 23.5 points over the baseline and 11.3 points" → the abstract says "by 23.5% and 11.3% in success rate" (https://arxiv.org/abs/2504.06821). It does not say whether these are absolute points or relative gains. Treat "points" as unconfirmed.
- §3 "Programs beat text, but only because they are verified" → not wrong, but too strong for transfer. https://arxiv.org/abs/2608.20274 finds text skills transfer across tasks better than code skills. Scope the claim to verified reuse in the same environment.

### Confirmed claims (briefly)

- AWM: 24.6% and 51.1% relative gain on Mind2Web and WebArena (abstract).
- SkillWeaver: +31.8% and +39.8% relative, and up to 54.3% when transferred from a strong agent to a weak one (abstract).
- ASI: 15.6% verification pass rate against AWM's 31.4% (HTML v2 text). The `sort_by_listings()` dropdown-versus-sidebar drift example is quoted correctly. Step reduction is 10.7–15.3%.
- Local ledger: re-counted `.awos/reward_store.jsonl` at 3,152 episodes and 1,667 successes, which matches the table.

### Still unverified

- Routine drift rate (half-life) against commit rate in code repos. No 2026 paper measures it.
- Owner-task recurrence on an always-on box. TMI and TeleTune give tools to segment traces, but no recurrence rates.
- Joules per verified task for replay plus gate on the M5. The 30 ms launch and 3–4 J/token figures were not re-measured here.
- Whether ASI's 15.6% acceptance rate carries over to coding diffs. CODESKILL reports pass rates, not acceptance or false-replay rates.
- SpeedRunner's claim that skills work "without replay or validation" for coding repos. It was tested only in embodied environments.
- arXiv rate limits blocked queries on anti-unification and SWE-specific record and replay, so recent PL-side work (Stitch/babble successors) was not checked.
