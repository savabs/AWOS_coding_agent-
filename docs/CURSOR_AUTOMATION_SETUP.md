# Cursor Automation Setup — AWOS

> Hooks, skills, subagents, and GitHub MCP for this repo.
> Pairs with: `docs/CURSOR_INDEXING_SETUP.md` · `docs/CURSOR_RULES_STRATEGY.md`

Last updated: 2026-06-29

---

## 1. GitHub MCP

**Config:** `.cursor/mcp.json`

### Enable

1. Create a GitHub PAT: Settings → Developer settings → Fine-grained or classic token
   - Scopes: `repo`, `read:org`, `read:user`
2. **Option A (recommended):** Cursor Settings → **MCP** → `github` server → set `GITHUB_PERSONAL_ACCESS_TOKEN`
3. **Option B:** Export in shell before launching Cursor:
   ```bash
   export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
   ```
4. Restart Cursor or reload MCP servers
5. Verify: Agent can list PRs / issues via GitHub MCP tools

### Uses

- `pr-ci-investigator` subagent reads failed CI checks
- `awos-github-pr` skill for PR creation workflow
- Agent queries issues/PRs without leaving the editor

---

## 2. Hooks (`.cursor/hooks.json`)

| Event | Script | Behavior |
|-------|--------|----------|
| `preToolUse` | `block-awosignore-edits.py` | **Denies** Write/Edit to `.awosignore` paths |
| `afterFileEdit` | `post-edit-ruff.sh` | Runs `ruff check --fix` + `ruff format` on `.py` (best-effort) |
| `subagentStart` | `log-subagent-start.sh` | Audit log → `.awos/cursor_subagent.log` |

### Pre-edit protection

Blocks edits to:
- `scaffold/agent/orchestrator.py`, `worker.py`, `unified_agent.py`
- `awos.py`, `worktree.py`, `runtime_session.py`
- `.env`, `.git/`

To edit these deliberately: stop AWOS dogfood sessions, or temporarily remove path from `.awosignore`.

### Verify hooks loaded

1. Cursor Settings → **Hooks** tab — should list 3 hooks
2. Or: Hooks output channel in panel
3. Test block: ask Agent to edit `orchestrator.py` → should deny

### Test pre-edit hook manually

```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"scaffold/agent/orchestrator.py"}}' \
  | python3 .cursor/hooks/block-awosignore-edits.py
# Expect: permission deny, exit 2
```

---

## 3. Project Skills (`.cursor/skills/`)

| Skill | Invoke when |
|-------|-------------|
| `awos-live-proof` | Creating demo + proof doc for kernel features |
| `awos-preflight` | Starting feature without research/spec/task |
| `awos-kernel-change` | Editing `scaffold/agent/` hot path |
| `awos-github-pr` | Opening PRs, branch push workflow |

Skills load on demand when the agent matches the description, or when you name them in chat.

---

## 4. Custom Subagents (`.cursor/agents/`)

| Agent | Mode | Use |
|-------|------|-----|
| `live-proof-verifier` | readonly | After kernel work — run demo, check markers |
| `kernel-explorer` | readonly | Map modules, trace flow, no edits |
| `pr-ci-investigator` | readonly | Red CI / PR URL → root cause via GitHub MCP |

### Invoke

```
Use the live-proof-verifier subagent to confirm stagnation breaker proof
```

```
/kernel-explorer where is pause/resume handled?
```

Or natural language — Agent delegates based on `description` in frontmatter.

### Built-in subagents (no config needed)

`explore`, `shell`, `browser` — still available. Project agents add AWOS-specific behavior.

---

## 5. Settings UI checklist

| Setting | Location | Action |
|---------|----------|--------|
| Codebase indexing | Indexing & Docs | ON, reindex after ignore changes |
| MCP GitHub | MCP | Token set, server green |
| Hooks | Hooks | 3 hooks visible |
| Subagent models | Agents | Optional: set explore model to fast tier |
| User Rules | Rules | Paste from `CURSOR_RULES_STRATEGY.md` §4 |

---

## 6. File tree

```
.cursor/
├── agents/           # Custom subagents (.md + YAML frontmatter)
├── hooks/            # Hook scripts
├── hooks.json        # Hook registry
├── mcp.json          # GitHub MCP (no secrets committed — set token in UI)
├── mcp.json.example  # Template
├── rules/            # 12 scoped + always-on rules
├── skills/           # 4 project skills
└── RULES.md          # Rules index
```

---

## 7. Troubleshooting

| Problem | Fix |
|---------|-----|
| MCP github red | Set `GITHUB_PERSONAL_ACCESS_TOKEN`; `npx -y @modelcontextprotocol/server-github` needs Node |
| Hooks not firing | Save `hooks.json`, restart Cursor, check Hooks tab |
| Pre-edit not blocking | Confirm matcher includes your tool type; check Hooks output |
| Ruff post-edit silent | Install ruff: `pip install ruff` or add to project venv |
| Subagent not found | File must be `.cursor/agents/<name>.md` with valid YAML |
