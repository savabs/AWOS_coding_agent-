"""Symbol-level codebase retrieval — find callers, callees, and tests for a symbol."""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _walk_python_files(root: str | Path) -> list[Path]:
    root_path = Path(root)
    files: list[Path] = []
    source_files: list[Path] = []
    for path in sorted(root_path.rglob("*.py")):
        if any(part in path.parts for part in (".awos", "worktrees", "__pycache__")):
            continue
        if "tests/fixtures" in str(path) or "awos-app/dist" in str(path):
            continue
        if "scaffold/agent" in str(path):
            source_files.append(path)
        else:
            files.append(path)
    return source_files + files


def build_symbol_index(root: str | Path = ".") -> dict[str, dict[str, Any]]:
    """Build an AST-level symbol index: {fully_qualified_name: {file, line, kind, references}}."""
    root_path = Path(root).resolve()
    index: dict[str, dict[str, Any]] = {}
    # Phase 1: collect definitions
    for path in _walk_python_files(root_path):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module = _module_name(path, root_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                fqn = f"{module}.{node.name}"
                index[fqn] = {
                    "file": str(path.relative_to(root_path)),
                    "line": node.lineno,
                    "kind": "function",
                    "calls": [],
                    "called_by": [],
                    "test_file": None,
                }
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                fqn = f"{module}.{node.name}"
                index[fqn] = {
                    "file": str(path.relative_to(root_path)),
                    "line": node.lineno,
                    "kind": "class",
                    "calls": [],
                    "called_by": [],
                    "test_file": None,
                }

    # Phase 2: find calls and cross-references
    for path in _walk_python_files(root_path):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module = _module_name(path, root_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name and name in index:
                    index[name]["called_by"].append({
                        "file": str(path.relative_to(root_path)),
                        "line": node.lineno,
                        "module": module,
                    })

    # Phase 3: find test files
    test_dir = root_path / "tests"
    if test_dir.is_dir():
        for path in sorted(test_dir.rglob("test_*.py")):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for fqn in index:
                simple = fqn.split(".")[-1]
                if simple in content or fqn in content:
                    index[fqn]["test_file"] = str(path.relative_to(root_path))

    return index


def _module_name(path: Path, root: Path) -> str:
    """Derive a Python module name from a file path relative to root."""
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _call_name(node: ast.Call) -> str | None:
    """Extract a fully qualified call name from an AST Call node."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = []
        current: Any = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        else:
            return None
        return ".".join(reversed(parts))
    return None


def query_symbol(symbol_index: dict, name: str) -> dict | None:
    """Look up a symbol by fully qualified name or simple name."""
    if name in symbol_index:
        return symbol_index[name]
    # Try simple-name match
    matches = {k: v for k, v in symbol_index.items() if k.endswith(f".{name}")}
    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        # Return the first match with a note
        entry = next(iter(matches.values()))
        entry["_ambiguous"] = list(matches.keys())
        return entry
    return None


def _as_safe_path(p: str) -> str:
    resolved = Path(p).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def main() -> None:
    root = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else Path.cwd()
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/symbol_retrieval.py <symbol_name> [root_dir] [--build] [--json]", file=sys.stderr)
        sys.exit(1)

    name = sys.argv[1]
    as_json = "--json" in sys.argv

    index = build_symbol_index(root)
    result = query_symbol(index, name)

    if result is None:
        print(f"Symbol not found: {name}")
        sys.exit(1)

    if as_json:
        import json
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Symbol:     {name}")
        print(f"File:       {result['file']}:{result['line']}")
        print(f"Kind:       {result['kind']}")
        print(f"Test file:  {result.get('test_file') or '(none found)'}")
        if result.get("called_by"):
            print(f"Called by:")
            for ref in result["called_by"][:10]:
                print(f"  {ref['file']}:{ref['line']}")
            if len(result["called_by"]) > 10:
                print(f"  ... ({len(result['called_by']) - 10} more)")
        if result.get("_ambiguous"):
            print(f"\nAmbiguous — matched multiple symbols:")
            for fqn in result["_ambiguous"]:
                print(f"  {fqn}")


if __name__ == "__main__":
    main()
