# CI Rescue Sprint — Head-to-head proof

## What this proves

We run the **same 18 bug fixes** two ways and compare cost and results.

## Command

```bash
python3 scripts/benchmark_ci_rescue_sprint.py
```

Or the wrapper:

```bash
./scripts/demo_ci_rescue_head_to_head.sh
```

## What to look for in the output

| Line | Good sign |
|------|-----------|
| `tests=PASS` for both arms | Both finished the job |
| AWOS cost **lower** than Raw | Harness saves API money |
| `SEMANTIC_PASS` (via report) | Fixes are real, tests were not edited to cheat |

## Read the report

`docs/product/ci_rescue_head_to_head.md` — plain English summary after each run.
