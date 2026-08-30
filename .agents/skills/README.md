---
title: "AWOS project-local Zed skills"
tags:
  - doc/skill-index
  - topic/zed-skills
---

# AWOS project-local Zed skills

These reviewed skills are loaded by Zed only for this trusted AWOS worktree. They are intentionally small and point to canonical AWOS protocols rather than replacing them.

| Skill | Use when | Canonical references |
|---|---|---|
| `awos-session-start` | Starting a session or recovering context | `AGENT_INDEX.md`, `memories/repo/project_structure.md`, `docs/memory/`, `tasks/active/` |
| `awos-change-gate` | Before non-trivial implementation or multi-file edits | `AWOS.md`, `protocols/RESEARCH_PROTOCOL.md`, `protocols/IMPLEMENTATION_PROTOCOL.md` |
| `awos-evaluation` | Running or interpreting E2E, benchmark, reward, or regression evaluation | `scaffold/agent/evaluation_v1.py`, `scripts/run_evaluation_v1.py`, `docs/evaluation_v1.md` |
| `awos-debug-safety` | Repeated failures or execution/sandbox/security work | `protocols/DEBUG_PROTOCOL.md`, `protocols/SECURITY_PROTOCOL.md`, sandbox task/spec |

## Trust and invocation

- Trust the repository in Zed before expecting project-local skills in the Agent catalog.
- Skills may be selected automatically or invoked with `/awos-session-start`, `/awos-change-gate`, `/awos-evaluation`, and `/awos-debug-safety`.
- These skills do not install dependencies, call providers, modify files automatically, or replace tests.
- Review changes to these files like prompt/configuration changes: they affect future agent behavior.
