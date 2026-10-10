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
