---
title: "Spec: TestRunner + Test-as-Reward (Phase 5A)"
tags:
  - doc/spec
  - phase/5
  - topic/self-learning
  - topic/test-runner
---
> **Content:** [test_runner_reward_spec.html](test_runner_reward_spec.html) — open in browser.

## Goal
Add TestRunner module that auto-detects tests, runs after patches, feeds pass_rate into LinUCB reward.

## Files
- **Create:** `scaffold/agent/test_runner.py`, `tests/test_test_runner.py`
- **Modify:** `scaffold/agent/orchestrator.py`, `scaffold/agent/escalation_engine.py`

## Key Decisions
- `TestRunner.detect()` → `TestConfig | None` based on signal files
- `TestRunner.run()` → `TestResult` with `pass_rate` float
- Safety: `AWOS_SAFE_TO_RUN_TESTS=1` required (opt-in)
- Timeout: 60s hard cap
- Integration: after `Verifier`, before `EscalationEngine.record_outcome()`

## Related
- [[test_runner_reward]] — research
- [[test_runner_reward_task]] — execution checklist
- [[escalation_engine]] — record_outcome()
