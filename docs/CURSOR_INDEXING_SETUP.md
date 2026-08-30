# Cursor Indexing & Docs Setup

> How this repo is configured for Cursor semantic search and agent context.
> Last updated: 2026-06-29

---

## What Was Configured

| File | Purpose |
|------|---------|
| `.cursorignore` | Blocks AI access **and** indexing (worktrees, secrets, git) |
| `.cursorindexingignore` | Skips indexing only; files remain @-mentionable |
| `docs/CODEBASE_INDEX.md` | Master module map (this doc + semantic index) |
| `.cursor/rules/awos-codebase-index.mdc` | Always-on pointer to index |
| `.cursor/rules/scaffold-agent.mdc` | Conventions when editing `scaffold/agent/` |
| `.cursor/rules/awos-tests.mdc` | Test conventions for `tests/` |
| `.cursor/rules/awos-cli.mdc` | CLI conventions for `awos.py` |

### Why `.awos/worktrees/` is ignored

AWOS session isolation copies the **entire repo** into `.awos/worktrees/<hash>/` (~1.3GB, 12+ copies). Without exclusion, Cursor indexes the same code 10× over and search quality degrades.

---

## Reindex After Setup

1. Open **Cursor Settings → Indexing & Docs**
2. Confirm indexing is enabled for this workspace
3. Command palette (`Ctrl+Shift+P`): **"Reindex Codebase"** or **"Resync Index"**
4. Click **View included files** — expect ~500–800 files, not 10,000+

### Success markers

- `.awos/worktrees/` paths **absent** from included files list
- `scaffold/agent/orchestrator.py` **present**
- `docs/CODEBASE_INDEX.md` **present**
- `src/` absent (legacy TS, indexing-only ignore)

---

## Add Docs (Indexing & Docs tab)

In **Cursor Settings → Indexing & Docs → Add Doc**, add these for `@Docs` context:

| Name | URL or path |
|------|-------------|
| AWOS Codebase Index | `docs/CODEBASE_INDEX.md` (local — already indexed) |
| Agent Cold-Start | `AGENT_INDEX.md` |
| AWOS Process | `AWOS.md` |
| Live Proof Protocol | `protocols/LIVE_PROOF_PROTOCOL.md` |
| Project Facts | `memories/repo/project_structure.md` |

Local markdown like `CODEBASE_INDEX.md` is already part of the codebase index.

**Rules:** Full strategy in `docs/CURSOR_RULES_STRATEGY.md`. Paste recommended User Rules from §4 into Cursor Settings → Rules.

**Automation:** Hooks, skills, subagents, GitHub MCP — `docs/CURSOR_AUTOMATION_SETUP.md`

---

## Indexed vs Excluded Summary

### Indexed (high value)

- `scaffold/agent/**/*.py` — active kernel
- `tests/test_*.py` — primary suite
- `scripts/` — demos and benchmarks
- `docs/**/*.md`, `tasks/**/*.md`, `protocols/`
- `awos.py`, `gui/`, `wiki/*.md`
- `memories/repo/`

### Excluded from index (`.cursorindexingignore`)

- `.awos/` runtime JSONL and session blobs
- `src/` legacy TypeScript
- `docs/**/*.html` (thin `.md` stubs indexed instead)
- `reports/`, checkpoint archive

### Blocked entirely (`.cursorignore`)

- `.awos/worktrees/` — duplicate full repos
- `.git/`, `.env`, secrets, model weights

---

## Maintenance

When adding major modules:

1. Add row to `docs/CODEBASE_INDEX.md`
2. Add test file reference if applicable
3. If new directory is generated/runtime → add to `.cursorindexingignore`
4. Trigger reindex if search misses new files after 5+ minutes
