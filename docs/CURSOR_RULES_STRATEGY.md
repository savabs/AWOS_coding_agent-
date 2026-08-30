# Cursor Rules Strategy — AWOS (2026)

> Advanced rule-setting for maximum agent accuracy without prompt repetition.
> Complements: `docs/CURSOR_INDEXING_SETUP.md` · `.cursor/RULES.md`

---

## 1. Rule hierarchy (how layers stack)

```
User Rules (global, Cursor Settings)
    ↓
Always-apply project rules (.cursor/rules/, alwaysApply: true)
    ↓
Scoped project rules (globs match open/edited files)
    ↓
@ mentions you add per prompt (@file, @codebase, @docs, @diff)
```

**Principle:** Rules teach *architecture and process*. Linters teach *style*. `@` mentions supply *task-specific context*.

---

## 2. Always-apply rules (3 max — keep lean)

AWOS uses exactly three. More always-on rules = diluted attention.

| Rule | Why always-on |
|------|----------------|
| `awos-vision.mdc` | Identity + stage discipline — prevents category errors |
| `awos-live-proof.mdc` | Kernel features need observable proof, not pytest alone |
| `awos-codebase-index.mdc` | Points to active paths; blocks editing `src/` by mistake |

**Anti-pattern:** Pasting `CONVENTIONS.md` or full `AWOS.md` into always-apply rules. Link them; load via scoped rules or `@file` when implementing.

---

## 3. Scoped rules (the 2026 standard)

Cursor loads **only** rules whose `globs` match your current context. AWOS scopes by subsystem:

| When you're editing… | Rule loads |
|----------------------|------------|
| Any `scaffold/agent/*.py` | `scaffold-agent.mdc` |
| `orchestrator.py` specifically | `awos-orchestrator.mdc` (extra invariants) |
| `awos.py`, worker, sessions | `awos-hotpath-safety.mdc` |
| `tests/` | `awos-tests.mdc` |
| `scripts/demo_*`, `*_proof.md` | `awos-scripts-proof.mdc` |
| `docs/research`, `docs/specs`, `tasks/` | `awos-artifacts.mdc` |
| `tools/`, `gui/`, `mcp_server.py` | `awos-security.mdc` |
| Checkpoints, `memories/` | `awos-checkpoints.mdc` |

Full index: `.cursor/RULES.md`

### Authoring new scoped rules

1. **One concern** — orchestrator invariants ≠ pytest conventions
2. **Under ~45 lines** — if longer, split or move detail to `docs/`
3. **Actionable** — "wire through `reward_store.compute_reward()`" not "be careful with rewards"
4. **Link, don't copy** — `protocols/SECURITY_PROTOCOL.md` stays canonical; rule extracts hot checks only

---

## 4. Recommended User Rules (global)

Paste into **Cursor Settings → Rules → User Rules** (applies to all projects):

```markdown
- Prefer minimal diffs; do not refactor unrelated code.
- Read a file before editing it.
- After code changes, run the relevant test command and report output.
- Use code citation format: ```startLine:endLine:filepath
- Do not commit unless explicitly asked.
- When stuck after 2 failed fix attempts, stop patching and investigate root cause.
```

AWOS-specific behavior stays in **project** rules — don't duplicate vision/process globally.

---

## 5. Context symbols — AWOS workflow

Use explicit `@` mentions to complement rules (rules = standing context; `@` = task context).

| Prompt pattern | Context to attach |
|----------------|-------------------|
| "Fix orchestrator pause/resume" | `@file:scaffold/agent/orchestrator.py` `@file:scaffold/agent/runtime_session.py` |
| "Where is reward computed?" | `@codebase` or `@code:compute_reward` |
| "Implement per spec step 3" | `@file:tasks/active/<task>.md` `@file:docs/specs/<feature>_spec.md` |
| "Why did demo fail?" | `@terminal` `@file:docs/<feature>_proof.md` |
| "Review my changes" | `@diff` |
| "Add CLI command" | `@file:awos.py` `@file:tests/test_awos_sessions_cli.py` |

**Avoid:** `@folder:scaffold/agent` for whole kernel — too much noise. Use `@codebase` query or specific files.

---

## 6. Indexing & Docs (pairs with rules)

| Doc | Role in rules strategy |
|-----|------------------------|
| `docs/CODEBASE_INDEX.md` | Module map — cite via `@file`, indexed for `@codebase` |
| `VISION.md` | Identity — rule links here; never restate in chat |
| `AWOS.md` | Process detail — `@file` when implementing |
| `memories/repo/project_structure.md` | Facts — single owner; `awos-checkpoints.mdc` enforces |

**External @Docs** (Settings → Indexing & Docs → Add Doc):

- Python 3.12 stdlib (if not bundled)
- Your LLM provider API docs (Anthropic/OpenAI/DeepSeek)
- `pytest` docs for marker/timeout patterns

---

## 7. MCP (GitHub — configured)

Project config: `.cursor/mcp.json` — GitHub MCP via `@modelcontextprotocol/server-github`.

Setup: `docs/CURSOR_AUTOMATION_SETUP.md` §1 — set `GITHUB_PERSONAL_ACCESS_TOKEN` in Cursor MCP settings.

---

## 8. Hooks, Skills & Subagents (configured)

| Layer | Path | AWOS setup |
|-------|------|------------|
| **Hooks** | `.cursor/hooks.json` | Pre-edit block (`.awosignore`), post-edit ruff, subagent audit log |
| **Skills** | `.cursor/skills/*/SKILL.md` | live-proof, preflight, kernel-change, github-pr |
| **Subagents** | `.cursor/agents/*.md` | live-proof-verifier, kernel-explorer, pr-ci-investigator |

Full guide: `docs/CURSOR_AUTOMATION_SETUP.md`

---

## 9. Hooks & BugBot (team scale)

| Tool | AWOS use case |
|------|----------------|
| **Hooks** `onPostEdit` | Run `ruff check` on edited `.py` files |
| **Hooks** `onPreEdit` | Block edits to `.awosignore` paths during dogfood |
| **BugBot** | PR review against `.cursor/rules/` — catches live-proof gaps |

Not configured by default. See `create-hook` skill when team needs enforcement.

---

## 9. Composer / Agent / Plan mode

| Mode | AWOS best practice |
|------|---------------------|
| **Agent** | Default for kernel work; rules auto-load by file |
| **Plan** | Use for multi-file features *before* Research→Spec→Task exists |
| **Composer** | Good for 2–5 file surgical edits with explicit `@file` list |

**Plan mode + AWOS:** If Plan produces steps, convert to `docs/specs/` + `tasks/active/` before marking done — aligns with preflight gate in `awos-artifacts.mdc`.

---

## 10. Maintenance checklist

When adding a major subsystem:

- [ ] Row in `docs/CODEBASE_INDEX.md`
- [ ] Scoped rule if architectural invariants exist
- [ ] Entry in `.cursor/RULES.md`
- [ ] Live proof script if user-visible (`awos-scripts-proof.mdc` applies)
- [ ] Reindex if new top-level directory (`docs/CURSOR_INDEXING_SETUP.md`)
