# Quick Start — Bootstrap a Project in 5 Minutes

---

## Step 1 — Clone or copy the template

```bash
# Option A: from GitHub
git clone https://github.com/your-org/agentic-os.git my-project
cd my-project

# Option B: via the bootstrap script (if you already have agentic-os locally)
python /path/to/agentic-os/scripts/new_project.py --name my-project --dest ~/projects/my-project
cd ~/projects/my-project
```

---

## Step 2 — Fill in the canonical facts file

Open `memories/repo/project_structure.md` and fill in:
- Project identity (what it does, what the edge is)
- Key modules and entry points
- Build/run commands
- Current phase and active task

This file is the cold-start accelerator. Every future session reads this first.

---

## Step 3 — Activate your agent

Copy `.github/copilot-instructions.md` into your project's `.github/` directory
(it's already there if you used `new_project.py`).

Open the project in VS Code. GitHub Copilot will now follow the Agentic OS workflow.

---

## Step 4 — Start your first research doc

For any non-trivial feature:

```bash
cp docs/research/RESEARCH_TEMPLATE.md docs/research/my_feature.md
# Fill in: Current Architecture, Observations, Risks, Data Requirements
```

No code until the research doc exists. This is the preflight gate.

---

## Step 5 — Write the spec

```bash
cp docs/specs/SPEC_TEMPLATE.md docs/specs/my_feature_spec.md
# Fill in: Goal, Files Affected, Implementation Steps, Edge Cases, Testing Plan
```

---

## Step 6 — Create the task file

```bash
cp tasks/active/TASK_TEMPLATE.md tasks/active/my_feature.md
# Fill in: status, links to research + spec, numbered steps
```

Now implementation can start. Open the task file and work through steps one at a time.

---

## Step 7 — End your session with a checkpoint

```bash
python scripts/session_checkpoint.py -m "completed steps 1-3, blocked on X"
```

The checkpoint is the handoff artifact. The next session reads it first.

---

## Daily Workflow (ongoing)

```bash
# Start session: read latest checkpoint + active task
cat docs/memory/$(ls -t docs/memory/ | head -1)
cat tasks/active/my_feature.md

# During work: run lint periodically
python scripts/obsidian_lint.py

# Before marking a task done: run quality gate
python scripts/quality_gate.py --task tasks/active/my_feature.md

# End session: checkpoint
python scripts/session_checkpoint.py -m "summary of what was done"
```

---

## Key Files to Know

| File | Purpose |
|---|---|
| `AWOS.md` | The full doctrine — read this to understand why the system works |
| `memories/repo/project_structure.md` | Canonical facts — the single source of truth for your project |
| `tasks/active/<name>.md` | Current task — the source of truth for progress (resumable cold) |
| `docs/memory/checkpoint_<date>.md` | Session handoff — what happened, what's next |
| `.github/copilot-instructions.md` | Agent instructions — activates the full workflow for any AI assistant |

---

## What "Non-Trivial" Means

The research → spec → task preflight is required for any change that:
- Changes behaviour
- Touches more than one file
- Changes architecture, dependencies, or configuration
- Requires external concepts or design judgment

The only exception: genuinely trivial single-file changes (typos, comment wording, narrowly scoped cleanup).
When in doubt, treat as non-trivial.
