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
