# Static gate at edit-apply time (trick T3, "V0+")

Stage 1, one excellent worker: verification moved earlier, with no LLM call. Source:
`docs/research/trick_book_2026-10.md` §1 #4 and §2.4. Experiment: E3.

## Research

- **SWE-agent ACI** (Yang et al. 2024, arXiv:2405.15793). The `edit` command runs a
  linter on the file after the edit. If the edit introduces lint errors, it is
  **discarded**: the file stays unchanged, and the model sees the errors plus the
  before/after snippet so it can try again. Removing the linter costs about 3pp on
  SWE-bench Lite, and 51.7% of trajectories trip it at least once (trick book §1 #4).
  The linter is flake8 `--isolated`, limited to syntax and name errors (F821, F822,
  F831, E111–E113, E999, E902). It has no style rules.
- **Loops.** In SWE-agent, 23.4% of failures are loops on syntax errors (trick book §2.4).
  So the gate gives up after **2** consecutive rejections of the same file and lets
  the write through, with a warning.
- **Which rules are safe.** Syntax errors (compile) and F821 undefined name are the
  high-value rules: they are never correct in final code. F822 (an undefined name in
  `__all__`) is the same bug. Rules left out:
  - F401 (unused import): fires on valid intermediate states, for example an import
    added one edit before its use.
  - F811 (redefinition): fires on legitimate conditional imports and overrides.
  - All style rules.
- **Tools available here.** `ruff 0.16.3` is in the project venv
  (`.venv/bin/ruff`), but not on PATH or in brew. `pyflakes` is not installed. Nothing
  was installed for this work. The gate finds ruff next to `sys.executable`, then on
  PATH. Without ruff it falls back to the compile check only.
- **What already existed.** `Verifier.verify_and_apply` already rejected syntax errors
  for search/replace edits. Three gaps remained:
  - The file-creation path (empty `old_string`) had no check.
  - The one-shot append path had no check.
  - Nothing checked undefined names.

  For one-shot, tests were already read-only (`apply_blocks(allow_test_edits=False)`
  unless `_asks_for_tests`). The agent loop had no such rule.

## Design

`scaffold/agent/static_gate.py`:

- `check(path, new_text, old_text) -> (ok, messages)`. Only `.py` files are checked;
  every other file passes.
  - (a) `compile()` of the new text, which is the same check as py_compile.
  - (b) `ruff check --isolated --select F821,F822` on the old text and the new text
    (via stdin). The edit is rejected only for a name whose count **grew**, so
    problems already in the file never block an edit.
  - Cap: when a file reaches `MAX_CONSECUTIVE = 2` rejections, the next failing write
    is allowed and logs `[STATIC-GATE] cap reached`. A passing write resets the count.
    `new_task()` resets the counts at the start of each task.
- (c) Resolving imports of project modules is **not implemented**. Resolving
  relative and namespace packages is error-prone, and F821 already covers the
  common case of a missing import.

Hooks (all are off unless `AWOS_STATIC_GATE=1`):

- `tools/code_edit.EditFileTool.execute`. It previews the exact text Verifier would
  write, using the same `_apply_fuzzy`, and gates it before anything reaches disk. The
  creation path is gated too. A rejection is `ToolResult.fail` with
  "NOT applied (the file is unchanged)" plus the messages. The agent loop and
  `one_shot.apply_blocks` both use this tool. In one-shot, a rejected block becomes a
  failed block with an `index`, so it goes to the repair call.
- `one_shot.apply_blocks` append path. It writes outside EditFileTool, so it calls the
  same gate (`_static_gate_reject`).
- `orchestrator._execute_task_via_agent_loop` records `static_gate_rejections` in
  the task result and logs `[STATIC-GATE] task <id>: N rejection(s)`.

**Tests read-only (V0)** has its own knob, `AWOS_STATIC_GATE_TESTS=1` (default 0).
When it is on, the orchestrator sets `EditFileTool.protect_tests` unless
`_asks_for_tests(task)`. Edits to existing test files are then refused, while new test
files are still allowed. This matches one-shot's rule. It is a separate knob because
of the offline check below: 7 of 73 solved runs edited an existing test file in a
feature task, for example adding a new CLI command to `tests/test_cli.py`. Bundling
the rule in would confound E3.

Both variables are recorded in `scripts/job_series.py` `ROUTING_ENV`.

## Offline check ($0)

`/tmp/sg_replay/replay.py` (scratch) replayed the final diffs from every recorded job
series under `.claude/worktrees/*/.awos/job_series/*/**/trajectories/*.jsonl`.
Pre-images came from the arm project's loose git objects, and solved labels from
`job_series_<ts>.json`.

| | replayed | flagged by the gate |
|---|---|---|
| solved | 73 | **0** |
| unsolved | 39 | 0 |

- All 200 Python file states were reconstructed: 0 had no pre-image and 0 failed to apply.
- The gate would not have wrongly rejected any solved run. It also flags no final
  unsolved diff, because those final states already compiled and the models did not
  leave undefined names.
- The value of the gate is therefore in **intermediate** edits, which the
  trajectories do not record. It has to be measured live, in E3.
- Read-only tests (separate knob): diffs that edited existing tests were 7 of 73
  solved and 4 of 39 unsolved.

## Status

Default off. Turn it on only after an E3 A/B run under the T1 decision rule.
