#!/usr/bin/env python3
"""
validate_bug_cases.py — Prove every benchmark case actually measures something.

A case is only useful if its tests fail on buggy.py and pass on fix.py. A case
that passes while still broken scores every agent as correct; one that fails
even when fixed scores every agent as wrong. Both are worse than having no case
at all, because they look like signal.

This runs each case twice, with no model involved, and reports:

  BROKEN   tests fail on buggy.py      (as they must)
  FIXABLE  tests pass on fix.py        (as they must)

Usage:
    python3 scripts/validate_bug_cases.py
    python3 scripts/validate_bug_cases.py --cases-dir tests/bug_cases --verbose

Exits non-zero if any case fails validation, so it can gate a commit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold"))

# Staging and scoring live in one place, shared with the executor benchmark.
from agent.bench_cases import load_case, run_case_tests


def _defined_names(source: str) -> set[str]:
    """
    Every identifier a module contributes, at any nesting depth.

    Walking only the top level is not enough: a dataclass field
    (`display_name: str`), an enum member (`CANCELLED = "cancelled"`) and a
    keyword argument at a call site (`parse(line, strict=False)`) are each the
    single piece of knowledge a case hinges on, and none of them is a
    module-level name.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    # Deliberately NOT ast.Attribute: that collects attribute *accesses*, so a
    # context file calling text.strip() would make any fix using .strip() look
    # context-dependent. Definitions, fields and keyword arguments are what the
    # cases actually turn on.
    return {n for n in names if n not in {"self", "cls", "__init__"}}


def check_context_is_required(case_dir: Path) -> list[str]:
    """
    A multi-file case must genuinely need its context.

    The premise of the whole benchmark is that a single-shot worker, which sees
    only buggy.py, cannot solve what an agent that can open other files can. If
    the fix never references anything defined in context/, the case does not
    test that difference and will score both executors the same.
    """
    context = case_dir / "context"
    if not context.is_dir():
        return []

    context_names: set[str] = set()
    for extra in context.iterdir():
        if extra.suffix == ".py":
            context_names |= _defined_names(extra.read_text(encoding="utf-8"))
    if not context_names:
        return ["context/ defines no importable names"]

    fix_source = (case_dir / "fix.py").read_text(encoding="utf-8")
    buggy_source = (case_dir / "buggy.py").read_text(encoding="utf-8")

    used = {n for n in context_names if n in fix_source and n not in buggy_source}
    if not used:
        return [
            "fix.py uses nothing from context/ — a single-shot worker could "
            "solve this without it, so the case does not discriminate"
        ]
    return []


def validate_case(case_dir: Path, verbose: bool = False) -> list[str]:
    """Return a list of problems with this case; empty means it is sound."""
    problems: list[str] = []

    for required in ("buggy.py", "fix.py", "test_case.py", "task.json"):
        if not (case_dir / required).exists():
            problems.append(f"missing {required}")
    if problems:
        return problems

    try:
        spec = json.loads((case_dir / "task.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"task.json is not valid JSON: {exc}"]
    for key in ("action", "file"):
        if not spec.get(key):
            problems.append(f"task.json missing '{key}'")

    if (case_dir / "buggy.py").read_text() == (case_dir / "fix.py").read_text():
        problems.append("buggy.py and fix.py are identical — nothing to fix")

    problems.extend(check_context_is_required(case_dir))

    case = load_case(case_dir)
    buggy_passed, buggy_output = run_case_tests(case)
    if buggy_passed:
        problems.append("tests PASS on buggy.py — the case does not test the bug")
    elif verbose:
        print(f"    buggy.py fails as expected")

    fixed_passed, fixed_output = run_case_tests(case, source=case.fix_source)
    if not fixed_passed:
        tail = "\n      ".join(fixed_output.strip().splitlines()[-6:])
        problems.append(f"tests FAIL on fix.py — the case is unsolvable:\n      {tail}")
    elif verbose:
        print(f"    fix.py passes as expected")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", default="tests/bug_cases")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir)
    if not cases_dir.exists():
        print(f"No such directory: {cases_dir}")
        return 1

    cases = sorted(d for d in cases_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not cases:
        print(f"No cases found in {cases_dir}")
        return 1

    print(f"Validating {len(cases)} case(s) in {cases_dir}\n")
    bad = 0
    for case_dir in cases:
        print(f"  {case_dir.name:34}", end=" ", flush=True)
        problems = validate_case(case_dir, verbose=args.verbose)
        if problems:
            bad += 1
            print("INVALID")
            for problem in problems:
                print(f"      - {problem}")
        else:
            has_context = (case_dir / "context").is_dir()
            print("ok" + ("  (multi-file)" if has_context else ""))

    print(f"\n{len(cases) - bad}/{len(cases)} valid")
    if bad:
        print(f"{bad} case(s) would produce meaningless benchmark numbers.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
