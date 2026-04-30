# Agentic OS — Backbone for Complex Agent-Driven Projects

> **The problem with AI-assisted development on complex projects is not capability — it is coherence.**
> An agent that forgets what it decided, re-derives architecture mid-implementation, loses track of state
> across sessions, or drifts between "what we planned" and "what we built" will fail on any project
> that takes more than a single sitting to complete. This repository is the fix.

**Agentic OS** is a project-agnostic backbone: a living workflow system, memory architecture,
automation toolkit, and minimal Python scaffold that gives any agent — and the human working with it —
the structure needed to manage complexity at scale.

---

## What's in This Repo

```
agentic-os/
├── AWOS.md                        ← The living doctrine document. Read this first.
├── QUICK_START.md                 ← Bootstrap a new project in 5 minutes.
│
├── .github/
│   └── copilot-instructions.md   ← Drop into any project to activate the full workflow.
│
├── docs/                         ← Research → Spec → Memory artifacts
│   ├── research/RESEARCH_TEMPLATE.md
│   ├── specs/SPEC_TEMPLATE.md
│   ├── adr/TEMPLATE.md
│   └── memory/CHECKPOINT_TEMPLATE.md
│
├── tasks/                        ← Task tracking (active + done)
│   ├── active/TASK_TEMPLATE.md
│   └── done/
│
├── wiki/                         ← Knowledge graph foundation
│   ├── SCHEMA.md                 ← When/how to create wiki pages
│   ├── GLOSSARY.md
│   └── log.md
│
├── memories/repo/                ← Canonical project facts (one file, no copies)
│   └── project_structure.md
│
├── protocols/                    ← Step-by-step operating guides
│   ├── DEBUG_PROTOCOL.md
│   ├── RESEARCH_PROTOCOL.md
│   ├── IMPLEMENTATION_PROTOCOL.md
│   ├── INTERNET_RESEARCH_PROTOCOL.md
│   ├── MEMORY_PROTOCOL.md
│   ├── MATH_PROTOCOL.md
│   └── SECURITY_PROTOCOL.md
│
├── scripts/                      ← Automation — lint, checkpoint, quality gate
│   ├── session_checkpoint.py
│   ├── obsidian_lint.py
│   ├── obsidian_linkify.py
│   ├── quality_gate.py
│   ├── rotate_checkpoints.py
│   ├── fact_lint.py
│   ├── extract_patterns.py
│   └── new_project.py           ← Bootstrap a new project from this template
│
├── scaffold/                     ← Minimal Python agent scaffold (extend, don't fork)
│   └── agent/
│       ├── config/settings.py
│       ├── memory/store.py
│       ├── tools/base.py
│       └── core/orchestrator.py
│
└── examples/                     ← Working minimal examples
```

---

## Core Insight

> **An agent without structure is a very fast way to build incoherent software.**

Three failure modes kill complex agent projects:

1. **Memory loss between sessions** — the agent re-derives decisions, contradicts prior work, re-reads the entire codebase on every session start.
2. **Unplanned implementation** — code is written before the architecture is clear; bugs require re-reading what was just written.
3. **Fact drift** — the same number or decision exists in five places and they disagree.

This system eliminates all three:

| Failure | Solution |
|---|---|
| Memory loss | Session checkpoints + single-owner canonical files + cold-start protocol |
| Unplanned implementation | Research → Spec → Task triad, enforced by preflight gate |
| Fact drift | Single-Owner Rule + `fact_lint.py` catches copies before they diverge |

---

## The 30-Second Mental Model

```
Every non-trivial change:
  1. Research phase  → docs/research/<name>.md      (understand, no code)
  2. Spec phase      → docs/specs/<name>_spec.md    (plan, ordered atomic steps)
  3. Task file       → tasks/active/<name>.md        (track, source of truth)
  4. Implement       → one step at a time, mark done
  5. Checkpoint      → docs/memory/checkpoint_<date>.md (persist for next session)
```

The task file is the only thing that needs to survive a session end to resume cold.

---

## How to Use This System

### Option A — New Project (recommended)
```bash
python scripts/new_project.py --name my-project --dest ~/projects/my-project
```
This scaffolds the full directory structure, copies all templates, seeds the memory file, and initialises git.

### Option B — Add to Existing Project
1. Copy `.github/copilot-instructions.md` into your project's `.github/`
2. Copy `docs/`, `tasks/`, `wiki/`, `memories/`, `scripts/` into your project root
3. Fill in `memories/repo/project_structure.md` with your project's actual facts
4. Start a session: `python scripts/session_checkpoint.py -m "cold start"`

### Option C — Just the Doctrine
Read `AWOS.md`. It is the complete playbook. Everything else in this repo is the tooling to execute it.

---

## The Knowledge Graph (Obsidian Integration)

This system is built around an Obsidian-compatible knowledge graph. Every markdown file:
- Has YAML frontmatter (`title`, `tags`)
- Uses `[[wiki links]]` for cross-references (never bare file paths)
- Has a `## Related` section linking the research ↔ spec ↔ task triad

Open the project root as an Obsidian vault. Graph View filtered by tag shows the state of any topic cluster instantly.

Programmatic navigation (when Obsidian UI is not available):
```bash
grep -r "\[\[filename\]\]" docs/ tasks/ wiki/   # find all backlinks to a file
grep -r "topic/convergence" docs/ tasks/         # find all files tagged with a topic
```

---

## Automation Scripts

| Script | What it does |
|---|---|
| `scripts/session_checkpoint.py` | Auto-generate a checkpoint from git state + active tasks |
| `scripts/obsidian_lint.py` | Check all markdown files for missing frontmatter and broken links |
| `scripts/obsidian_linkify.py` | Auto-add frontmatter and convert bare paths to wiki links |
| `scripts/quality_gate.py` | Pre-commit: tests pass + lint clean + task steps checked |
| `scripts/rotate_checkpoints.py` | Archive old checkpoints when count exceeds threshold |
| `scripts/fact_lint.py` | Detect numeric constants duplicated outside their canonical owner |
| `scripts/extract_patterns.py` | Mine completed tasks and checkpoints for reusable patterns |
| `scripts/new_project.py` | Bootstrap a new project from this template |

---

## Philosophy

This is not a framework that constrains what you build. It is an operating system for how you build it.

The principles are:
- **Atomicity** — every step changes one thing, tests one thing, proves one thing
- **Persistence** — every decision is written to a file before it is considered done
- **Single ownership** — every fact lives in exactly one place
- **Research before code** — no implementation without a research phase and spec
- **Checkpoints as handoffs** — the last thing a session writes is the first thing the next session reads

These principles are not bureaucracy. They are the minimum structure required to keep a complex project coherent across dozens of sessions and hundreds of files.

---

## Contributing Back

When you discover a pattern that works in your project, update `AWOS.md` and bump the version.
When a pattern fails, strike it out and note why.
The AWOS is a living document. A stale AWOS is a liability. A maintained AWOS is a compounding asset.
