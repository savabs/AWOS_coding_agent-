---
title: "Spec: Tool-Using Agent Loop"
tags:
  - doc/spec
  - topic/architecture
  - status/active
---

# Spec: Tool-Using Agent Loop

## Problem

`Worker.execute_task()` makes exactly one model call per task and parses
SEARCH/REPLACE blocks out of the reply. A grep across the real pipeline
(`worker.py`, `orchestrator.py`, `planner.py`, `verifier.py`) for `tool_use`,
`tool_choice` and `tools=[` returns nothing: the executing model has no tools
at all.

Consequences, each measurable in the current design:

1. The model cannot read a second file, search for a caller, or run a command
   and react to the output. It must emit a correct patch from a single blind
   look at whatever the Planner pre-selected.
2. Context is chosen by heuristic. `Worker._extract_context()` slices files
   over 500 lines by taking the first 90 lines, guessing a class name from the
   first capitalised word in the task description, and grabbing ±5 lines around
   every `def`/`class`.
3. `planner.py:76` forbids multi-file tasks ("Each task must change ONLY ONE
   file"), which excludes the most common real change shape: edit a signature,
   update its callers, fix the test.

Observed outcome in `.awos/reward_store.jsonl` (227 episodes): 40% success on
`bug_fix`, 50–58% on `new_feature`.

## Approach

Add an agent loop in which the model drives its own turns through tools, and
run it alongside the existing single-shot Worker so the two can be compared on
the same tasks.

Reuse, do not duplicate. The repository already carries a `ToolRegistry`, a
`Tool` ABC with a uniform `ToolResult` envelope, and working `read_file`,
`grep`, `find_files`, `list_dir` and `shell` tools — they are simply wired into
`cli.py` and the unused `core/orchestrator.py` template rather than into the
coding agent. This spec connects them to the executing model.

Equally, the verification layer stays exactly as it is. `Verifier` (AST/syntax
checks, contract compliance, fuzzy apply), `TestRunner`, `GitManager` and
`BudgetLedger` are the strongest parts of the system and are what make an
autonomous loop safe to run.

## Ordered steps

1. **`Tool.input_schema`** — add a JSON Schema property to the `Tool` ABC,
   defaulting to one derived from the existing `{name: description}`
   `parameters` mapping. Backwards compatible: every existing tool keeps
   working, and tools needing richer types override it.
2. **`edit_file` tool** — surgical old/new string replacement routed through
   `Verifier`, so every model-driven edit inherits syntax checking and fuzzy
   matching. Not `write_file`, which overwrites whole files.
3. **`run_tests` tool** — wrap `TestRunner` so the model can observe whether
   its own change passes, rather than being told after the fact.
4. **`AgentLoop`** — the driver. Sends the task plus tool schemas, executes
   returned tool calls against the registry, feeds results back, repeats until
   the task verifies or a stop condition trips.
5. **Wire behind a flag** — `AWOS_AGENT_LOOP=1` selects the loop; default keeps
   the single-shot Worker. Both paths write to the same `RewardStore`, so the
   comparison is apples to apples.

## Stop conditions

The loop must terminate on every path. It halts when:

- the model replies with no tool calls (it considers the task done),
- `max_turns` is reached (default 12),
- the per-task token budget is exhausted,
- `BudgetLedger.check_budget()` blocks, or
- the same tool call repeats with identical arguments beyond a threshold
  (loop detection).

## Exit condition

`AgentLoop` completes a task end to end against a temporary repository with a
stubbed model, driving at least one `read_file` → `edit_file` → `run_tests`
sequence, with every stop condition covered by a test. No API key required to
run the suite.

## Explicitly out of scope

Removing the single-shot Worker, lifting the one-file planner constraint, and
changing the model ladder. Each is a separate change, and keeping the Worker
intact is what makes the loop measurable against it.

## Related

- _(add links to related documents here)_
