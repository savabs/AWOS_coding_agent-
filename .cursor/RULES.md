# Cursor Rules Index — AWOS

> Maps every `.cursor/rules/*.mdc` file to when it loads.
> Strategy guide: `docs/CURSOR_RULES_STRATEGY.md`

## Always apply (every chat)

| Rule | Concern |
|------|---------|
| `awos-vision.mdc` | Identity (`VISION.md`), stage discipline, AWOS process |
| `awos-live-proof.mdc` | Runnable demo + proof doc for kernel features |
| `awos-codebase-index.mdc` | Module map pointers, active vs legacy paths |

## Scoped (load when matching files are open)

| Rule | Globs | Concern |
|------|-------|---------|
| `scaffold-agent.mdc` | `scaffold/agent/**/*.py` | Kernel architecture, module placement |
| `awos-orchestrator.mdc` | `scaffold/agent/orchestrator.py` | Hot-path loop invariants |
| `awos-hotpath-safety.mdc` | `awos.py`, `worker.py`, `runtime_session.py`, `worktree.py` | Dogfood-protected files |
| `awos-security.mdc` | `tools/**`, `gui/**`, `mcp_server.py` | Shell, secrets, injection |
| `awos-cli.mdc` | `awos.py` | CLI handler pattern |
| `awos-tests.mdc` | `tests/**/*.py` | Pytest layout, markers |
| `awos-scripts-proof.mdc` | `scripts/demo_*`, `docs/*_proof.md` | Live proof artifacts |
| `awos-artifacts.mdc` | `docs/research/**`, `docs/specs/**`, `tasks/**` | Research → Spec → Task gate |
| `awos-checkpoints.mdc` | `docs/memory/**`, `memories/**` | Single-owner facts, checkpoints |

## Recommended @ mentions (don't duplicate in rules)

| Symbol | When |
|--------|------|
| `@docs/CODEBASE_INDEX.md` | Cross-module navigation |
| `@codebase` | "Where is X handled?" without knowing the file |
| `@diff` | Review-oriented tasks |
| `@terminal` | After failed pytest/demo |
| `@folder:scaffold/agent` | Sparingly — prefer `@codebase` or specific `@file` |

## Do NOT put in rules

- Formatting (use `ruff` — auto via `afterFileEdit` hook)
- Full style guides (use linters + `CONVENTIONS.md` as reference doc)
- Identity restatements (link `VISION.md`)

## Skills & subagents (use instead of bloating rules)

| Type | Path | When |
|------|------|------|
| Skills | `.cursor/skills/` | On-demand workflows (live proof, preflight, PR) |
| Subagents | `.cursor/agents/` | Delegated readonly verification or exploration |

See `docs/CURSOR_AUTOMATION_SETUP.md`
