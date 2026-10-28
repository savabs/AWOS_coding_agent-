# Harness-lift ladder H1c (Haiku, held-out confirmation): INCONCLUSIVE (both 30/30)

Pre-registration: `docs/specs/harness_lift_ladder.md` amendment 3 plus its addendum. GitHub Actions run
38712261349 (`bench-cloud/ladder-heldout`, commit 59f2553 = engine 0d4fe24 plus CI config). The set is
`heldout_series`: 30 issues from PRs merged 2026-09-11 to 10-14, none used for any design decision.

## Result

| arm | solved | $ / run | median min / run |
|---|---|---|---|
| AWOS (`off`) | **30/30** | $0.0119 | 0.61 |
| mini-swe-agent 2.4.6 (`mini`) | **30/30** | $0.0054 | 0.60 |

- `stats_audit.py --a mini --b off --cost-budget 0.5` gave **INCONCLUSIVE**: P(Δ>0) = 0.501; McNemar b = 0, c = 0, p = 1.0;
  cost ×2.22 (over the +50% budget).
- Spend $0.52. Integrity: 0 fatal health violations; mini sandbox on 30/30; no mini log mentions the hidden tests
  or the series dir; every row has a real hidden-test summary.

## Reading

There is a ceiling effect: both arms solved every held-out issue, so this set cannot separate them on solve rate.
On unseen issues AWOS matched the minimal harness on correctness, cost 2.2× as much, and took the same time.

Across the ladder on Haiku 5.5:
- H1: REJECT (AWOS 33/40 vs mini 39/40).
- H1b: REJECT, in-sample (34/40 vs 40/40).
- H1c: INCONCLUSIVE at a ceiling (30/30 vs 30/30, AWOS 2.2× cost).

The AWOS engine does not beat a minimal edit/test loop on this model, and it costs more. A harder held-out set
would be needed to separate the arms; with this one, the defensible claim is parity on solve rate at higher cost.
