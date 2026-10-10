# Ablation E1: the edit-reliability package (T4 fuzzy apply + T2 loop breaker)

Pre-registered 2026-10-09, before any run. Stage 1. This is the first evidence
run on the 73-issue set under the T1 decision rule
(`docs/specs/stats_decision_rule.md`).

## Why

Two failure modes keep showing up:

- **Edits that never apply.** The match fails, the indentation is wrong, or
  the path line is broken. Offline (T4), the ladder raised "placed correctly
  and still compiles" from 60% to 92% on a synthetic corpus. Its path fix
  turned a live Flash job from 0 applied edits into a solve.
- **Repetition loops to the output cap.** T2 caught 8 of 38 unsolved Aider
  replies with 0 of 70 false trips.

Both are cheap, LLM-free changes on the edit path. They are tested together
as one package, matching Trick Book E1.

## Arms (same code, `claude/bench-run` @ 6ee75dc or later)

- **Control:** current defaults (one-shot, whole-source context, acceptance
  gate mode 2 with fixed arbitration, goal verbatim; every T-flag off). The
  control arm is also the new 73-issue baseline.
- **Treatment:** control plus `AWOS_FUZZY_APPLY=1` and `AWOS_LOOP_BREAKER=1`.
- **Model:** DeepSeek V4 Flash, with the pinned providers
  `deepinfra,gmicloud,novita,siliconflow` and no fallbacks.

## Design

- **Issues:** all 73 real issues in `~/.awos-harness/real_series` (28
  original plus 45 sourced on 2026-10-09). click_3884 is flagged as lower
  confidence because its hidden tests were edited.
- **Repeats:** 1 per arm, interleaved per issue (control first, then
  treatment). The budget is about $3, so K = 1 is the affordable design.
  At 73 tasks × 1 the MDE is about 17–18 pp; smaller effects will come out
  INCONCLUSIVE.
- **Primary metric:** task-level solve, paired, scored with the T1 rule.
- **Secondary metrics:**
  - edit apply rate (failed blocks / total blocks) and fuzzy tier counts;
  - loop-guard trips;
  - one-shot cap hits;
  - billed and logged $ per run;
  - minutes;
  - $ per solved issue.

## Decision rule (written before running)

- **KEEP** (make both flags default on) if P(Δ>0) ≥ 0.95 **and** the exact
  McNemar p ≤ 0.05 **and** billed $/run rises at most 20%.
- **REJECT** if P(Δ>0) ≤ 0.20.
- **INCONCLUSIVE** otherwise. The flags stay off. If it leans positive and
  more budget is available, repeat with K = 2.

Report the components separately where the logs allow it (fuzzy tiers,
loop trips). A package verdict does not prove which part did the work.

## Result (interim, 2026-10-10): INCONCLUSIVE. Flags stay off.

68 of the 73 issues have a complete pair. The last 9 runs (toolz_634 treat, toolz_635, validators_411, wrapt_356, wrapt_357) were cut off when the OpenRouter key was revoked. Scored with `scripts/stats_audit.py --cost-budget 0.2` on `.awos/job_series_E1_combined.json`.

| | control | treatment (fuzzy apply + loop breaker) |
|---|---|---|
| solved (paired tasks) | 55/68 | 56/68 |
| billed $/run | $0.0093 | $0.0086 (×0.93) |
| minutes/run | 2.88 | 2.88 |

- **Paired stats:** P(Δ>0) = 0.542. Posterior Δ is +0.7 pp, with a 95% credible interval of [-12.3, +13.6]. Task-level exact McNemar: b = 7, c = 6, p = 1.0. MDE ≈ 15 pp.
- **The missing runs cannot produce KEEP.** Even if the treatment won all 5 remaining tasks, McNemar gives b = 12, c = 6, p ≈ 0.24 > 0.05. If control won all 5, P(Δ>0) could move toward the REJECT threshold. Finishing those pairs is optional.
- **Secondary metrics:**
  - The fuzzy ladder fired only at tier 4: 34 times, with 30 refusals. Tiers 1–3 never fired. Most failed edits are not near-misses that a fuzzy matcher can recover. That points at the model writing wrong content, not at matching.
  - The loop guard tripped 11 times. 34 one-shot replies were cut off at the output cap.
  - The treatment is about 7% cheaper per run with no solve-rate change. That is consistent with fewer runaway repetitions, but it is too small to clear the rule.
- **Decision:** INCONCLUSIVE under the pre-registered rule. Both flags stay off by default.
- **Takeaway:**
  - The edit-application path is not the bottleneck at this solve rate (81%).
  - The 34 output-cap cut-offs are a larger, separate failure mode worth its own ablation.
  - Per the atlas, single-run noise of 2–6 pp means K = 1 designs can only detect large effects.
