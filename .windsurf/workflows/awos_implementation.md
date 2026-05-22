---
auto_execution_mode: 0
description: AWOS implementation protocol — atomic steps, no code before preflight
---
**Rule: no implementation without a research doc and spec.**

## Pre-Implementation Checklist
Before writing the first line of code, confirm:
- [ ] `docs/research/<feature_name>.html` exists and covers current architecture, risks, external sources
- [ ] `docs/specs/<feature_name>_spec.html` exists and covers goal, files affected, ordered steps, edge cases, testing plan
- [ ] `tasks/active/<task_name>.html` exists with numbered steps derived from the spec
- [ ] The spec's "Files Affected" list is explicit
- [ ] The exit condition for the first step is clear (falsifiable one-line check)

If any missing: create them first. Do not proceed.

## The Implementation Loop
For each step in the task file:
1. Read the step description
2. Read the spec's detail for that step
3. Identify the exact file(s) and function(s) to change
4. Make exactly that change
5. Run the verification test for that step
6. If test passes: mark the step done in the task file
7. If test fails: debug before moving on (see awos_debug workflow)
8. Move to the next step

**One step at a time. No skipping. No combining.**

## During Implementation
- Only modify files listed in the spec. If you need to edit something else, update the spec first.
- Don't re-analyze architecture during coding. If architecture seems wrong, stop and update research+spec first.
- No speculative improvements: don't refactor adjacent code, fix unrelated bugs, or add unplanned features.

## Testing Requirements
Every sub-phase requires edge case tests:
- Happy path
- Invalid input (wrong type, None, empty, malformed)
- Boundary values (zero, one, max, min)
- Error paths (external calls fail)
- Security cases (injection attempts)
- Exception handling

A sub-phase is not complete until its tests pass. "I'll add tests later" is not allowed.

## Pre-Completion Quality Gate
Before marking a task complete:
- [ ] All task steps marked done
- [ ] `pytest` passes with no failures
- [ ] `python scripts/obsidian_lint.py` is clean
- [ ] `memories/repo/project_structure.md` updated
- [ ] Checkpoint written
- [ ] `docs/adr/` updated if significant design decisions were made

After passing: move task file to `tasks/done/` and write final checkpoint.
