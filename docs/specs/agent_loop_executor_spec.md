---
title: "Spec: Agent Loop as the Orchestrator's Executor"
tags:
  - doc/spec
  - topic/architecture
  - status/active
---

# Spec: Agent Loop as the Orchestrator's Executor

VISION stage 1 (one excellent worker): execution + verification.

## Evidence

`scripts/bench_executors.py`, 12 cases, 2026-09-22/23:

| Executor | Haiku 4.5 | DeepSeek v4 Flash |
|---|---|---|
| AgentLoop (native tool calls) | 12/12 | 12/12 ($0.016 total) |
| ReActWorker — current default | 0/12 | 3/12 |
| Worker (single-shot) | 4/12 | — |

`Orchestrator._execute_single_task` sends every task to `ReActWorker` by
default (`AWOS_REACT_WORKER` defaults to 1). ReAct takes any reply that does
not start with JSON as `finish` (`react_worker.py:511`); models that emit tool
calls as XML/DSML tags therefore "finish" on turn 1 having edited nothing.

## Design

`AWOS_EXECUTOR` selects the executor:

| Value | Path |
|---|---|
| `agent_loop` (default) | `_execute_task_via_agent_loop` |
| `react` | `_execute_task_via_react` (previous default) |
| `worker` | single-shot Worker retry loop |

`_execute_task_via_agent_loop`:

1. Builds a client for the escalation decision's model
   (`build_client_from_env(openrouter_model_id(spec.model_id))`). If no client
   can be built, falls back to the previous path — never fails a task for want
   of a backend.
2. Runs `AgentLoop` over `build_coding_registry(codebase_root)`. Edits go
   through `edit_file` → Verifier, as in the benchmark.
3. **Verifies independently.** The loop's `success` only means the model
   stopped calling tools. The orchestrator runs `TestRunner` on the touched
   files; failing tests fail the task. An edit task that touched no file fails.
4. Returns a result in ReActWorker's shape, and the existing bookkeeping —
   reward store, escalation, spans, git tracking/rollback, GUI events — is
   shared, not duplicated: `_execute_task_via_react` is split into running the
   executor and `_record_tool_executor_result`.

Per-task spend: `AgentLoop` already honours `AWOS_MAX_RUN_COST`.

## Out of scope

- Removing ReActWorker or the Worker. Both stay selectable.
- ReAct's parser bug. Recorded here; not fixed, since ReAct stops being the
  default.

## Proof

- Unit: executor selection, fallback when no client, success requires touched
  files and passing tests, rollback on failure.
- Live: an orchestrator run on a staged benchmark case with
  `AWOS_EXECUTOR=agent_loop` that edits, tests and records the task.

## Live proof — 2026-09-23

`python3 scripts/prove_agent_loop_executor.py [--case NAME]` runs the real
`Orchestrator` (`AWOS_EXECUTOR=agent_loop`) on a staged benchmark case in a
fresh git repo, then checks the file it left with the case's own tests.

| Case | Orchestrator | Its own test run | Independent check | Hosts |
|---|---|---|---|---|
| `b1_constant_mismatch` | success | 3 passed, 0 failed | pass | openrouter.ai only |
| `c1_reuse_existing_helper` | success | 3 passed, 0 failed | pass | openrouter.ai only |

Model: DeepSeek v4 Flash via OpenRouter, chosen by the escalation ladder.

### Defects found on the way, all fixed

Each one blocked or falsified a real orchestrator run; none was visible to the
unit suite.

1. **Every task crashed** — `EscalationEngine.decide`: LinUCB weights trained
   on an older, longer ladder picked rung 4 of a 2-rung ladder → `IndexError`.
   Out-of-range picks now fall back to the first allowed rung, with a warning.
2. **Test verification silently never ran** — `TestRunner` invoked a bare
   `pytest`, "not found" without an activated venv, and reported that as
   0 passed / 0 failed rather than as no evidence. A good fix then scored as a
   0% pass rate and was decomposed into pointless subtasks. Now
   `sys.executable -m pytest`, and "not found" is `no_tests_found`.
3. **Edits that broke tests were kept** — the shared bookkeeping decided
   keep-or-rollback *before* folding in test failures (true of the ReAct path
   too). Test failures now fail the task first.
4. **The stagnation breaker crashed** when runtime sessions are off
   (`None.pause_reason`), and without a session the run did not stop. Both
   fixed.
