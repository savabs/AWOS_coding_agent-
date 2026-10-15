# Harness-lift ladder H1d (Haiku): AWOS `loop` executor vs mini-swe-agent — switch rule met

Pre-registration: `docs/specs/harness_lift_ladder.md` amendment 4 (the switch rule was fixed before any data).
GitHub Actions run 38738599802 (`bench-cloud/ladder-h1d`, commit b14d3e3 = engine 0f1912b plus `ARMS=loop,mini`).
Both arms were run fresh on the 40 fresh issues.

## Result

| arm | solved | $ / run | median min / run |
|---|---|---|---|
| AWOS `loop` (`AWOS_EXECUTOR=loop`) | **40/40** | **$0.0123** | 2.01 |
| mini-swe-agent 2.4.6 (`mini`) | 39/40 | $0.0200 | 1.97 |

- `stats_audit.py --a mini --b loop --cost-budget 0.5` gave **INCONCLUSIVE**: P(Δ>0) = 0.561; McNemar b = 1, c = 0,
  p = 1.0; MDE ≈ 7.0%; cost ×0.62.
- Spend $1.29. Integrity: 0 fatal health violations; mini sandbox on 40/40; neither arm's logs mention the
  hidden tests or the series dir; all 40 loop runs ended with `stop_reason=submitted`.

## Switch rule (amendment 4)

| condition | needed | got |
|---|---|---|
| H1d not REJECT | not REJECT | INCONCLUSIVE ✓ |
| loop solves ≥ 34/40 | 34 | 40 ✓ |
| loop cost ≤ 1.5× mini | ≤ 1.5× | 0.62× ✓ |

All three hold, so **`loop` becomes AWOS's default executor** when a sandbox is available (Seatbelt or docker).
Without a sandbox, AWOS falls back to `pipeline`, because the loop refuses to run unsandboxed. An explicit
`AWOS_EXECUTOR` always wins.

## Ladder on Haiku 5.5, so far

| run | AWOS | mini | verdict |
|---|---|---|---|
| H1 (pipeline) | 33/40 | 39/40 | REJECT |
| H1b (pipeline + fixes, in-sample) | 34/40 | 40/40 | REJECT |
| H1c (pipeline, held-out 30) | 30/30 | 30/30 | INCONCLUSIVE (ceiling), ×2.2 cost |
| H1d (loop) | **40/40** | 39/40 | INCONCLUSIVE, **×0.62 cost** |

The defensible claim is that AWOS with the loop executor matches mini-swe-agent's solve rate on this set at
about 60% of its cost. It is not "better" (one task of difference). Confirming on unseen issues needs a harder
held-out set, since H1c hit a ceiling.
