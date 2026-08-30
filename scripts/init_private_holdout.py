#!/usr/bin/env python3
"""Create a local-only AWOS holdout workspace without private data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / ".awos" / "holdout_private"

TEMPLATE = {
    "schema_version": "holdout_corpus_v1",
    "corpus_id": "REPLACE_WITH_PRIVATE_CORPUS_ID",
    "environment_identity": "REPLACE_WITH_RECORDED_ENVIRONMENT_ID",
    "split": "holdout",
    "tasks": [],
    "baseline": {
        "artifact_reference": "REPLACE_WITH_IMMUTABLE_BASELINE_REFERENCE",
        "report_sha256": "0" * 64,
    },
    "candidate": {
        "artifact_reference": "REPLACE_WITH_IMMUTABLE_CANDIDATE_REFERENCE",
        "report_sha256": "0" * 64,
    },
}

SUBDIRECTORIES = (
    "tasks",
    "repositories",
    "reports/baseline",
    "reports/candidate",
    "comparisons",
)


def _write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def initialize(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    if root == REPO or REPO in root.parents and "tests" in root.parts:
        raise ValueError("private holdout root is inside an unsafe repository fixture path")
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    created: list[Path] = []
    for relative in SUBDIRECTORIES:
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    template_path = root / "manifest.template.json"
    if _write_if_missing(template_path, json.dumps(TEMPLATE, indent=2, sort_keys=True) + "\n"):
        created.append(template_path)
    readme = root / "README.md"
    readme_text = """# AWOS private holdout workspace

This directory is local-only and ignored by Git. It contains no private tasks by default.

- `tasks/`: operator-supplied private task fixtures
- `repositories/`: operator-supplied repository snapshots
- `reports/baseline/`: immutable baseline report bundles
- `reports/candidate/`: immutable candidate report bundles
- `comparisons/`: outputs from `scripts/verify_holdout_comparison.py`
- `manifest.template.json`: metadata-only starting point; replace placeholders before use

Git ignore is not encryption. Use an access-controlled filesystem or volume for sensitive data.
"""
    if _write_if_missing(readme, readme_text):
        created.append(readme)
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a local AWOS private holdout workspace")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    try:
        created = initialize(args.root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"PRIVATE_HOLDOUT_ROOT {args.root.expanduser().resolve()}")
    print(f"CREATED_FILES {len(created)}")
    for path in created:
        print(f"CREATED {path}")
    print("No private tasks, repositories, credentials, or provider calls were created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
