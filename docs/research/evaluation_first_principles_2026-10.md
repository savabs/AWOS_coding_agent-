# Evaluation and fixes from first principles (2026-10-02)

This document combines four web research tracks: evaluation rigor, cheap agent
design, error analysis and reliability, and measuring learning. The research
agents fetched and checked the sources; the arXiv IDs are as they reported
them. It builds on `docs/memory/learning_phase_2026-09-30.md`.

## First principles

1. **A result is only real if the instrument works, the sample is big enough
   and the test set was not tuned on.** All three failed for us: the Aider arm
   ran without files, n was 12 × 1, and we tuned on ordertool.
2. **Cost is mostly input tokens.** Agents read about 100 input tokens for
   every token they write (Manus), so caching and context size dominate cost.
3. **"Done" needs an independent check of the spec.** Visible tests passing is
   not proof. More tests do not help either (arXiv 2602.07900). What helps is
   spec-derived tests enforced as a gate.
4. **Memory has weak evidence.** Most measured gains are 1–5 pp and show up in
   steps and cost rather than solve rate. Gains need curated memory edits gated
   on feedback (VibeMemBench 2609.23570, ACE 2510.04618, AGENTS.md study
   2602.11988).

## Evaluation protocol

| # | Rule | Source |
|---|---|---|
| 1 | **Validate the harness before every scored run.** Three checks per job and arm: the reference solution passes the hidden tests, an empty patch fails them, and the inputs each arm received are hashed and logged. Any failure voids the run. | Agentic Benchmark Checklist 2507.02825 |
| 2 | **Run a health check on every run, and mark the run INVALID if any check fails.** Every LLM call has model, finish_reason, tokens and cost recorded. No truncation in components that write state. No call returns empty visible output. Every model is on the pinned allowlist. Every component's output is consumed. Notebook size does not drop more than X%. Spend reconciles with billing. | Hamel Husain FAQ; OTel GenAI semconv |
| 3 | **Use paired, repeated trials.** K = 3–5 per task per arm. The task is the cluster. Report mean, pass@1 and pass^K. | Miller 2411.00640; τ-bench 2406.12045 |
| 4 | **Choose the test by data shape.** For one attempt per task, use exact McNemar on discordant pairs (`binomtest(b, b+c)`). For K attempts per task, use a paired permutation test or bootstrap over tasks. | Miller |
| 5 | **Use small-sample intervals.** Report Wilson or Clopper-Pearson intervals, never ±1.96·SE, while n is below a few hundred. | Bowyer 2503.01747 |
| 6 | **Be honest about power.** At 12 tasks, p < .05 needs a discordant split of at least 6–0, 8–1 or 10–2. Our 10/12 vs 7/12 is p ≈ 0.25. The minimum detectable difference is about 39 pp at n = 12 and about 22 pp at n = 36 (K = 3). Grow to 36–50+ tasks, and use cost and turns as co-primary endpoints. | Miller |
| 7 | **Keep a held-out split.** Tune only on the dev set (ordertool). Claims come only from held-out series (salesdesk, backupd, later real repos). | Kapoor 2407.01502 |
| 8 | **Pre-register each run and change one thing per run.** | Kapoor; ABC |
| 9 | **Report cost honestly.** Use reconciled provider billing. Plot a Pareto chart of solve rate against $. Always include a simple retry baseline at the same budget. | Kapoor; HAL 2510.11977 |
| 10 | **Read transcripts.** Inspect every discordant task and every outlier before believing a result. | Anthropic, "Demystifying evals for AI agents" |

## Error analysis routine (60–90 minutes, after every run)

1. Run the health check. If it fails, stop and fix the harness.
2. Put each failed job in one view: the task, the hidden-test failure, the
   agent's own tests and the diff.
3. **Open coding.** Write one line per trace naming the *first* thing that went
   wrong.
4. **Axial coding.** Group the lines into categories, count them, and add them
   to `failure_taxonomy.md`.
5. Take the top category and write one binary check for it.
6. Choose one fix, then re-run with K trials.

Sources: Hamel Husain ("Field Guide", "Evals FAQ"); Shankar et al., EvalGen
2404.12272.

## Fix plan, by expected impact

### Cost (gap: ~$0.024 vs Aider ~$0.0024 per job)

1. **Make caching work** (≈ −$0.012–0.015 per job).
   - **Pin one caching provider.** OpenRouter load-balances V4 Flash across 16
     third-party endpoints with different caching; DeepSeek is not one of them.
     Use `provider.order` with `allow_fallbacks: false` and send a
     `session_id` per job. Calling DeepSeek directly makes a cache hit about
     50× cheaper.
   - **Keep the prompt prefix byte-stable.** `_condense_history` currently
     rewrites old turns on every call, which breaks the prefix cache. Mask in
     batches instead, roughly every 8 turns. Keep history append-only and
     serialise JSON with sorted keys.
   - **Verify** that `cached_tokens / prompt_tokens` is at least 80% from
     turn 2 onward.
2. **One-shot first** (≈ −$0.008 when it works). One call with a repo map, the
   relevant files and a search/replace edit, then run the tests. Fall back to
   the agent loop only if they fail (Aider, Agentless).
3. **Drop the planner and reviewer on small jobs** (≈ −$0.004–0.006).
4. **Cap turns and stop early.** A median of about 12 steps is enough on solved
   runs (SWE-agent). EET (2601.05777) reports −32% cost for at most −0.2% solve
   rate.
5. **Shrink context.**
   - Trim the 7.6k-token base prompt.
   - Truncate tool output when it is produced: 100-line windows, at most 50
     grep hits, test output reduced to its tail plus the failures.
   - Lint each edit before accepting it.
   - Observation masking halves cost with no loss in solve rate (JetBrains,
     2508.21433).

### Correctness ("stops when visible tests pass")

1. **Write acceptance tests from the spec before coding.** Confirm they fail on
   the old code, then gate "done" on them (Agentless; TDFlow 2510.23761).
2. **Harden the tests against mutants.** Ask what wrong implementation would
   still pass, and add a test for it. CoHarden (2607.19843) reports +9.6 pp on
   SWE-bench Verified.
3. **Add a requirements-coverage gate.** A cheap per-clause covered/uncovered
   check, validated against hand labels.
4. **Pass the goal verbatim, reuse existing helpers, and keep partial work on a
   stop.** These come from our own failure analysis.

### Memory (the moat claim)

- **Replace whole-notebook rewrites with small itemised edits.** Each entry
  carries helped/harmed counters and updates are gated on test results. Full
  rewrites cause "context collapse" (ACE).
- **Store only what cannot be derived from the code:** non-standard practices
  and pitfalls. Skip repo overviews.
- **Proof design.** Run the following arms with equal token budgets:

  | Arm | Notebook |
  |---|---|
  | A0 | No memory |
  | A1 | Static hand-written conventions file |
  | A2 | Auto-generated file at job 0 |
  | A3 | AWOS notebook |
  | A4 | Placebo notebook from another project |
  | A5 | Oracle notebook |
  | A3-frozen | AWOS notebook, frozen after job k |

  - Run several shuffled task orders and seeds.
  - Use commit replay on obscure real repos, scored only on later commits
    (Learning to Commit, 2603.26664).
  - Compounding counts as proven when A3 beats A1 and A4, and A3's gain grows
    with task position while A3-frozen's does not.
  - Detecting about a 6 pp solve-rate gain needs roughly 400+ paired
    task-runs. Cost and turns detect effects sooner.

## Order of work

1. **Instrument.** Build harness validation (rule 1), run health checks
   (rule 2) and an automatic per-run report covering the failure taxonomy, cost
   breakdown, Wilson intervals, McNemar and the Pareto chart.
2. **Baseline.** Run once on held-out backupd, with AWOS off/on and Aider,
   K = 2–3, and no new changes.
3. **Cost ablations, one per run.** Provider pin plus stable prefix, then
   one-shot first, then planner/reviewer gating.
4. **Correctness ablation.** Spec-derived acceptance tests as a gate.
5. **Memory.** Switch to itemised notebook edits, then run the multi-arm proof
   design on commit replay.
