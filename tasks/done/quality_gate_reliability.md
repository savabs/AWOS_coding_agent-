---
title: "Task: Quality Gate Reliability"
tags:
  - doc/task
  - topic/quality-gate-reliability
  - status/active
---
> **Content:** [quality_gate_reliability.html](quality_gate_reliability.html) — open in browser.
## Steps
- [x] Exclude generated directories from linter discovery.
- [x] Add explicit file-list lint mode.
- [x] Scope quality gate lint to task artifacts and retain configurable tests.
- [x] Add focused tests.
- [x] Live proof: `python3 scripts/quality_gate.py --task tasks/active/quality_gate_reliability.md --test-cmd "python3 -m pytest tests/test_quality_gate_reliability.py -q"` — scoped gate passes.
## Related
- [[quality_gate_reliability]]
- [[quality_gate_reliability_spec]]
