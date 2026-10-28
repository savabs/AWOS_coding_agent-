# Harness-lift ladder H1b (Haiku, in-sample re-test after engine fixes): REJECT

Pre-registration: `docs/specs/harness_lift_ladder.md` amendment 2. GitHub Actions run
38704035564 (`bench-cloud/ladder-h1b`), engine commit **233c092** (H1 engine + the full-suite
gate, the verify loop for an unchecked one-shot, and extra repair rounds). Both arms were re-run fresh.
**In-sample:** the fixes were designed from these 40 issues' H1 failure logs.

## Result

| arm | solved | $ / run |
|---|---|---|
| AWOS (`off`) | **34/40** | $0.0347 |
| mini-swe-agent 2.4.6 (`mini`) | **40/40** | $0.0183 |

- `stats_audit.py --a mini --b off --cost-budget 0.5` gave **REJECT**: P(Δ>0) = 0.178; posterior Δ −7.5%
  (95% CrI −23.7 to +8.7); observed per-task Δ −15.0%; exact McNemar b = 0, c = 6, p = 0.031; MDE ≈ 16.0%; cost ×1.90.
- Spend $2.12 (cap $5). 40/40 issues done.

## AWOS against its own H1 run

- **Recovered (5):** astroid_3286 (full-suite gate), click_3818 and markdown-it-py_433 (verify loop),
  sqlglot_8574 (extra repair round), sqlglot_8259 (not targeted; likely variance). Every targeted
  failure was recovered.
- **Newly lost (4):**
  - boltons_441 and boltons_513: **a sandbox bug the gate exposed.** boltons' `tests/test_socketutils.py`
    uses a localhost socket. AWOS's Seatbelt profile denied all network, loopback included, so 2 tests
    failed only inside AWOS's sandbox. The new full-suite gate counted them as new failures and rejected
    correct fixes. The grader runs unsandboxed and saw green. Fixed in **0d4fe24** (loopback allowed per
    operation, internet still denied; verified live).
  - pathspec_132: the one-shot passed its checks and shipped an incomplete fix (H1 got it right; variance).
  - sqlglot_8524: the gate caught a real regression; the follow-up agent-loop fix then missed the hidden tests.
- **Still lost:** boltons_443 (also hit by the sandbox bug), networkx_8892 (exploration loop that never edits).

## Reading

The fixes work on the failures they target, but on Haiku 5.5 mini-swe-agent is near-perfect on this set
(39/40, then 40/40), and AWOS is not ahead even with the fixes and the sandbox bug removed (projected about
36/40). H1 and H1b both REJECT. The held-out run (H1c, amendment 3) is the confirmatory test of the current
engine on unseen issues.
