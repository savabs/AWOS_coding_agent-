# SWE-bench Lite subset — live proof

## What this is

Industry-standard benchmark harness for AWOS: **10 curated tasks** from [SWE-bench Lite](https://www.swebench.com) (300 real GitHub bugs).

## Quick proof (no API, no Docker)

```bash
chmod +x scripts/demo_swebench_smoke.sh
./scripts/demo_swebench_smoke.sh
```

### Success markers

| Marker | Meaning |
|--------|---------|
| `[SWEBENCH] dry-run OK` | Loaded 10 instances from HuggingFace |
| `10 instance(s)` | Default subset manifest |
| `passed` | `tests/test_swebench_lite_harness.py` green |

## Run predictions (ReAct + git clone)

```bash
# One instance smoke (~minutes + API cost; clones real repo)
python3 scripts/run_swebench_lite_subset.py --predict --limit 1

# Full default subset (10 instances — expensive)
python3 scripts/run_swebench_lite_subset.py --predict
```

Outputs:
- `.awos/swebench_lite/predictions.jsonl` — SWE-bench format
- `.awos/swebench_lite/report.json` — AWOS run stats

## Official grade (Docker)

```bash
pip install swebench   # + Docker images per SWE-bench docs
python3 scripts/run_swebench_lite_subset.py --evaluate
```

Score = **% resolved** (hidden acceptance tests, not agent self-report).

## Subset file

`tests/fixtures/swebench_lite/subset_10.txt` — edit to change the 10 tasks.

## Checklist

- [ ] Live proof: `./scripts/demo_swebench_smoke.sh` — `[SWEBENCH] dry-run OK`
- [ ] Optional: `--predict --limit 1` then `--evaluate` for real score
