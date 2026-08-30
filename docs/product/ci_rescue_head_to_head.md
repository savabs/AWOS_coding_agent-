# CI Repair Benchmark — AWOS vs Raw API

**Generated:** 2026-06-26 (live run)

Same 18 repair steps on the same broken practice repo (`ci_rescue_sprint`).

## Summary

| | Raw API (one try per step) | AWOS harness (verify + retry) |
|--|--|--|
| All 35 tests pass? | **no** | **yes** |
| Honest pass check (tests not edited) | no | yes |
| Steps completed | 18/18 | 18/18 |
| Total API cost | $0.0027 | $0.0056 |
| Wall time | 49s | 81s |

## Plain English result

**Raw API** ran all 18 prompts and applied 18 patches. It was cheaper, but **tests still failed** — the job was not actually finished.

**AWOS** ran the same 18 steps with verification and retry. It cost more, but **all 35 tests passed** — the work was actually completed.

So on this benchmark: **the harness advantage is not “chat cheaper than Cursor.”**
It is that the system can spend a little more when necessary to **finish the job correctly** through planning, verification, and recovery.

That is the right lens for AWOS:
- proper coding agent first
- reliable recovery second
- failure-driven learning on top

## How to re-run

```bash
python3 scripts/benchmark_ci_rescue_sprint.py
```

Raw JSON: `.awos/benchmarks/ci_rescue_head_to_head_20260626_043339.json`

See also: `docs/ci_rescue_head_to_head_proof.md`
