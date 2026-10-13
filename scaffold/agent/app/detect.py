"""detect.py — guess a repo's test command for the app header (display only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "build", "dist"}


def _has_python_tests(repo: Path, limit: int = 4000) -> bool:
    seen = 0
    for path in repo.rglob("*.py"):
        if any(part in _SKIP for part in path.relative_to(repo).parts):
            continue
        seen += 1
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            return True
        if seen > limit:
            break
    return False


def detect_test_command(repo: Path) -> Optional[str]:
    repo = Path(repo)
    try:
        pyproject = repo / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8") if pyproject.exists() else ""
        if "[tool.pytest" in text or (repo / "pytest.ini").exists() or _has_python_tests(repo):
            return "python -m pytest"
        pkg = repo / "package.json"
        if pkg.exists():
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
            if "test" in scripts:
                return "npm test"
        if (repo / "Cargo.toml").exists():
            return "cargo test"
        if (repo / "go.mod").exists():
            return "go test ./..."
        mk = repo / "Makefile"
        if mk.exists() and "\ntest:" in "\n" + mk.read_text(encoding="utf-8", errors="ignore"):
            return "make test"
    except (OSError, ValueError):
        return None
    return None
