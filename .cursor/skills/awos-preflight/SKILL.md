---
name: awos-preflight
description: Runs AWOS Research → Spec → Task preflight before non-trivial implementation. Use when starting a new feature, kernel change, or when the user asks to implement without existing spec/task files.
---

# AWOS Preflight Gate

No kernel implementation until triad exists (trivial typo fixes exempt).

## Triad

```
docs/research/<feature>.md
docs/specs/<feature>_spec.md
tasks/active/<feature>.md
```

## Spec step rules

- One change, one test, one proof per step
- If step has "and" → split into two steps
- Link facts — never copy metrics from `memories/repo/project_structure.md`

## Write-gate

Decisions written in the **same turn** as approval. No deferred spec updates.

## After preflight

Follow `AWOS.md` → one atomic step → test → live proof if kernel feature.

Reference: `.cursor/rules/awos-artifacts.mdc`
