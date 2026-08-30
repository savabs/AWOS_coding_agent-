---
name: awos-change-gate
title: "AWOS change gate"
tags:
  - doc/skill
  - topic/awos-workflow
description: Use before any non-trivial AWOS implementation, behavior change, dependency change, schema change, or multi-file edit; enforce research, specification, and atomic task gates.
---

# AWOS change gate

Before implementation, fail closed unless the governing triad exists and is current:

- `docs/research/<feature>.md` — research note
- `docs/specs/<feature>_spec.md` — spec
- `tasks/active/<task>.md` — task file

All three are **Markdown-canonical**. The agent's scripts (`quality_gate.py`, `obsidian_lint.py`) parse Markdown only; HTML is invisible to them and costs extra tokens. Use HTML only for a research/spec a human will open — never for task files or checkpoints.

Research must identify current modules, insertion points, verified external sources, risks, and non-goals. The spec must list the exact files allowed to change and atomic steps. The task file is the progress source of truth.

Rules:

1. Read only relevant code first; do not re-read the whole repository.
2. State the governing task and spec before editing implementation files.
3. If a step contains multiple independent actions, split it.
4. Modify only files listed in the governing spec. Update the task after each proven step.
5. For external APIs, unfamiliar libraries, mathematical methods, or new dependencies, verify authoritative documentation before coding and record the source in research.
6. Keep task files and checkpoints in Markdown with full `- [x]` checklists. If a research/spec has an HTML rendering, keep its `.md` as a complete self-contained summary with frontmatter + wiki links — never a bare pointer.
7. Run the narrowest relevant test after each atomic change, then the quality/lint checks required by the task.
8. Never weaken a test, hide a failure, or claim a green suite when known failures remain.

Trivial single-file wording or comment edits may skip the full gate; behavior and architecture changes may not.
