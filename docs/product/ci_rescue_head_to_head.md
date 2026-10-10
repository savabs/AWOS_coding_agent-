# CI Rescue Sprint — AWOS vs Raw API

**Generated:** 2026-06-26 (live run)

Same 18 fix steps on the same broken practice repo (`ci_rescue_sprint`).

## Summary

| | Raw API (one try per step) | AWOS harness (verify + retry) |
|--|--|--|
| All 35 tests pass? | **no** | **yes** |
| Honest pass check (tests not edited) | no | yes |
| Steps completed | 18/18 | 18/18 |
| Total API cost | $0.0027 | $0.0056 |
| Wall time | 49s | 81s |

## Plain English result

**Raw API** ran all 18 prompts and applied 18 patches. It was **cheaper** (~half the cost). But **tests still failed** — the job was not done.

**AWOS** ran the same 18 steps with verify-and-retry. It cost **about twice as much** (~0.6 cents vs ~0.3 cents). But **all 35 tests passed** — the job was actually finished.

So on this job: **AWOS is not cheaper, but it is the only one that delivered a working result.** That is the harness value — not saving money on a failed run, but finishing work that one-shot prompts leave broken.

## How to re-run

```bash
python3 scripts/benchmark_ci_rescue_sprint.py
```

Raw JSON: `.awos/benchmarks/ci_rescue_head_to_head_20260626_043339.json`

See also: `docs/ci_rescue_head_to_head_proof.md`
