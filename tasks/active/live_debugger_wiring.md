---
title: "Task: Live Debugger Wiring"
tags:
  - doc/task
  - topic/live-debugger-wiring
  - status/active
---
> **Content:** [live_debugger_wiring.html](live_debugger_wiring.html) — open in browser.

## Steps
- [x] Initialize per-run `DebugJournal` and safe emit helpers.
- [x] Emit task, verifier/test, and exception evidence plus deterministic findings.
- [x] Emit terminal evidence and return debug session identity.
- [x] Add controlled end-to-end integration coverage.
- [x] Run quality gate and write checkpoint; gate is blocked by pre-existing repository-wide lint/internal-runner issues documented in the checkpoint.
- [x] Live proof: `python3 -m pytest tests/test_orchestrator_e2e.py -q` — mocked live run creates an inspectable debug bundle.

## Related
- [[live_debugger_wiring]]
- [[live_debugger_wiring_spec]]
- [[evidence_first_debugger_task]]
