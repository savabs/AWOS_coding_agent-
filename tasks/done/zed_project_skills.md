---
title: "Task: Add AWOS project-local Zed skills"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/zed-skills
  - layer/agent-workflow
---

# Task: Add AWOS project-local Zed skills

**Status:** active  
**Phase:** 1  
**Research:** [[zed_project_skills]]  
**Spec:** [[zed_project_skills_spec]]  
**Started:** 2026-07-30  
**Completed:** —

> **This file is the source of truth for progress.**

## Context
Create a small, reviewed project-local Zed skill pack so future AWOS development sessions consistently load the repo’s cold-start, research/spec/task, evaluation, and debugging/safety practices. Success means the files are discoverable, frontmatter-valid, scoped to AWOS, and do not claim capabilities the repository has not proven.

## Steps
- [x] 1.1 Create four project-local `SKILL.md` files → each has valid Zed frontmatter.
- [x] 1.2 Keep skill bodies focused and below Zed’s 500-line guidance → line-count check passes.
- [x] 1.3 Add a skill manifest/readme for human discoverability → all four skills and triggers are listed.
- [x] 1.4 Validate names, paths, and no nested skill directories → scripted filesystem check passes.
- [x] 1.5 Run focused AWOS evaluation tests → `13 passed` in `tests/test_evaluation_v1.py`.
- [x] 1.6 Run scoped Obsidian lint → eight new/changed Markdown files clean; full vault remains blocked by 378 pre-existing errors.
- [x] 1.7 Write checkpoint and update project facts → completed in checkpoint `2026-07-30`.

## Blockers
- The full-vault Obsidian lint remains red with 378 pre-existing errors outside this task.
- Project-local skills require the Zed worktree to be trusted before they appear in the Agent catalog.

## Related
- [[zed_project_skills_spec]]
- [[zed_project_skills]]

## Evidence
- `python3 -c "...skill validation..."` → four valid flat skills.
- `python3 -m pytest -q tests/test_evaluation_v1.py` → 13 passed.
- `python3 scripts/obsidian_lint.py --path .agents/skills` → clean.
- `python3 scripts/obsidian_lint.py --files ...` → eight files clean.
