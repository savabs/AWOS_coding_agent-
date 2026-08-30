#!/usr/bin/env python3
"""Run the offline private holdout comparison workflow.

This command validates local workspace boundaries and delegates all evidence
verification to verify_holdout_comparison.py. It never runs an agent/provider.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / ".awos" / "holdout_private"
VERIFY_SCRIPT = REPO / "scripts" / "verify_holdout_comparison.py"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline AWOS private holdout comparison pipeline"
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.expanduser().resolve()
    manifest = (args.manifest or root / "manifest.json").expanduser().resolve()
    baseline = (args.baseline or root / "reports" / "baseline" / "baseline.json").expanduser().resolve()
    candidate = (args.candidate or root / "reports" / "candidate" / "candidate.json").expanduser().resolve()
    output = (args.output or root / "comparisons" / "comparison.json").expanduser().resolve()

    paths = {"manifest": manifest, "baseline": baseline, "candidate": candidate, "output": output}
    if not root.is_dir():
        print(f"ERROR: private holdout root does not exist: {root}", file=sys.stderr)
        return 1
    for label, path in paths.items():
        if not _inside(path, root):
            print(f"ERROR: {label} path must remain inside private holdout root: {path}", file=sys.stderr)
            return 1
    if not manifest.is_file() or not baseline.is_file() or not candidate.is_file():
        missing = [label for label, path in paths.items() if label != "output" and not path.is_file()]
        print(f"ERROR: missing private holdout artifact(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    command = [
        sys.executable,
        str(VERIFY_SCRIPT),
        "--manifest", str(manifest),
        "--baseline", str(baseline),
        "--candidate", str(candidate),
        "--output", str(output),
    ]
    try:
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    except OSError as exc:
        print(f"ERROR: unable to start comparison verifier: {exc}", file=sys.stderr)
        return 1
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
