# Clawcode CI Rescue — head-to-head live proof

## What this proves

Same 12 fixes on real **clawcode** repo (`awos-ci-rescue-buggy` branch): raw one-shot API vs AWOS harness.

## Run

```bash
# Ensure buggy branch exists (12 failing tests)
python3 scripts/clawcode_ci_rescue_prepare.py

# Full comparison (~2 min, API calls)
./scripts/demo_clawcode_ci_rescue_head_to_head.sh
```

Dry run (no API):

```bash
python3 scripts/benchmark_clawcode_ci_rescue.py --dry-run
```

## Success markers (2026-06-26 run)

| Marker | Expected |
|--------|----------|
| `[1/2] Raw API...` | `tests=PASS` |
| `[2/2] AWOS harness...` | `tests=PASS` |
| Summary line | `Raw: pass=True` and `AWOS: pass=True` |
| Report | `docs/product/clawcode_ci_rescue_head_to_head.md` |

## Observed output (checkpoint)

```
[1/2] Raw API...
      tests=PASS  $0.0022  31.8s
[2/2] AWOS harness...
      tests=PASS  $0.0046  49.7s
  Raw:  pass=True  $0.0022
  AWOS: pass=True  $0.0046
  AWOS / Raw cost: 2.04x
```

JSON: `.awos/benchmarks/clawcode_ci_rescue_head_to_head_20260626_060456.json`

## Interpretation

On this **easier** 12-bug clawcode set, both arms finished green. AWOS cost ~2× raw ($0.0046 vs $0.0022) and took longer. Contrast with practice fixture (`ci_rescue_sprint`) where raw failed and only AWOS passed — harness value shows up on harder jobs, not every run.
