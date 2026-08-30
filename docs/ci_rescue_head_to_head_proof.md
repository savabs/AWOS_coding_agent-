# CI Repair Benchmark — Head-to-head proof

## What this proves

We run the **same 18 bug fixes** two ways and compare outcomes, cost, and verification.

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
| `tests=PASS` for the AWOS arm | The harness actually finished the repair job |
| Raw API still fails or degrades | One-shot prompting was not enough |
| `SEMANTIC_PASS` (via report) | Fixes are real, tests were not edited to cheat |
| AWOS cost stays bounded | The harness remains economically reasonable while finishing the job |

## Read the report

`docs/product/ci_rescue_head_to_head.md` — plain English summary after each run.
