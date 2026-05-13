#!/usr/bin/env python3
"""
new_project.py — Bootstrap a new project from the Agentic OS template.

Copies the Agentic OS scaffold to a new destination, seeds key files,
and initializes git.

Usage:
    python scripts/new_project.py --name my-project --dest ~/projects/my-project
    python scripts/new_project.py --name my-project --dest ~/projects/my-project --no-git
"""

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).parent.parent

# Files/dirs to copy (relative to template root)
COPY_ITEMS = [
    ".github",
    "docs",
    "tasks",
    "wiki",
    "memories",
    "scripts",
    "protocols",
    "AWOS.md",
    "AGENT_INDEX.md",
    "QUICK_START.md",
]

# Files to NOT copy (template-specific)
EXCLUDE = {
    "README.md",  # Will be replaced with project-specific README
    "scripts/new_project.py",  # Bootstrap script itself
    ".git",
}


def copy_template(src: Path, dest: Path) -> list[str]:
    """Copy template files to destination. Returns list of copied paths."""
    copied = []
    dest.mkdir(parents=True, exist_ok=True)

    for item in COPY_ITEMS:
        src_path = src / item
        dest_path = dest / item

        if item in EXCLUDE or not src_path.exists():
            continue

        if src_path.is_dir():
            if dest_path.exists():
                shutil.rmtree(dest_path)
            shutil.copytree(
                src_path,
                dest_path,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            copied.append(item + "/")
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            copied.append(item)

    return copied


def create_readme(dest: Path, project_name: str) -> None:
    content = f"""# {project_name}

> Brief description of what this project does.

---

## Project Status

**Current Phase:** 0 (bootstrap)
**Active Task:** (none yet)

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt  # or: poetry install

# Run tests
pytest

# Start agent
python agent/cli.py --goal "your goal here"
```

---

## Architecture

(Fill in: describe the key modules and how they connect)

---

## Workflow

This project uses the [Agentic OS](https://github.com/your-org/agentic-os) workflow:

```
Research → Spec → Task → Implement → Checkpoint
```

See `AWOS.md` for the full doctrine.
See `QUICK_START.md` for a 5-minute onboarding guide.

---

## Session Start

```bash
cat memories/repo/project_structure.md
ls -t docs/memory/*.md | head -1 | xargs cat
cat tasks/active/*.md
```
"""
    (dest / "README.md").write_text(content, encoding="utf-8")


def create_structure_file(dest: Path, project_name: str) -> None:
    today = date.today().isoformat()
    content = f"""# {project_name} — Project Structure

## Identity
**{project_name}** is a (fill in: what it does, what the edge is).

## Build Sequence
- Phase 0: Bootstrap (DONE — {today})
- Phase 1: (fill in)
- Phase 2: (fill in)

## Workflow
- Strict phased pipeline: Research → Spec → Implement
- Instructions in `.github/copilot-instructions.md`
- Research: `docs/research/`, Specs: `docs/specs/`
- Task tracking: `tasks/active/`
- Memory: `docs/memory/`, `memories/repo/`

## Key Modules
- Entry: (fill in)
- Config: (fill in)
- Core: (fill in)

## Current Metrics
- Test count: 0
- Phase: 0 (bootstrap)

## Active Tasks
- (none yet)
"""
    structure_path = dest / "memories" / "repo" / "project_structure.md"
    structure_path.parent.mkdir(parents=True, exist_ok=True)
    structure_path.write_text(content, encoding="utf-8")


def create_gitignore(dest: Path) -> None:
    content = """# Python
__pycache__/
*.py[cod]
*.pyo
*.pyd
.Python
*.egg-info/
dist/
build/
.eggs/
*.egg

# Virtual environments
.venv/
venv/
env/
ENV/

# Testing
.pytest_cache/
.coverage
coverage.xml
htmlcov/
.tox/

# IDEs
.vscode/settings.json
.idea/
*.swp
*.swo
.DS_Store

# Environment and secrets
.env
.env.*
!.env.example
*.pem
*.key

# Database files
*.db
*.db-shm
*.db-wal
*.sqlite

# Compiled models / large artifacts
*.pkl
*.pt
*.bin
*.h5
models/
checkpoints/

# Cache
.cache/
*.cache

# Obsidian
.obsidian/workspace
.obsidian/workspace.json
.obsidian/cache
"""
    (dest / ".gitignore").write_text(content, encoding="utf-8")


def init_git(dest: Path) -> None:
    try:
        subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=dest, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: bootstrap from agentic-os template"],
            cwd=dest,
            check=True,
            capture_output=True,
        )
        print("  Initialized git repository with initial commit")
    except subprocess.CalledProcessError as e:
        print(f"  Warning: git initialization failed: {e}")
    except FileNotFoundError:
        print("  Warning: git not found — skipping git initialization")


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap a new project from the Agentic OS template."
    )
    parser.add_argument(
        "--name", required=True, help="Project name (used in README and structure file)"
    )
    parser.add_argument(
        "--dest", required=True, help="Destination directory (will be created)"
    )
    parser.add_argument("--no-git", action="store_true", help="Skip git initialization")
    args = parser.parse_args()

    dest = Path(args.dest).expanduser().resolve()

    if dest.exists() and any(dest.iterdir()):
        print(f"ERROR: Destination already exists and is not empty: {dest}")
        print("Choose a new directory or remove the existing one.")
        sys.exit(1)

    print(f"Bootstrapping project '{args.name}' at {dest}")
    print()

    # Copy template files
    print("Copying template files...")
    copied = copy_template(TEMPLATE_ROOT, dest)
    for item in copied:
        print(f"  {item}")

    # Create project-specific files
    print("\nCreating project-specific files...")
    create_readme(dest, args.name)
    print("  README.md")
    create_structure_file(dest, args.name)
    print("  memories/repo/project_structure.md")
    create_gitignore(dest)
    print("  .gitignore")

    # Ensure required directories exist
    for d in [
        "docs/research",
        "docs/specs",
        "docs/adr",
        "docs/memory",
        "tasks/active",
        "tasks/done",
    ]:
        (dest / d).mkdir(parents=True, exist_ok=True)

    # Git init
    if not args.no_git:
        print("\nInitializing git...")
        init_git(dest)

    print(
        f"""
Bootstrap complete!

Next steps:
  1. cd {dest}
  2. Open in VS Code: code .
  3. Fill in memories/repo/project_structure.md with your project's facts
  4. Read AGENT_INDEX.md (agent cold-start map) and QUICK_START.md (human setup guide)
  5. Start your first feature (HTML-first):
       cp docs/research/RESEARCH_TEMPLATE.html docs/research/my_feature.html
       # Fill in the HTML research doc
       # Create a thin docs/research/my_feature.md stub (see AGENT_INDEX.md for stub format)
       # Then: SPEC_TEMPLATE.html → TASK_TEMPLATE.html → implement

Remember: Research → Spec → Task → Implement. Never skip the preflight.
All artifacts are .html primary + thin .md stub. See .github/copilot-instructions.md §5.7.
"""
    )


if __name__ == "__main__":
    main()
