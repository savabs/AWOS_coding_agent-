---
title: "Task: <Feature Name>"
tags:
  - doc/task
  - status/active
  - phase/N
  - topic/<slug>
  - layer/<slug>
---

# Task: <Feature Name>

**Status:** active
**Phase:** N
**Research:** [[<feature_name>]]
**Spec:** [[<feature_name>_spec]]
**Started:** YYYY-MM-DD
**Completed:** —

> **This file is the source of truth for progress.**
> It must be resumable cold — any agent or human should be able to pick up from here
> without replaying the conversation history.

---

## Context (1 paragraph)

What is this task doing and why? Include:
- The problem being solved
- The approach chosen (reference the spec for details, don't repeat them)
- The exit condition: how will you know this is done?

---

## Steps

Mark steps: `[ ]` not started / `[~]` in progress / `[x]` done / `[!]` blocked

### Phase N.1: <name>
- [ ] N.1.1: <atomic step> → verification: `<one-line check>`
- [ ] N.1.2: <atomic step> → verification: `<one-line check>`
- [ ] N.1.3: <atomic step> → verification: `<one-line check>`

### Phase N.2: <name>
- [ ] N.2.1: <atomic step>
- [ ] N.2.2: <atomic step>
- [ ] N.2.3: <atomic step>

### Phase N.3: Tests
- [ ] N.3.1: Happy path test passes
- [ ] N.3.2: Failure case 1 test passes
- [ ] N.3.3: Failure case 2 test passes
- [ ] N.3.4: Edge case tests pass

### Phase N.4: Cleanup
- [ ] N.4.1: Obsidian lint clean (`python scripts/obsidian_lint.py`)
- [ ] N.4.2: Quality gate passes (`python scripts/quality_gate.py --task tasks/active/<name>.md`)
- [ ] N.4.3: `memories/repo/project_structure.md` updated with new metrics
- [ ] N.4.4: Checkpoint written
- [ ] N.4.5: Task moved to `tasks/done/`

---

## Blockers

*List any blocking issues here. Include what information is needed to unblock.*

| Blocker | Blocking step | Resolution needed |
|---|---|---|
| — | — | — |

---

## Notes

*Running notes during implementation. Facts go here first, then migrate to canonical files.*

- YYYY-MM-DD: ...

---

## Session Log

*Brief notes from each session. What was done, what's next.*

| Date | Done | Next |
|---|---|---|
| YYYY-MM-DD | Steps N.1.1–N.1.3 complete | Start N.2.1 |

---

## Decisions Made

*Any design decisions made during implementation that aren't in the spec.*

| Decision | Rationale | Date |
|---|---|---|
| Used X instead of Y | Y had a bug in v2.1; X is equivalent | YYYY-MM-DD |

---

## Related

- [[<feature_name>]] — research doc
- [[<feature_name>_spec]] — specification
- [[<related_task>]] — related work
