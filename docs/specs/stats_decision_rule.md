# Spec: forward stats decision rule for ablations (Trick T1)

Status: implemented 2026-10-09 (`scripts/eval_report.py` `decision()`,
`scripts/stats_audit.py`). Source: `docs/research/trick_book_2026-10.md` §1 #1,
§2.10, §5 (shared rules) and E0. VISION stage: 1 (verification of the one
worker). It does not change any runtime default.

## Why

Every ablation so far used raw run counts and pre-registered "N issues improved,
0 worse" checks with no significance requirement. With 9–28 tasks and K = 2
repeats, a 2–0 or 6–1 split of tasks happens by chance often. The repeats of one
task are correlated, so counting runs (26/38 vs 20/38) overstates the evidence.

## Research (brief)

- **Exact McNemar.** For paired binary outcomes, only discordant pairs carry
  information; the exact test is a two-sided binomial test of b on b + c at 0.5
  (Miller, *Adding Error Bars to Evals*, arXiv 2411.00640 §4; already used in
  `eval_report.py`). At n = 12, p < .05 needs a split of at least 6–0, 8–1 or
  10–2 (`docs/research/evaluation_first_principles_2026-10.md` rule 6).
- **Clustering.** Runs of one task share the task's difficulty. Miller §2.2
  recommends clustered errors; the simplest honest version is one unit per task
  (task-clustered analysis), which is what the bootstrap and permutation tests in
  `eval_report.py` already do for K > 1.
- **Two noises.** Wang, *Measuring all the noises of LLM Evals* (arXiv
  2512.21326): total noise = prediction noise (which answer a run produces) +
  data noise (which tasks were sampled), and paired prediction noise usually
  exceeds paired data noise. So (a) pair by task, (b) model within-task run
  noise, (c) more repeats per task buy real power.
- **Bayesian paired posterior.** Rubin's Bayesian bootstrap (1981): Dirichlet(1,…,1)
  weights over observed units give a posterior for a mean without a CLT. Bowyer
  et al., *Don't use the CLT in LLM evals with fewer than a few hundred
  datapoints* (arXiv 2503.01747), recommend Beta/Bayesian intervals at our
  sample sizes. Plain Bayesian bootstrap over observed task deltas is
  degenerate at small n (one improved task and no worse task gives P(Δ>0) = 1),
  so each task's per-arm rate also gets a Beta posterior (Beta-Binomial per task).
- **Wilson intervals** for single-arm rates (already in the report); never
  ±1.96·SE.
- **pass^k** (τ-bench, arXiv 2406.12045) stays a reported reliability metric;
  it is not part of the decision.

## Units

- **Unit = task.** For each task t and arm, pool the valid runs: s/n solves.
  Repeats are *not* paired by index — repeat 1 of arm A and repeat 1 of arm B
  share nothing but the task. A task enters if it has ≥ 1 valid run in both arms.
- **Δ = candidate − base**, the mean over tasks of the per-task solve-rate
  difference.
- **McNemar is computed at the task level**, not on run-level pairs:
  b = tasks where the candidate's rate is higher, c = tasks where it is lower,
  ties are concordant; p = exact two-sided binomial on b of b + c. With K = 1
  this is classic McNemar; with K > 1 it is the exact sign test on task deltas.
  Reason: run-level pairs treat correlated repeats as independent evidence
  (anti-conservative), and repeat-index pairing is arbitrary. Majority vote per
  task is ill-defined at K = 2 (1/2 ties) and discards information; the rate
  comparison keeps it.
- **P(Δ>0):** draws = 20 000 (≥ 10 000), seed 20261009. Each draw: task weights
  w ~ Dirichlet(1,…,1) (data noise); per task and arm p ~ Beta(½ + s, ½ + n − s)
  (Jeffreys prior; prediction noise); Δ* = Σ w_t (p_cand,t − p_base,t).
  P(Δ>0) = share of draws with Δ* > 0. The report also shows a 95% credible
  interval and the plain Bayesian bootstrap P (ties counted ½) for reference.
- **Cost:** mean billed $ per valid run (ledger cost when billing is missing),
  over the included tasks.

## Rule

| Label | Condition |
|---|---|
| **KEEP** | P(Δ>0) ≥ 0.95 **and** task-level exact McNemar p ≤ 0.05 **and** candidate $/run ≤ base $/run × (1 + cost budget) |
| **REJECT** | P(Δ>0) ≤ 0.20 |
| **INCONCLUSIVE** | anything else → extend n (more tasks first, then repeats); do not ship |

A proven solve gain that busts the cost budget is INCONCLUSIVE with the reason
"over budget"; the cost trade-off is then a separate, explicit decision. The cost
budget is stated in the pre-registration (default used in the re-audit: +50%,
the guardrail every ablation spec used).

Sensitivity: on the seven audited decisions P(Δ>0) moves by ≤ 0.06 between
uniform Beta(1,1) and Beta(0.2,0.2) priors; no label changes.

## Power (minimum detectable effect)

Simulated, 80% power, two-sided α = .05. Task model: base per-task rates
resampled from the five audited control arms (55 tasks: 32 always solved, 13 at
1/2, 10 never; smoothed by a Jeffreys posterior); the candidate's gain on a task
is proportional to its headroom, p' = min(1, p + δ(1 − p)/mean(1 − p)); δ is the
true mean uplift in solve rate. `python scripts/stats_audit.py --power`
reproduces the McNemar-leg column (2 000 sims, 1-pp grid); the full-rule column
was found on a 2-pp grid with 150 sims (± ~2 pp).

| Tasks | Repeats | MDE, McNemar leg | MDE, full rule (both legs) |
|---|---|---|---|
| 28 | 2 | 21 pp | ~23 pp |
| 28 | 3 | 18 pp | ~20 pp |
| 66 | 2 | 13 pp | ~15 pp |
| 66 | 3 | 11 pp | ~13 pp |
| 100 | 2 | 10 pp | ~13 pp |
| 100 | 3 | 8 pp | ~10 pp |

Reading: with today's 9–19-task ablations only uplifts of 25–40 pp are
detectable. The Trick Book's I28→I66+ expansion with K = 3 is what makes
10–15 pp effects (the size of every change we have actually seen) provable.

## Interfaces

- `eval_report.task_table(results, base, cand)` → `{task: (s_base, n_base, s_cand, n_cand)}`
- `eval_report.p_delta_positive(table, draws, seed, prior)` → `{p, mean, cri, p_bb, …}`
- `eval_report.mcnemar_tasks(table)` → `{b, c, p}` (reuses `mcnemar_exact`)
- `eval_report.decision(results, base, cand, cost_budget=None)` → dict with
  `label` and all numbers; every report gains a "Decision (stats rule)" section
  (candidate = first arm, base = second, no cost budget).
- `scripts/stats_audit.py <combined.json> --a BASE --b CAND [--cost-budget 0.5] [--json]`
- `scripts/stats_audit.py --power [--tasks …] [--repeats …]`

Tests: `tests/test_stats_decision.py`.
