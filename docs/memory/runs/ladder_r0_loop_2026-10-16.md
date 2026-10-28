# R0 (Haiku): regression check of the integrated `loop` — 37/37 on the issues that ran; 3 issues not run

Pre-registration: `docs/specs/loop_reliability_ab.md`, section R0 (rule fixed before the run; owner approved the
run and the `loop,mini` pair, $2 cap). GitHub Actions run 38881425567 (`bench-cloud/loop-r0-2026-10-16`, commit
f89bbb8 = code of 1015397: A6–A8, C9, C10, D13, D15 with their defaults — stuck detector `log`, routing off,
compaction off, read-only control paths on, secret scan on). macOS runners, 8 shards, 1 repeat, interleaved.

Data source: the shard job logs (the artifact zips could not be downloaded from the cloud session: its network
policy blocks GitHub's artifact storage host). Per-issue verdicts, turns, cost, minutes and the loop's
`[COMPONENTS]` entry were read from each shard's log; `stats_audit.py` was not run (it needs the artifacts).

## Result

| arm | solved | $ / run | mean min / run |
|---|---|---|---|
| AWOS `loop` (integrated) | **37/37** | $0.0141 | 2.38 |
| mini-swe-agent 2.4.6 (`mini`) | 36/37 | $0.0181 | 2.71 |

- **3 of the 40 issues did not run.** The $2 cap is split evenly ($0.25 per shard); shard 2 stopped after 4 issues
  (`fresh_sqlglot_8524` not run) and shard 4 after 3 (2 issues not run), each on the driver's cost projection.
  H1d had $0.625 per shard. These are "not done", not unsolved.
- Spend: $1.19 over 37 paired issue-runs (both arms).
- Loop vs mini on the 37: McNemar b = 1 (`fresh_pathspec_144`, mini unsolved), c = 0, p = 1.0, as in H1d.
  Cost ×0.78 (H1d ×0.62).
- Integrity: every issue's health line `ok (3 warnings)`; every loop run ended `stop_reason=submitted` with 0
  gate retries; mean cached-prompt fraction 0.856; mini ran with `sandbox=on` throughout.

## R0 rule

| condition | needed | got |
|---|---|---|
| loop solves ≥ 38/40 | 38 | 37 solved of 37 run (3 not run) — **not decidable as written** |
| loop $/run ≤ 1.3 × H1d ($0.0123) | ≤ $0.0160 | $0.0141 (×1.15) ✓ |
| 0 fatal health violations | 0 | 0 ✓ |

**Verdict: INCOMPLETE, no regression seen.** Every issue that ran was solved and cost stayed inside the bound, but
the rule counts out of 40 and three issues never ran because of the per-shard split of the cap. The $/run figure
may be slightly low, since the issues the driver projected as most expensive are the ones that were skipped.
Finishing it means running the 3 missing issues (about $0.10–0.30); that is the owner's call.

## Also recorded

- **D13 on real macOS:** the read-only control paths ran under real Seatbelt for the first time. No sandbox
  denial, "Operation not permitted", read-only-filesystem error or `blocked_secret` appears in any shard log.
- **R2 step 0 (stuck triggers, log mode):** **0 of 37** loop runs fired any trigger (repeat_command, failure_rate,
  failure_signature, no_progress); escalation was never recommended. That is below the pre-registered 10%, so R2
  (nudge A/B) is **not worth running on Haiku** on this set. This was the detector's first version, before the
  precision fixes (which only make it fire less). On the local model (H2) the count may differ, so log mode stays.
- Defaults are unchanged by this run: nothing in R0 was meant to switch a default.
