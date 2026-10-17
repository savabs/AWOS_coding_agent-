# Haiku 5.5 baseline on the 73 real issues (2026-10-11)

The owner turned OpenRouter off and moved AWOS to direct Anthropic **Claude Haiku 5.5** (commits 92e9649, 9664867, 391d3bb). This run is the new baseline on all 73 real issues in `~/.awos-harness/real_series`.

**Setup**
- One run per issue, `--arms off`.
- `AWOS_SERIES_MODEL=claude-haiku-5-5`: the series pins every ladder rung to Haiku 5.5.
- Anthropic prompt caching on; goal check off (the hidden tests judge).
- Driver: `run_haiku.sh`, scorer: `score_haiku.py` (job tmp).
- Results: `.awos/job_series_20261011T12*.json` onward.

## Result

| | Haiku 5.5 |
|---|---|
| Solved | **72 / 73 (99%)** |
| Cost (AWOS ledger, list prices) | $0.52 total; **$0.0071 / issue**; $0.0072 / solved |
| Time | 0.63 min / issue |
| Invalid runs | 0 |
| Unsolved | `real_markdown_1515` |

## Versus DeepSeek V4 Flash (E1 control, 2026-10-10)

The comparison uses the same 69 issues; E1's control arm had a valid row for 69 of them.

| | Haiku 5.5 | Flash |
|---|---|---|
| Solved | 68 / 69 | 56 / 69 |
| $ / issue | $0.0069 (ledger) | $0.0093 (OpenRouter billed) |

- **Per-issue disagreement:** Haiku solved 12 issues Flash missed, and Flash solved 0 that Haiku missed. Exact McNemar p ≈ 0.0005.

## Caveats

This is **not a pre-registered A/B**. Read the numbers as a strong signal, not a controlled result.
- The runs were a day apart, with different code (Anthropic caching and routing changes), one run each.
- Haiku's cost is AWOS's own ledger estimate at list prices; Flash's is OpenRouter's billed figure.
- **Contamination risk.** These are fixed issues from public repos, and Haiku 5.5 is a newer model that may have seen some fixes in training.
- **The 73 issues are near their ceiling for this model.** Distinguishing future changes needs harder or fresh issues (created after the model's training cutoff).

## Implications

- Haiku 5.5 is both more accurate and cheaper per issue than Flash on this set, so it is the default cloud model (it already is).
- The 73-issue set no longer separates improvements on Haiku: an ablation can gain at most 1 issue. The next evidence step needs a harder or fresh set.
