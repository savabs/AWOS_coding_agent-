# REA findings → AWOS roadmap (2026-10-16)

Source: `~/Projects/REA/investigations/` (13 targets plus Notes), written up as each folder's README.md and report.md.
Read by four parallel reviewers. Designs are summarised in our own words; no decompiled code or extracted prompts
were copied. Evidence is mostly static analysis. **None of the products ships accuracy numbers**, so every idea
here is a hypothesis to test, not a proven win.

## What it means for the question "is it the model or AWOS?"

Across Cursor, the Codex app, Devin, Amp/Droid, Kiro and Claude Desktop, **no product shows evidence that its harness
raises accuracy.** (Static analysis cannot observe accuracy and none of them publishes numbers: this is absence of
evidence, not evidence of absence.)
- Cursor's client registers a bash-only, mini-swe-agent-style executor next to its full tool set (per-model selection
  is inferred).
- What the products share is product infrastructure: isolation, resume, cost routing, safety, and scheduling.

This matches our ladder results:
- planner and gate layers hurt on Haiku 5.5 (33/40 and 34/40 vs 39 and 40);
- the plain loop ties mini-swe-agent (40/40 vs 39/40) at 0.62× the cost.

AWOS's defensible, measurable bets are therefore:

| bet | metric | why it can show AWOS value even on a strong model |
|---|---|---|
| **A. Cost routing:** cheap or local model first, escalate one way on a behavioural stuck signal | $ per solved task at equal solve rate | targets cost, not accuracy |
| **B. Local-model reliability and speed** | local solve rate, tool-call validity, tokens/s, Wh per solve | weak models are where a harness can add lift (H2) |
| **C. Long-horizon robustness** | solve rate on long tasks (Terminal-Bench tail), work lost to interruption | today's loop has no compaction and no per-turn checkpoint |
| **D. Unattended safety** | red-team blocks, zero unapproved sends or leaks | product value for background agents (not accuracy) |

## Ranked roadmap

Each item gets its own pre-registered A/B before it becomes a default, as with the ladder.

**B: local-model reliability (laptop, $0)**
1. **Grammar-constrained loop replies on the local model.** A GBNF allowing a short preamble plus exactly one bash call
   or the SUBMIT line, in llama-server's `grammar` field, reusing `edit_grammar.py` plumbing (from Apple's tool-call
   grammars). Primary: local solve rate. Secondary: tool-call validity and no-tool-call nudges. M.
2. **Byte-stable prompt prefix plus cache telemetry.** Log prompt and cached tokens per call; keep the system and tool
   text fixed and the history append-only. Primary: cached fraction and seconds per step. S.
3. **Speculative decoding.** Prompt-lookup or n-gram drafting (agents re-quote file text), or a small same-tokenizer
   draft model. First check that Qwen3.5's architecture supports llama.cpp speculation. Primary: tokens/s at equal solve rate. S to try.
4. **Persist static-prefix KV across tasks** (`--slot-save-path`, keyed by model, template and prefix hash). Primary:
   first-step latency. M.
5. **Power-aware residency:** stop llama-server after 300 s idle; on battery, use a restrictive policy (min/max merge);
   admit only model + draft + context combinations that fit memory. Primary: Wh over a fixed idle/work script. S–M.

**A: cost routing**
6. **Local- or cheap-first routing, one-way escalation to Haiku, sticky between escalations** (to keep the cache warm).
   Inspired by Factory Droid (cheapest model with P(success) ≥ 0.7) and Kiro (one-way tier escalation).
   Area: `loop_executor.py`, `escalation_engine.py`, plus a capability → provider table with a privacy class (Apple GenerativeFunctions).
   Primary: $ per solve with solve rate no worse than −1 task in 40. M.
7. **Behavioural stuck detector** (repeated identical commands, ≥ 15 of the last 20 tool results failing, repeated
   failure signature, no diff progress for K steps). It nudges, then escalates. **It never stops a run early**; early
   stopping is what lost 33/40. Do not use phrase counting. Log how often each trigger fires before claiming any lift. S.
8. **Log every route and escalation decision as a labelled example**, then calibrate `ml_router.py`'s prior. M, after #6.

**C: long-horizon**
9. **Per-turn checkpoint and resume in the loop** (write each turn to `host/journal.py`), so a job killed by the
   battery or a crash continues the same conversation (Codex JSONL sessions, Cursor content-addressed checkpoints).
   Primary: tokens wasted and solve rate under random kills. S–M.
10. **Two-stage compaction:** start a summary at about 70% of the window, swap it in at about 85%; keep the goal word
    for word and the list of edited files; tell the model how many summaries it has had; never split a tool call from
    its result (Droid; Cursor for the two-stage idea, whose real thresholds are server-set and unknown, so 70/85% are
    our own guesses). Primary: solve rate on tasks over 60 steps, together with a cost check (summaries cost cache hits). M.
11. **Keep the prompt cache warm through long test runs** (longer TTL, or a keep-alive call). Primary: cache-read ratio when the gate runs over 5 minutes. S.
12. **Layered edit tool for weak models:** exact → whitespace-insensitive → "did you mean", normalising the matched
    region only (Cursor), on `fuzzy_apply.py`. Local only. S.

**D: unattended safety (product value)**
13. **Read-only control files in the sandbox:** `.awos/`, `.claude/`, `.agents/`, hook configs, like `.git` (Codex). S.
14. **Egress proxy with a domain allowlist and credential stripping** for the sandbox (Claude Cowork). M.
15. **Secret scan on the review-branch handoff:** added lines only, fail closed (Droid Shield). S.
16. **Computer use:**
    - AX diffs sent only when shorter than the full tree, with stable IDs and a "no_change" message (ChatGPT Work with Apps).
    - Destructive and cancel button roles become an irreversible-action class in `desktop/policy.py` (Apple UIIntelligence).
    - A turn-scoped lease, with a re-read when the user takes over.
    - Two-phase prepare → approve → commit-once for every external send.
    S each.
17. **Worktree lifecycle:** an owner record, keep N worktrees, snapshot to a hidden ref before deleting, transactional merge-back (Codex). S–M.

## Explicitly not adopting (it would repeat the planner mistake, or has no AWOS use)

- Typed plan DSLs, planner/interpreter splits, Kiro-style spec phases with approval gates, mission orchestrators with
  validators at every milestone, lead/sidekick fusion. All are planner or gate layers; ours lost on Haiku.
- Tool retrieval and reranking, BM25 or LLM context engines, Merkle indexes: redundant with grep on a strong model,
  and AWOS's tool set is small. Test only on local models if ever.
- Apple engine internals that llama.cpp already covers or that need trained models (module KV splicing, ANE shape
  specialisation, speculative streaming), daemon platform machinery, a semantic output cache for code (unsafe without
  a verifier), and all of Apple Notes.

## Suggested order

1. **B1–B2 plus H2:** grammar loop and prefix discipline, then the pre-registered local-model test of `loop` vs
   `mini-local`. This is the cleanest test of whether a harness adds lift where the model is weak.
2. **A6–A7:** cost routing with a behavioural stuck detector.
3. **C9–C10:** resume and compaction, before the Terminal-Bench run (long tasks, battery).
4. **D13, D15:** cheap safety wins, then D14 and D16 as computer use matures.

## Second pass against the full write-ups (2026-10-16, cloud)

Six reviewers compared what was built (A6–A8, B1–B5, C9–C11, D13–D17) with the full private write-ups
(`savabs/REA-writeups`). Corrections went into the digest above. Being fixed now: per-rung step budget and
model-error classes before R1; context-overflow recovery; stuck-detector precision (grep exit 1 is not a failure,
polling is not a repeat, no "no progress" before the first edit); resume-time workspace check; host-side test
verification inside the sandbox with a scrubbed environment; non-git and MCP in-place runs; computer-use target
policy, emergency stop and secret masking; one secret rule set with more formats and redaction at rest; sandbox
loopback deny-list and worktree gitdir access; local task lease, prefix-drift and speculation telemetry; response
cache key.

New items, ranked (each needs its own pre-registered A/B before it changes a default):

| item | source | bet | size | where |
|---|---|---|---|---|
| Consult-a-stronger-model tool (one call, keep going on the cheap rung) as an arm beside A6's rung switch | Amp oracle, Droid self-upgrade | cost | M | cloud build, laptop A/B |
| Cheap static check (syntax/import on changed files) before accepting a submit; new lint errors after edits, capped | Devin, Cursor | cost, local | S | cloud |
| Typed loop guards: refusal → resample then escalate or INVALID; empty-reply cap; degenerate-output resample (≤ 3) | Droid | local, long | S | cloud |
| Two-phase commit with idempotency keys for `awos fix` push/PR/comment (crash + resume must not double-push) | ChatGPT sends, Codex | safety | S | cloud |
| Per-job permission profile stored with the job; unattended jobs never escalate; denial breaker (sandbox/egress/policy refusals → BLOCKED) | Codex | safety | S–M | cloud |
| Steer a running job (`awos steer`), soft interrupt at turn boundaries, keep rewritten checkpoints as new files | Codex sessions | product | S | cloud |
| Hide benchmark identifiers from the agent's environment | Cursor | validity | S | cloud |
| Scheduler that only enqueues normal jobs (jitter, per-tick cap, structured result) | Codex | product | M | cloud |
| Read-only egress zone (MITM, GET/HEAD only, no credentials) to close uploads through allowed tunnels | Claude Desktop | safety | M | cloud |
| Token-exact transcript for the local model if prefix drift is confirmed | Apple TokenGeneration | local speed | M | laptop |

Still rejected (the planner result holds): plan languages, spec phases, mission orchestrators, context engines,
semantic output caches.
