# Stats re-audit of past ablation decisions — 2026-10-09 (Trick Book E0)

No new runs and no model calls. Rule and units: `docs/specs/stats_decision_rule.md`.
KEEP needs P(Δ>0) ≥ 0.95 **and** task-level exact McNemar p ≤ 0.05 within the
cost budget (+50%, the guardrail the ablation specs used); REJECT needs
P(Δ>0) ≤ 0.20; anything else is INCONCLUSIVE. Δ = candidate − base, per task.

Reproduce one row with
`python scripts/stats_audit.py <file> --a BASE --b CAND --cost-budget 0.5`
(20 000 draws, seed 20261009). C2.1 file built with
`python scripts/assemble_c21_pairs.py <bench-run>/.awos docs/memory/runs/stats_reaudit_c21_combined.json`
(same row selection as the C2.1 scoring script; it reproduces 26/38 vs 20/38).

## Decisions

| Decision (base → candidate) | File | Tasks | Runs cand vs base | Original | Stats rule | P(Δ>0) | McNemar b/c, p | MDE | Cost ×  |
|---|---|---|---|---|---|---|---|---|---|
| Ablation 4 whole source (whole0 → whole05) | ablation4_combined | 9 | 15/18 vs 13/18 | ADOPTED | **INCONCLUSIVE** | 0.71 | 2/0, 0.50 | 21 pp | 1.08 |
| Ablation C acceptance v1 (acc0 → acc1) | ablationC_combined | 11 | 16/22 vs 19/22 | REJECTED | **INCONCLUSIVE** (cost ×3.15 still disqualifies) | 0.27 | 2/3, 1.00 | 43 pp | 3.15 |
| Ablation C2 fail-safe gate (acc0 → acc2) | ablationC2_combined | 11 | 18/22 vs 15/22 | ADOPTED | **INCONCLUSIVE** | 0.76 | 2/0, 0.50 | 27 pp | 0.93 |
| Ablation A best-of-3 (n1 → n3) | ablationA_combined | 12 | 21/24 vs 20/24 | REJECTED | **INCONCLUSIVE** | 0.61 | 1/0, 1.00 | 12 pp | 1.13 |
| Ablation B experience (exp0 → exp1) | ablationB_combined | 12 | 15/24 vs 10/24 | REJECTED (promising) | **INCONCLUSIVE** | 0.84 | 6/1, 0.125 | 40 pp | 1.50 |
| AWOS vs Aider, 28 issues (aider → off) | real_issues_combined_fair | 28 | 36/56 vs 34/56 | n.s. ("parity") | **INCONCLUSIVE** | 0.60 | 8/7, 1.00 | 25 pp | 1.27 |
| C2.1 arbitration fix (ctrl → c21) | stats_reaudit_c21_combined | 19 | 26/38 vs 20/38 | KEPT | **INCONCLUSIVE** | 0.86 | 6/1, 0.125 | 22 pp | 1.37 |

MDE here is Miller's 2.8·sqrt(var(Δ_t)/n) on the observed per-task deltas
(80% power). Cost × = candidate billed $/run ÷ base. Ablation 4 and C2 were
originally judged on their target subsets (9/14 → 11/14, 7/14 → 10/14); the
control tasks are ties, so the task-level McNemar (2/0, p = 0.50) is the same
on the subset. Posterior 95% credible intervals for Δ all include 0:
ablation 4 [−20, +35] pp, C2 [−16, +35], A [−20, +26], B [−15, +41],
C2.1 [−10, +31], Aider [−17, +21], C [−38, +19].

The C2.1 controls are not same-run controls: they are the earlier ablation B
exp0 and C2 acc2 rows, paired with C2.1 by task id.

## Label changes (E0 metric)

6 of 6 KEEP/REJECT calls change label to INCONCLUSIVE (the Aider comparison was
already "not significant"). No decision reaches KEEP, and none reaches REJECT.
The largest task-level split in the whole history is 6–1 (p = 0.125); with 7
discordant tasks the best possible split, 7–0, would give p = 0.016.

## Plain-English conclusion

- **Proven: nothing.** No adoption — whole source (4), fail-safe gate (C2),
  C2.1 — has statistical support. Each rests on 2–6 tasks moving, which chance
  produces often at n = 9–19. They are **promising, not proven**. The
  current defaults stay (no code changed here); treat them as provisional.
- **Most promising:** C2.1 (P = 0.86, 6 tasks better vs 1 worse) and the
  experience store B (P = 0.84, 6 vs 1, but +50% cost). These are the first
  candidates for a confirmation run on a larger task set.
- **Rejections were not proven harmful either.** Best-of-3 (A) is a near-null
  (P = 0.61, 1 task moved), so dropping it for cost is reasonable. Acceptance v1
  (C) leaned negative (P = 0.27) and cost ×3.15, so the reject stands on cost,
  not on solve rate.
- **"AWOS ≈ Aider" is not demonstrated parity.** P = 0.60 and MDE ≈ 25 pp:
  the data cannot tell parity from a 20-pp gap in either direction.
- **What fixes it:** today's ablations can only detect uplifts of 20–40 pp.
  From the power table in the spec, about 66 tasks × 3 repeats are needed to
  prove a ~13 pp gain, and 100 × 3 for ~10 pp. Per Trick Book E0, any flip
  means: add a task-set expansion item to `docs/MASTER_PLAN.md` and use this
  rule for every forward decision. (Not edited here; owner's call.)
