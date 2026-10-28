# Experiment 1 rerun: full AWOS vs Aider on Claude Haiku 5.5 (B3)

Pre-registered 2026-10-12, before any run, on `claude/bench-run` @ 3373f41. The owner approved the run with a hard cap of about $6.

## Question

On the same model, does the full AWOS system solve more fresh real issues than a strong minimal harness (Aider), at acceptable cost?

## Why rerun B2

B2 (`docs/memory/runs/awos_vs_aider_haiku_2026-10.md`) came out INCONCLUSIVE: 37/38 vs 26/38, McNemar 12–1, P(Δ>0)=0.946. Three problems make it unreliable:

1. **AWOS ran crippled.** The planner returned 400 on every run, and the acceptance gate and one-shot were inactive. Fixed in 3373f41 (`claude_compat`).
2. **About half of AWOS's lead came from Aider's empty responses.** Haiku 5.5's default thinking used up the output budget, and those runs were scored "not solved". Now they are retried, then marked INVALID (906364b).
3. **One repeat only.**

## Arms (interleaved per issue: AWOS then Aider, repeat 1 then repeat 2)

- **AWOS:** job_series arm `off`, full defaults as shipped:
  - planner `auto` (skips small goals by design);
  - acceptance gate mode 2;
  - one-shot on;
  - notebook and experience off;
  - goal check off (job_series pins `AWOS_GOAL_CHECK=0`; the hidden tests judge);
  - utility calls at `AWOS_REASONING_<ROLE>=low` effort (the existing default); the agent loop at Haiku's default effort.
- **Aider:** job_series arm `aider`, `--max-reflections 10`, Haiku's default effort, same max output tokens as before.
- **Model:** both on `claude-haiku-5-5`, direct Anthropic, OpenRouter off (`AWOS_SERIES_MODEL=claude-haiku-5-5`).
- **Every AWOS row records `components`,** showing which of planner, acceptance and one-shot actually ran.

## Tasks

The fresh set: `~/.awos-harness/fresh_series`, 40 issues whose fixing PRs merged on or after 2026-08-01. Every issue the driver accepts after preflight is used. If an issue is INVALID in one arm, it is dropped from both for that repeat, as the stats rule requires.

## Design and budget

- 2 repeats.
- Driver: `scripts/ablation_awos_vs_aider.py --cap 6.0 --repeat 2 --min-issues 15`.
- The cap is a hard total across both arms and all retries. If it binds, the analysis uses complete pairs only, and the report says so.

## Primary metric and decision rule (T1, `docs/specs/stats_decision_rule.md`)

Task-level paired solve, AWOS (b = `off`) vs Aider (a = `aider`), scored by `scripts/stats_audit.py --a aider --b off --cost-budget 0.5`.

- **AWOS ADDS VALUE (KEEP)** if all three hold:
  - P(Δ>0) ≥ 0.95;
  - exact McNemar p ≤ 0.05;
  - AWOS cost per run ≤ 1.5× Aider's (the 0.5 budget; AWOS runs more machinery by design).
- **AWOS DOES NOT ADD VALUE (REJECT)** if P(Δ>0) ≤ 0.20.
- **INCONCLUSIVE** otherwise.

## Secondary metrics (reported, not decided on)

- $ per solved issue, minutes per issue.
- Component activity rates.
- INVALID count per arm, by reason.
- Per-issue disagreements.
- A sensitivity analysis counting Aider's empty-response INVALIDs as unsolved, to bracket the old B2 framing.

## Known limitations, stated in advance

- One model only.
- Fresh issue goals were written to match the hidden tests, so grading is tied to implementation details.
- The AWOS planner's `auto` mode may skip planning on most fresh issues. That is the shipped behaviour and is what we test.

## Result (2026-10-13): INCONCLUSIVE, leaning AWOS

AWOS solved 59/71 runs and Aider 46/69. Task-level McNemar: AWOS better on 14 tasks, worse on 4, p = 0.031. P(Δ>0) = 0.910, below the 0.95 bar. Cost per run is ×1.05. The cap stopped the run at 72 issue-runs ($5.73). Aider had 2 INVALID runs (empty responses); AWOS had 0. Full AWOS components ran: acceptance 71/71, one-shot 71/71, planner ok 46, skipped 20, failed 5. Details: `docs/memory/runs/b3_full_awos_vs_aider_2026-10-13.md`.

## Extension addendum (pre-registered 2026-10-13, before any extension run)

The interim result above was INCONCLUSIVE (P=0.910, McNemar p=0.031). The owner asked for an extended run to settle it. The plan is fixed now, before running:

- **Data:** complete repeat 2 (the 8 issues the cap cut) and run a full repeat 3 on all 40 issues. Same arms, same model, same code path (`claude/bench-run` @ 0cf720c), same resumable driver and output dir.
- **Hard total cap:** $10.50 for all B3 data. If it binds, use complete pairs only.
- **Analysis:** the same T1 rule (`stats_audit.py --a aider --b off --cost-budget 0.5`), applied ONCE to all B3 data after the extension finishes. No early stop on a favourable interim look.
- **Disclosure:** this is a single planned extension after one interim look. The report will state that, and also give the repeat-3-only result as a fresh-data check.

## Final result after the pre-registered extension (2026-10-14): INCONCLUSIVE by 0.001, leaning AWOS

All B3 data: 3 repeats × 40 issues, 120 issue-runs, $9.49 of the $10.50 cap. One T1 analysis, as pre-registered:
- AWOS solved **102/120 runs** and Aider 81/116 (4 Aider runs INVALID).
- Task-level exact McNemar: AWOS better on **16** tasks, worse on 4, **p = 0.012**.
- P(Δ>0) = **0.949**, against a bar of 0.95. Posterior Δ is +12.6 pp, 95% credible interval [-2.4, +27.4]; the plain Bayesian bootstrap gives P = 0.990.
- Cost per run is **×0.98**: AWOS is slightly cheaper.
- **Verdict: INCONCLUSIVE.** The rule misses by 0.001. We do not round.

Fresh-data check, repeat 3 only: 33/38 vs 26/38, better on 10 tasks and worse on 3, McNemar p = 0.092, P = 0.842, cost ×0.91. Same direction, too few runs to decide alone.

Disclosure: this is one planned extension after one interim look (P = 0.910).
