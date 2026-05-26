---
title: "Task: TestRunner + Test-as-Reward (Phase 5A)"
tags:
  - doc/task
  - phase/5
  - topic/self-learning
  - topic/test-runner
---
> **Content:** [test_runner_reward_task.html](test_runner_reward_task.html) — open in browser.

## Goal
Add TestRunner module: auto-detect tests, run after patches, feed pass_rate into LinUCB reward.

## Steps
1. Create `scaffold/agent/test_runner.py` with dataclasses + detection
2. Implement `run()` with subprocess + timeout
3. Implement pytest parser
4. Implement jest + generic parsers
5. Add `AWOS_SAFE_TO_RUN_TESTS` safety guard
6. Write `tests/test_test_runner.py`
7. Wire into `Orchestrator`
8. Verify `EscalationEngine` float reward
9. Full suite regression check

## Decision
2026-05-19: Opt-in safety model (env var required) over opt-out.

## Related
- [[test_runner_reward_spec]] — spec
- [[test_runner_reward]] — research
