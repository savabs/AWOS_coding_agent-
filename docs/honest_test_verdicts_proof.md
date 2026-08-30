# Honest Test Verdicts Proof

## Command

```bash
python3 - <<'PY'
from scaffold.agent.test_runner import TestRunner
r = TestRunner(".")
res = r._parse_pytest("2 passed in 0.05s", "", ["pytest"], returncode=1)
print(f"[PROOF] nonzero exit + '2 passed' -> failed={res.failed}, returncode={res.returncode}")
assert res.failed == 1
ok = r._parse_pytest("2 passed in 0.05s", "", ["pytest"], returncode=0)
print(f"[PROOF] zero exit + '2 passed' -> failed={ok.failed}, passed={ok.passed}")
assert ok.failed == 0
print("[PROOF] PASS — exit-status-first verdicts")
PY
```

## Success Markers

- `[PROOF] nonzero exit + '2 passed' -> failed=1` — a broken command that printed "2 passed" is correctly marked failed.
- `[PROOF] zero exit + '2 passed' -> failed=0` — a healthy run stays passing.
- `[PROOF] PASS — exit-status-first verdicts`

## What It Demonstrates

The test runner's pass/fail verdict is now driven by the subprocess exit status, not by parsing a superficially-successful output line. A command that crashes after printing "N passed" can no longer be reported as green.

## Automated Coverage

`tests/test_test_runner.py::TestExitStatusFirst` (4 tests) and `TestChangedFilesTargeting` (2 tests).
