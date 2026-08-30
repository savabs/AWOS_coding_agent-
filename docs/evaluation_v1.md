---
title: "Evaluation v1 Operator Guide"
tags:
  - doc/guide
  - phase/2
  - topic/evaluation
---

# Evaluation v1 Operator Guide

## Purpose

`evaluation_v1` is the deterministic gate for measuring coding-agent changes. It separates:

- hard correctness and safety success;
- diagnostic reward;
- latency, cost, and retries;
- replayable trajectory evidence.

A failed acceptance test is never converted into success by a high reward or low cost.

## Offline smoke proof

Run the deterministic harness without API keys:

```bash
python3 scripts/run_evaluation_v1.py --mode demo-correct --trials 2 --report .awos/evaluation_v1_demo.json
```

Expected result:

```text
RESULT success=True pass_at_1=1.000 trials=2
```

Prove the hard failure path:

```bash
python3 scripts/run_evaluation_v1.py --mode demo-wrong
```

This command is expected to exit non-zero and report `pass_at_1=0.000`. That is a successful evaluator proof, not a harness error.

Replay a saved report without model execution:

```bash
python3 scripts/run_evaluation_v1.py --replay .awos/evaluation_v1_demo.json
```

## Fixture contract

A fixture has a public request and repository snapshot plus evaluator-owned acceptance commands, allowed paths, forbidden paths, and optional human calibration time. Acceptance commands are structured argv lists and execute with `shell=False`.

## Interpreting results

- `pass_at_1`: fraction of trials that satisfy every hard gate.
- `all_trials_passed`: whether every trial passed.
- `safety_failure_rate`: fraction of trials with forbidden or unallowed writes.
- `mean_latency_sec`, `mean_cost_usd`, `mean_retries`: efficiency diagnostics.
- `trajectory`: ordered events for inspection and later replay tooling.

The demo CLI validates the harness, not live agent quality. Public fixture families currently cover `basic_edit`, `exploration`, `multi_file`, and `recovery` under `tests/fixtures/evaluation_v1/`.

## Real orchestrator mode

Live mode is explicitly opt-in and may call a configured provider:

```bash
python3 scripts/run_evaluation_v1.py \
  --mode live \
  --fixture tests/fixtures/evaluation_v1/basic_edit/fixture.json \
  --trials 1 \
  --report .awos/evaluation_v1_live.json
```

The adapter records the orchestrator result as trajectory evidence, but evaluator-owned acceptance tests remain authoritative. Do not include live mode in the default offline CI gate.

Holdout fixtures must be stored separately from public fixtures, excluded from prompt/reward tuning, and evaluated only through a recorded baseline/candidate comparison.

## Related

- [[self_learning_reward_evaluation_v1]]
- [[self_learning_reward_evaluation_v1_spec]]
- [[test_outcome_gate]]
