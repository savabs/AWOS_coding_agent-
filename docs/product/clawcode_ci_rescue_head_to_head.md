# Clawcode CI Rescue — AWOS vs Raw API

**Generated:** 2026-06-26T06:04:56.821191+00:00

Real repo **clawcode**, branch `awos-ci-rescue-buggy`, same 12 fix steps.

## Summary

| | Raw API (one try per step) | AWOS harness |
|--|--|--|
| All 22 tests pass? | yes | yes |
| Honest pass check | yes | yes |
| Steps completed | 12/12 | 12/12 |
| API cost | $0.0022 | $0.0046 |
| Time | 31.8s | 49.7s |

## Plain English

Both passed. Compare cost and time below.

- **Raw API** = 12 separate model calls, no retry loop.
- **AWOS** = same 12 steps with verify and retry.

Raw JSON: `.awos/benchmarks/clawcode_ci_rescue_head_to_head_20260626_060456.json`
