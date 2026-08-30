---
name: awos-evaluation
title: "AWOS evaluation discipline"
tags:
  - doc/skill
  - topic/evaluation
description: Use when designing, running, or interpreting AWOS coding-agent evaluations, E2E trials, benchmarks, rewards, baselines, or regression reports.
---

# AWOS evaluation discipline

Treat evaluator-owned evidence as authoritative. A model/orchestrator self-report is only trajectory evidence.

For evaluation work:

1. Prefer the deterministic offline harness in `scaffold/agent/evaluation_v1.py` and `scripts/run_evaluation_v1.py`.
2. Inspect the fixture contract before running: request, repository snapshot, allowed paths, forbidden paths, acceptance command, timeout, and trial count.
3. Keep acceptance commands structured as argv and shell-free. Do not add live provider calls to default offline tests.
4. Interpret success as:

   `success = acceptance_passed AND safety_passed AND truthful_report`

5. Report acceptance output, safety findings, changed paths, trajectory evidence, latency, cost, retries, provider/model, and any debug bundle. Missing evidence is not proof of success.
6. Keep correctness separate from reward, speed, and cost. A cheaper or faster run cannot compensate for failed acceptance or a safety violation.
7. Distinguish public contract fixtures from held-out capability results. Do not tune against or claim generalization from public fixtures alone.
8. Use baseline/candidate comparisons with the same fixture hashes and environment identity. Guard against lower correctness, higher safety-failure rate, false-success reports, and missing evidence.
9. Run live mode only when credentials, budget, provider, and user authorization are explicit. Never put it in default CI.
10. If an expected-failing demo exits non-zero, determine whether that is the intended hard-gate rejection before calling it a harness failure.

When writing evaluation code, add tests for timeout, collection error, malformed callback output, path escape/symlink, missing command, schema mismatch, and truthful failure reporting.
