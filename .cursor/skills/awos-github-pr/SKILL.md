---
name: awos-github-pr
description: Creates GitHub pull requests for AWOS using gh CLI and github MCP. Use when the user asks to open a PR, push a branch, or summarize changes for review.
---

# AWOS GitHub PR Workflow

## Prerequisites

- `GITHUB_PERSONAL_ACCESS_TOKEN` set (scopes: `repo`, `read:org`)
- GitHub MCP enabled in `.cursor/mcp.json`
- Optional: `gh` CLI installed

## Steps

1. `git status` + `git diff` — understand full branch delta
2. Push branch: `git push -u origin HEAD`
3. Create PR with `gh pr create` or GitHub MCP
4. PR body: Summary (1-3 bullets) + Test plan checklist
5. Return PR URL

## AWOS-specific test plan items

- [ ] `pytest -q --timeout=10`
- [ ] Live proof demo if kernel/CLI changed
- [ ] No edits to `.awosignore` paths without justification

Never force-push to main/master.
