# B3: full AWOS vs Aider on Claude Haiku 5.5 (2026-10-13)

Pre-registration: `docs/specs/ablation_full_awos_vs_aider_haiku_rerun.md` (d9109b9, committed before the run).

**Run:**
- Code: `claude/bench-run` @ 3373f41 and later. The Aider browser no-op fix (399fa0f) landed mid-run; it changes no scoring.
- Driver: `scripts/ablation_awos_vs_aider.py --cap 6.0 --repeat 2`.
- Data: `~/.awos-harness/b3_rerun/combined.json`.

## Result: INCONCLUSIVE under the pre-registered rule, leaning clearly toward AWOS

| | AWOS (arm `off`, full defaults) | Aider (`--max-reflections 10`) |
|---|---|---|
| Runs solved (valid) | **59 / 71 (83%)** | 46 / 69 (67%) |
| Issues where this arm did better | **14** | 4 |
| Cost per run | $0.0349 | $0.0334 (AWOS ×1.05) |

- **Exact McNemar (task level):** b = 14, c = 4, **p = 0.031**.
- **Paired Bayesian:** P(Δ>0) = **0.910**. Posterior Δ is +11.2 pp, 95% credible interval [-5.0, +27.5]. The observed per-task Δ is +18.8 pp; a plain Bayesian bootstrap gives P = 0.992. MDE ≈ 22 pp.
- **Cost:** ×1.05, within the pre-registered ≤1.5× budget.
- **Why not KEEP:** P(Δ>0) = 0.910 < 0.95. The other two conditions (McNemar ≤ 0.05, cost) are met.
- **Coverage:** 40 issues. The $6 hard cap stopped repeat 2 after 72 issue-runs ($5.73 spent); the analysis uses complete pairs only, as pre-registered.
- **INVALID runs:** Aider 2 (empty model response after 2 retries: Haiku's default thinking exhausting the output budget), dropped from both arms. AWOS 0.

## Was full AWOS actually running?

Yes, unlike B2:
- the acceptance gate ran in 71 of 71 AWOS rows;
- one-shot was used in 71 of 71;
- the planner returned ok in 46 rows, skipped small goals by design in 20, and failed in 5.

## Reading it honestly

- **Both B2 and B3 point the same way:** AWOS solves more fresh real issues than Aider on the same model, at the same cost per run. In B3 the gap is +16 pp in raw solve rate, with a 14-vs-4 issue split.
- **It does not clear our strict pre-registered Bayesian bar** (0.95), because the per-issue Beta model with about 2 runs per issue is conservative and the cap cut repeat 2 short. A third repeat or more issues would likely settle it, at about $3–6.
- **Safe public claim:** "On 40 fresh real issues (fixes merged Aug–Oct 2026), same model and same cost, AWOS solved 83% of runs vs 67% for Aider. It was better on 14 issues and worse on 4 (McNemar p = 0.03). Our stricter Bayesian test did not reach its 95% bar (91%)."
- **Do not claim** that "AWOS is proven better".
