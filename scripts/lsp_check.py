"""LSP diagnostic integration for AWOS coding sessions.

Checks that LSP tooling is available and provides structured diagnostic output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _find_executable(*names: str) -> str | None:
    for name in names:
        result = subprocess.run(["which", name], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def lsp_status() -> dict:
    """Check which LSP tools are available for this project."""
    python_lsps = {
        "pyright": _find_executable("pyright", "pyright-langserver"),
        "basedpyright": _find_executable("basedpyright", "basedpyright-langserver"),
        "ruff": _find_executable("ruff"),
        "mypy": _find_executable("mypy"),
        "pylsp": _find_executable("pylsp"),
    }
    available = {k: v for k, v in python_lsps.items() if v}
    return {
        "project_root": str(Path.cwd()),
        "python": sys.executable,
        "python_version": sys.version,
        "language_servers": available,
        "recommendation": _recommend(available),
    }


def _recommend(available: dict) -> str:
    if "basedpyright" in available:
        return "basedpyright (fastest, best for large repos)"
    if "pyright" in available:
        return "pyright (good, enable via `hermes lsp` if supported)"
    if "ruff" in available:
        return "ruff (linter only; add pyright or basedpyright for type checking)"
    return "Install pyright or basedpyright for the best LSP experience"


def run_diagnostics(file_path: str) -> dict:
    """Run available diagnostic tools on a single file."""
    path = Path(file_path)
    if not path.is_file():
        return {"error": f"File not found: {file_path}"}

    results: dict = {"file": str(path), "diagnostics": {}}

    # ruff check
    ruff = _find_executable("ruff")
    if ruff:
        proc = subprocess.run(
            [ruff, "check", str(path), "--output-format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        try:
            results["diagnostics"]["ruff"] = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            results["diagnostics"]["ruff"] = [{"error": proc.stderr[:500]}]

    # py_compile (always available)
    try:
        with open(path) as f:
            compile(f.read(), str(path), "exec")
        results["diagnostics"]["py_compile"] = "ok"
    except SyntaxError as exc:
        results["diagnostics"]["py_compile"] = {
            "error": str(exc),
            "line": exc.lineno,
            "offset": exc.offset,
        }

    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Check LSP tool availability")
    parser.add_argument("--check", metavar="FILE", help="Run diagnostics on a file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.status:
        result = lsp_status()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("LSP Status:")
            print(f"  Python: {result['python']} ({result['python_version'].split()[0]})")
            print(f"  Language servers available:")
            for name, exe in result["language_servers"].items():
                print(f"    {name}: {exe}")
            print(f"  Recommendation: {result['recommendation']}")

    elif args.check:
        result = run_diagnostics(args.check)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Diagnostics for: {result['file']}")
            for tool, diag in result.get("diagnostics", {}).items():
                if diag == "ok":
                    print(f"  {tool}: OK")
                elif isinstance(diag, list):
                    print(f"  {tool}: {len(diag)} issue(s)")
                    for d in diag[:5]:
                        print(f"    {d.get('message', str(d))[:120]}")
                elif isinstance(diag, dict):
                    print(f"  {tool}: ERROR at line {diag.get('line', '?')} — {diag.get('error', '')[:120]}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
