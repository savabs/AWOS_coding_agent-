#!/usr/bin/env python3
"""
wedge_v1_assert.py — post-run semantic gate for Test Rescue wedge.

Spec: docs/specs/wedge_v1_test_rescue_spec.md

Usage:
    python3 scripts/wedge_v1_assert.py \\
        --root tests/fixtures/wedge_v1/payment_utils \\
        --assertions tests/fixtures/wedge_v1/payment_utils/wedge_goal.json

Exit 0 + prints WEDGE_ASSERT markers on semantic_pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _load_assertions(path: Path) -> dict:
    data = json.loads(path.read_text())
    return data.get("goal_assertions", data)


def _run(cmd: str, cwd: Path) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode, out.strip()


def _is_forbidden_edit(path: str, forbidden: list[str]) -> bool:
    """True if path is a forbidden source edit (ignore pytest __pycache__ artifacts)."""
    if "__pycache__" in path or path.endswith(".pyc"):
        return False
    for prefix in forbidden:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def _diff_paths(root: Path, forbidden: list[str]) -> list[str]:
    """Return repo-relative paths with changes that violate forbidden_paths."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    return [p for p in paths if _is_forbidden_edit(p, forbidden)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Wedge v1 Test Rescue assertions")
    parser.add_argument("--root", required=True, help="Repo or worktree root")
    parser.add_argument("--assertions", required=True, help="wedge_goal.json path")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    assertions = _load_assertions(Path(args.assertions).resolve())

    must_pass: list[str] = assertions.get("must_pass", [])
    must_still_pass: str = assertions.get("must_still_pass", "")
    forbidden: list[str] = assertions.get("forbidden_paths", [])

    ok = True

    passed = 0
    for node_id in must_pass:
        cmd = f"python3 -m pytest {node_id} -q --tb=line"
        code, out = _run(cmd, root)
        if code == 0:
            passed += 1
            print(f"WEDGE_ASSERT: must_pass OK {node_id}")
        else:
            ok = False
            print(f"WEDGE_ASSERT: must_pass FAIL {node_id}")
            if out:
                print(out[:500])

    print(f"WEDGE_ASSERT: must_pass OK ({passed}/{len(must_pass)})")

    if must_still_pass:
        code, out = _run(must_still_pass, root)
        if code == 0:
            print("WEDGE_ASSERT: must_still_pass OK")
        else:
            ok = False
            print("WEDGE_ASSERT: must_still_pass FAIL")
            if out:
                print(out[:800])

    if forbidden:
        violations = _diff_paths(root, forbidden)
        if violations:
            ok = False
            print(f"WEDGE_ASSERT: forbidden_paths VIOLATION {violations}")
        else:
            print("WEDGE_ASSERT: forbidden_paths clean")

    if ok:
        print("WEDGE_ASSERT: SEMANTIC_PASS")
        return 0

    print("WEDGE_ASSERT: SEMANTIC_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
