---
title: "Research: TestRunner + Test-as-Reward (Phase 5A)"
tags:
  - doc/research
  - phase/5
  - topic/self-learning
  - topic/test-runner
---
> **Content:** [test_runner_reward.html](test_runner_reward.html) — open in browser.

## Summary

Upgrade AWOS reward signal from syntax-only to test execution pass rate.
Validated by SWE-RL (Meta 2025), Darwin Gödel Machine (Sakana AI 2025), Reflexion (Princeton 2023), AFlow (ICLR 2025).

## Key Decisions

- New module: `scaffold/agent/test_runner.py` — language-agnostic, detects pytest/jest/vitest/make/go/cargo
- Reward: `pass_rate = passed / (passed + failed + errors)` — float replaces binary `success`
- Opt-in safety: `AWOS_SAFE_TO_RUN_TESTS=1` required (default off to prevent side effects)
- Timeout: 60s hard cap; graceful fallback to syntax-only reward on timeout
- Reward hacking guard: only run tests that existed BEFORE the task (snapshot guard)
- Integration point: between `Verifier` and `EscalationEngine.record_outcome()`

## Related

- [[test_runner_reward_spec]] — spec (next to write)
- [[ml_meta_controller_spec]] — LinUCB + RewardStore design
- [[verifier]] — current syntax gate (`scaffold/agent/verifier.py`)
- [[reward_store]] — Episode + ReplayGate
- [[escalation_engine]] — record_outcome()
