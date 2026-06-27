# Clawcode CI Rescue — proof guide

## Setup (once)

```bash
python3 scripts/clawcode_ci_rescue_prepare.py
```

This creates branch `awos-ci-rescue-buggy` on clawcode with 12 broken tests and writes the 12-step mission plan.

## Run AWOS

```bash
python3 awos.py mission start --clawcode-ci-rescue
```

## Success

- All 22 tests pass in the worktree
- `WEDGE_ASSERT: SEMANTIC_PASS` at the end
- clawcode `main` branch still clean

## Restore clawcode

```bash
python3 scripts/clawcode_ci_rescue_prepare.py --restore
```
