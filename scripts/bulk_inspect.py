"""Bulk repository inspection tool for execute_code workflows.

Generates targeted repo-introspection scripts that can be run via execute_code
to collapse many tool calls into a single LLM turn.
"""
from __future__ import annotations

import sys
from pathlib import Path


INSPECT_TEMPLATES: dict[str, str] = {
    "symbols": '''"""Find all public symbols matching a pattern in the project."""
from hermes_tools import search_files
import json

pattern = {pattern!r}
matches = search_files(pattern, path={path!r}, file_glob="*.py", limit={limit})
results = []
for match in matches.get("matches", []):
    results.append({{"file": match["path"], "line": match.get("line", "?"), "text": match.get("match", "")[:200]}})
print(json.dumps(results, indent=2))
''',
    "imports": '''"""Find all files that import a given module."""
from hermes_tools import search_files
import json

module = {module!r}
matches = search_files(f"from {module} import|import {module}", path={path!r}, file_glob="*.py", limit={limit})
results = []
for match in matches.get("matches", []):
    results.append({{"file": match["path"], "line": match.get("line", "?"), "text": match.get("match", "")[:200]}})
print(json.dumps(results, indent=2))
''',
    "classes": '''"""Find all class definitions in a directory."""
from hermes_tools import search_files
import json

matches = search_files(r"^class \\w+", path={path!r}, file_glob="*.py", limit={limit})
results = []
for match in matches.get("matches", []):
    results.append({{"file": match["path"], "line": match.get("line", "?"), "name": match.get("match", "")[:120]}})
print(json.dumps(results, indent=2))
''',
    "test_coverage": '''"""Find source files with and without corresponding test files."""
from hermes_tools import search_files
from pathlib import Path
import json

# Find all Python source files
src_files = search_files("", path={src_path!r}, file_glob="*.py", target="files", limit=200)
test_dir = Path({test_dir!r})
results = {{"with_tests": [], "without_tests": []}}
for src in src_files.get("matches", []):
    src_path = src["path"]
    stem = Path(src_path).stem
    test_candidate = test_dir / f"test_{{stem}}.py"
    results["with_tests" if test_candidate.is_file() else "without_tests"].append(src_path)
print(json.dumps(results, indent=2))
''',
    "changed_files": '''"""List all files changed relative to a git reference."""
from hermes_tools import terminal
import json

result = terminal("git diff --name-only {ref}", timeout=30)
files = [f.strip() for f in result.get("output", "").split("\\n") if f.strip()]
print(json.dumps({{"ref": {ref!r}, "changed": files, "count": len(files)}}, indent=2))
''',
}


def generate_inspect_script(
    template_name: str,
    *,
    pattern: str = "",
    module: str = "",
    path: str = "scaffold/agent",
    src_path: str = "scaffold/agent",
    test_dir: str = "tests",
    limit: int = 50,
    ref: str = "HEAD~1",
) -> str:
    """Generate a self-contained execute_code script for repo inspection."""
    if template_name not in INSPECT_TEMPLATES:
        raise ValueError(f"Unknown template: {template_name}. Choose from: {', '.join(INSPECT_TEMPLATES)}")
    return INSPECT_TEMPLATES[template_name].format(
        pattern=pattern, module=module, path=path,
        src_path=src_path, test_dir=test_dir, limit=limit, ref=ref,
    )


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", choices=list(INSPECT_TEMPLATES), help="Inspection template")
    parser.add_argument("--pattern", default="", help="Search pattern (for symbols template)")
    parser.add_argument("--module", default="", help="Module name (for imports template)")
    parser.add_argument("--path", default="scaffold/agent", help="Directory to search")
    parser.add_argument("--src-path", default="scaffold/agent", help="Source directory (for test_coverage)")
    parser.add_argument("--test-dir", default="tests", help="Test directory (for test_coverage)")
    parser.add_argument("--limit", type=int, default=50, help="Max results")
    parser.add_argument("--ref", default="HEAD~1", help="Git reference (for changed_files)")
    args = parser.parse_args()

    script = generate_inspect_script(
        args.template,
        pattern=args.pattern,
        module=args.module,
        path=args.path,
        src_path=args.src_path,
        test_dir=args.test_dir,
        limit=args.limit,
        ref=args.ref,
    )
    print(script)


if __name__ == "__main__":
    main()
