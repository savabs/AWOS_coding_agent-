"""Focused test discovery — find the smallest test scope for a changed file."""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple


class TestScope(NamedTuple):
    direct: list[str]       # tests/test_<module>.py
    impacted: list[str]     # tests importing symbols from this module
    full_suite: str = "tests/"


def _module_name(file_path: str) -> str:
    """Derive a Python module name from a source path."""
    path = Path(file_path)
    stem = path.stem
    # Walk up to find package root (where no __init__.py exists upward)
    parts = [stem]
    parent = path.parent
    while (parent / "__init__.py").exists() or (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    return ".".join(parts)


def _extract_public_symbols(source: str) -> set[str]:
    """Extract top-level public function and class names from source."""
    symbols: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            symbols.add(node.name)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            symbols.add(node.name)
    return symbols


def _find_imports_of(file_path: str, scope: str = "tests") -> list[str]:
    """Find test files that import from the given source file."""
    module = _module_name(file_path)
    test_dir = Path(scope)
    if not test_dir.is_dir():
        return []
    results: list[str] = []
    patterns = [
        re.compile(rf"from\s+{re.escape(module)}\s+import"),
        re.compile(rf"import\s+{re.escape(module)}"),
        rf"from {module} import",
        rf"import {module}",
    ]
    for test_file in sorted(test_dir.rglob("test_*.py")):
        try:
            content = test_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if any(
            (isinstance(p, re.Pattern) and p.search(content)) or
            (isinstance(p, str) and p in content)
            for p in patterns
        ):
            results.append(str(test_file))
    return results


def _find_direct_test(file_path: str) -> str | None:
    """Find the direct test file for a source module: tests/test_<stem>.py."""
    stem = Path(file_path).stem
    # Normalize: strip _impl, _core, _utils suffixes
    for suffix in ("_impl", "_core", "_utils", "_lib"):
        if stem.endswith(suffix):
            base = stem[: -len(suffix)]
            candidate = Path("tests") / f"test_{base}.py"
            if candidate.is_file():
                return str(candidate)
    direct = Path("tests") / f"test_{stem}.py"
    return str(direct) if direct.is_file() else None


def focused_test_scope(file_path: str) -> TestScope:
    """Return the recommended test scope for a changed source file."""
    direct_test = _find_direct_test(file_path)
    direct = [direct_test] if direct_test else []
    impacted = _find_imports_of(file_path)
    # Remove direct test from impacted if present
    if direct_test and direct_test in impacted:
        impacted.remove(direct_test)
    return TestScope(direct=direct, impacted=impacted)


def cmd_focused_test(file_path: str, *, fast: bool = False) -> str:
    """Return the shell command(s) to run the focused test scope."""
    scope = focused_test_scope(file_path)
    parts: list[str] = []
    if scope.direct:
        parts.append(f"python3 -m pytest {' '.join(scope.direct)} -q" + (" -x" if fast else ""))
    if scope.impacted:
        cmds = " ".join(scope.impacted)
        parts.append(f"python3 -m pytest {cmds} -q" + (" -x" if fast else ""))
    if not parts:
        parts.append(f"python3 -m pytest tests/ -q --ignore=tests/fixtures" + (" -x" if fast else ""))
    return " && ".join(parts)


def _as_safe_path(p: str) -> str:
    """Return a relative path that resolves inside the repo."""
    resolved = Path(p).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/focused_test.py <source_file> [--fast] [--json]", file=sys.stderr)
        sys.exit(1)
    file_path = _as_safe_path(sys.argv[1])
    fast = "--fast" in sys.argv
    as_json = "--json" in sys.argv
    scope = focused_test_scope(file_path)
    if as_json:
        import json
        print(json.dumps({
            "file": file_path,
            "direct": scope.direct,
            "impacted": scope.impacted,
            "command": cmd_focused_test(file_path, fast=fast),
        }, indent=2))
    else:
        print(f"Direct tests:   {scope.direct or '(none found)'}")
        print(f"Impacted tests: {scope.impacted or '(none found)'}")
        print(f"\nCommand:\n  {cmd_focused_test(file_path, fast=fast)}")


if __name__ == "__main__":
    main()
