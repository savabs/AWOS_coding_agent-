# AWOS Agent Index — Cold-Start Reference

> **This file is for the agent, not the human.**
> Read this first on every session start in an AWOS project.
> It maps every canonical file to its purpose and tells you what to read, in order.

---

## Cold-Start Sequence (do this every session)

```
1. Read this file                          → understand the map
2. Read memories/repo/project_structure.md → get project facts (phase, modules, metrics)
3. Read latest docs/memory/checkpoint_*.md → get what happened last session
4. Read tasks/active/*.md                  → get current work and next atomic step
5. Follow [[wiki links]] in those files    → reach relevant research/spec context
```

Do NOT re-read the full codebase. The above 5 files are all you need.

---

## File Map — Every Canonical Location

### Doctrine (read-only unless you're maintaining AWOS itself)

| File | What it contains |
|---|---|
| `AWOS.md` | The full doctrine — all rules, all protocols, all patterns. 900 lines. |
| `.github/copilot-instructions.md` | Distilled operating rules for agents. Read by Copilot automatically. |
| `QUICK_START.md` | Human-oriented 5-minute project bootstrap guide. |
| `AGENT_INDEX.md` | This file. Agent cold-start map. |

### Canonical Facts (one owner per fact — Single-Owner Rule)

| File | What it contains | Update trigger |
|---|---|---|
| `memories/repo/project_structure.md` | Project identity, key modules, build commands, current metrics, active phase | After any metric change, phase change, or major architecture change |
| `wiki/log.md` | Running activity log (append-only) | After every meaningful action |

### Session State

| File | What it contains | Update trigger |
|---|---|---|
| `docs/memory/checkpoint_YYYY-MM-DD.md` | Session summary: what changed, what is blocked, source of truth for next session | At every natural session end — NEVER skip |
| `tasks/active/<task>.md` or `.html` | Current execution checklist — source of truth for progress | After each atomic step completed |

### Knowledge Graph (wiki)

| File | What it contains |
|---|---|
| `wiki/AGENTIC_CONCEPTS.html` | Project-agnostic reference: agent architectures, tool use, memory, reasoning, eval, multi-agent, context, decomposition |
| `wiki/GLOSSARY.html` | All AWOS terms defined |
| `wiki/SCHEMA.html` | When and how to create wiki pages |
| `wiki/log.md` | Running activity log (append-only — never edit past entries) |

### Protocols (step-by-step operating guides)

| File | When to read |
|---|---|
| `protocols/DEBUG_PROTOCOL.md` | When stuck on a bug after 2 failed attempts |
| `protocols/IMPLEMENTATION_PROTOCOL.md` | Before starting any implementation phase |
| `protocols/RESEARCH_PROTOCOL.md` | Before starting any research phase |
| `protocols/INTERNET_RESEARCH_PROTOCOL.md` | When external knowledge is needed |
| `protocols/MEMORY_PROTOCOL.md` | For checkpoint and memory management |
| `protocols/MATH_PROTOCOL.md` | When the task involves math or formulas |
| `protocols/SECURITY_PROTOCOL.md` | For any security-relevant changes |
| `protocols/RL_TRAINING_PROTOCOL.md` | For RL/LLM training tasks |

### Research → Spec → Task Triad

| Location | Artifact | When to create |
|---|---|---|
| `docs/research/<feature>.html` | Research note (canonical) | Before any implementation of a non-trivial feature |
| `docs/research/<feature>.md` | Research stub (Obsidian) | Created alongside the .html |
| `docs/specs/<feature>_spec.html` | Spec (canonical) | After research, before coding |
| `docs/specs/<feature>_spec.md` | Spec stub (Obsidian) | Created alongside the .html |
| `tasks/active/<task>.html` | Task file (canonical) | After spec, tracks atomic steps |
| `tasks/active/<task>.md` | Task stub (Obsidian) | Created alongside the .html |
| `tasks/done/` | Completed tasks (moved, never deleted) | On task completion |

### Architecture Decisions

| Location | What it contains |
|---|---|
| `docs/adr/0001-project-bootstrap.html` | The decision to adopt AWOS for this project |
| `docs/adr/NNNN-<slug>.html` | Significant design decisions (numbered, immutable) |

### Automation Scripts

| Script | What it does | Run when |
|---|---|---|
| `scripts/session_checkpoint.py` | Auto-generate checkpoint from git state + active tasks | Session end |
| `scripts/obsidian_lint.py` | Check all .md files for missing frontmatter + broken links | Before committing |
| `scripts/quality_gate.py` | Pre-commit: tests + lint + task steps | Before every commit |
| `scripts/fact_lint.py` | Detect numeric constants duplicated outside canonical owner | Periodically |
| `scripts/new_project.py` | Bootstrap a new project from this template | Once per new project |

---

## The 5 Unbreakable Rules

1. **No code before preflight** — Research doc + spec + task file must exist before any implementation. No exceptions.
2. **Write-Gate** — A decision is not real until it exists in a file *in the same turn*. Deferred writes are forbidden.
3. **Two-failed-attempt rule** — After 2 failed fix attempts: STOP, read `protocols/DEBUG_PROTOCOL.md`, research first.
4. **Checkpoint every session** — Write `docs/memory/checkpoint_YYYY-MM-DD.md` at every session end. User should never have to ask.
5. **Single-Owner Rule** — Every fact lives in exactly one canonical file. Never copy — always link.

---

## HTML-First Artifacts Rule

All output artifacts are **HTML primary + thin MD stub**. This applies to:
- Research notes → `docs/research/<feature>.html` + `docs/research/<feature>.md` (stub only)
- Specs → `docs/specs/<feature>_spec.html` + stub
- Task files → `tasks/active/<task>.html` + stub
- ADRs → `docs/adr/NNNN-<slug>.html` + stub
- Wiki pages → `wiki/<slug>.html` + stub
- Checkpoints → `docs/memory/checkpoint_YYYY-MM-DD.html` + stub

**The stub template** (copy this for every new artifact):
```markdown
---
title: "<title>"
tags:
  - doc/<type>
  - topic/<slug>
---

> **Content:** [<filename>.html](<filename>.html) — open in browser.

## Related
- [[linked_doc]]
```

**HTML document conventions:**
- Self-contained — no external CSS/JS dependencies
- Header: title, tags, date
- Tab navigation for multi-section docs
- Code blocks: `<pre><code>` with syntax highlighting
- End every interactive doc with a **"Copy as Prompt"** button
- Never ASCII art — use SVG/CSS diagrams

**Still OK as plain Markdown** (no HTML companion needed):
- The `.md` stub files themselves
- `README.md`
- `wiki/log.md` (append-only)
- `AWOS.md`, `AGENT_INDEX.md`, `QUICK_START.md`

---

## Artifact Creation Quick Reference

### New research note
```bash
# 1. Create the HTML (copy from docs/research/RESEARCH_TEMPLATE.html)
# 2. Create the MD stub (copy from docs/research/RESEARCH_TEMPLATE.md)
# 3. Fill both with content
# 4. Link from the relevant task file
```

### New spec
```bash
# 1. Create docs/specs/<feature>_spec.html (copy from SPEC_TEMPLATE.html)
# 2. Create docs/specs/<feature>_spec.md (stub)
# 3. Fill in ordered atomic steps
# 4. Link from the task file
```

### New task file
```bash
# 1. Create tasks/active/<task>.html (copy from TASK_TEMPLATE.html)
# 2. Create tasks/active/<task>.md (stub)
# 3. Fill in checklist (numbered atomic steps with checkboxes)
# 4. Update memories/repo/project_structure.md → "Active Tasks" section
```

### Session checkpoint
```bash
python scripts/session_checkpoint.py -m "brief summary of session"
# Or create docs/memory/checkpoint_YYYY-MM-DD.html manually (use CHECKPOINT_TEMPLATE.html)
```

---

## Obsidian Knowledge Graph

This project is an Obsidian-compatible knowledge graph. Every `.md` file has:
- YAML frontmatter (`title`, `tags`)
- `[[wiki links]]` for cross-references (never bare file paths)
- A `## Related` section

Open the project root as an Obsidian vault. Graph View filtered by `topic/` tag shows any topic cluster.

Quick navigation without Obsidian:
```bash
grep -r "\[\[filename\]\]" docs/ tasks/ wiki/  # find all backlinks to a file
grep -r "topic/feature-x" docs/ tasks/ wiki/   # find all files on a topic
```

---

## Related

- `AWOS.md` — full doctrine (900 lines, all rules)
- `.github/copilot-instructions.md` — agent operating instructions (auto-loaded by Copilot)
- `memories/repo/project_structure.md` — canonical project facts
- `wiki/GLOSSARY.html` — all terms defined
- `wiki/AGENTIC_CONCEPTS.html` — agent architecture and pattern reference
