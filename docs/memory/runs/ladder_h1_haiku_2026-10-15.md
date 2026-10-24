# Harness-lift ladder H1 (Haiku): AWOS vs mini-swe-agent — REJECT

Pre-registration: `docs/specs/harness_lift_ladder.md` (amendment 1). Run on
2026-10-14/15 on GitHub Actions (macos-14, 8 shards), run 38641388149 on
`bench-cloud/ladder-h1`, engine commit **0482183**. The 8-issue laptop pilot
(`~/.awos-harness/ladder_haiku_laptop_pilot`) is not counted.

## Result

| arm | solved | $ / run | median min / run |
|---|---|---|---|
| AWOS (`off`) | **33/40** | $0.0284 | 0.86 |
| mini-swe-agent 2.4.6 (`mini`) | **39/40** | $0.0192 | 1.92 |

- `stats_audit.py combined.json --a mini --b off --cost-budget 0.5`:
  **REJECT**. AWOS vs mini: P(Δ>0) = 0.189; posterior Δ −7.5% (95% CrI −24.3 to +9.3);
  observed per-task Δ −15.0%; exact McNemar b = 1 (AWOS only), c = 7 (mini only), p = 0.070;
  MDE ≈ 18.9%; cost ×1.48.
- Total spend $1.90 (cap $5). 40/40 issues done.
- Integrity: 0 fatal health violations; mini sandbox on in 40/40; no mini log
  mentions hidden tests or the series dir.

## Why AWOS lost (7 tasks mini solved and AWOS did not)

| task | cause |
|---|---|
| astroid_3286 | success gate ran only name-mapped tests (17 passed); the edit broke 61 other visible tests. The acceptance start run timed out, so the gate was inactive |
| click_3818, markdown-it-py_433 | acceptance kept 0 tests (gate inactive); the one-shot edit passed the visible tests and shipped incomplete |
| sqlglot_8259 | 1 kept acceptance test passed; the hidden tests needed more |
| sqlglot_8574 | acceptance still 2/4 failing when the 10-turn repair cap ran out; the visible-green result shipped |
| boltons_443 | planner split the goal into 6 tasks (13 min); 2 hidden failures |
| networkx_8892 | 12-minute agent loop; 1 hidden failure |

AWOS won pathspec_144, which mini failed (7 hidden failures).

## What it means (per the pre-registration)

On Haiku 5.5, the AWOS harness does not beat a minimal edit/test loop on solve rate. It is
faster per issue but less accurate and 1.5× the cost. The common failure is stopping early:
the one-shot edit, the acceptance gate and the capped repair accept a result that a longer
self-testing loop would have kept working on.

Concrete engine changes this points to:
1. The success gate runs the full visible suite once, always (not only when the name mapping finds nothing).
2. When the acceptance gate is inactive (0 kept, or the start run timed out), the one-shot edit is not accepted on visible tests alone; continue into a verify loop.
3. When kept acceptance tests still fail at the repair cap, keep working (within budget) instead of shipping.
4. Alternatively, use a mini-style open loop as the executor, with AWOS's product layer around it.

The B3 result (AWOS 102/120 vs Aider 81/116) still stands; Aider is a weaker baseline than
mini-swe-agent. H2 (local model) is not yet run.
