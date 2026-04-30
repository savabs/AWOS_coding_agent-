#!/usr/bin/env python3
"""
fact_lint.py — Detect numeric constants and key facts duplicated outside their canonical owner.

Motivation (Single-Owner Rule):
  Each important fact lives in exactly one canonical file.
  When the same number appears in 5 places, they eventually diverge.
  This script catches drift before it becomes invisible.

Canonical owner map (default — override with --config):
  memories/repo/project_structure.md  →  all metrics, counts, dimensions
  tasks/active/*.md                   →  phase ordering, roadmap
  docs/adr/*.md                       →  architecture decisions

Findings:
  FL01  Numeric constant found in non-canonical file (error)
  FL02  Constant copied but consistent across files (advisory)
  FL03  Constant has different values in different files (error — drift detected)

Usage:
    python scripts/fact_lint.py
    python scripts/fact_lint.py --strict     # enforce FL02
    python scripts/fact_lint.py --config fact_lint_config.yaml
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


def get_project_root() -> Path:
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "AWOS.md").exists():
            return parent
    return here


# Patterns for numbers that are likely project metrics (not dates, not version numbers)
# Matches standalone integers like "9676", "41", "29" when preceded/followed by non-numeric context
METRIC_PATTERN = re.compile(
    r"""
    (?:
        # Number followed by context words (common metric patterns)
        (\d{2,6})             # the number (2-6 digits to avoid single digits and huge numbers)
        \s*
        (?:tests?|nodes?|tools?|entities|entities|observations?|files?|steps?|arms?|
           dims?|dimensions?|features?|layers?|passes?|passing|fail(?:ing|ed)?|
           operators?|types?)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Pairs that are clearly version-like or date-like (skip these)
SKIP_PATTERNS = re.compile(r"\d{4}-\d{2}-\d{2}|v\d+\.\d+|\d+\.\d+\.\d+")


def collect_files(root: Path) -> list[Path]:
    return [
        f
        for f in root.rglob("*.md")
        if ".git" not in f.parts
        and "node_modules" not in f.parts
        and "TEMPLATE" not in f.name
    ]


def extract_metrics(content: str, file_path: Path) -> dict[str, list[int]]:
    """Extract (context, value) pairs from a file."""
    metrics: dict[str, list[int]] = defaultdict(list)
    for line in content.split("\n"):
        if SKIP_PATTERNS.search(line):
            continue
        for match in METRIC_PATTERN.finditer(line):
            value = int(match.group(1))
            # Normalize the context (the word after the number)
            full_match = match.group(0).lower()
            context_word = (
                re.sub(r"\d+\s*", "", full_match).strip().rstrip("s")
            )  # singularize
            key = context_word
            metrics[key].append(value)
    return dict(metrics)


def load_canonical_owners(root: Path) -> dict[str, str]:
    """Returns {file_relative_path: description} for canonical owner files."""
    owners = {}

    structure_file = root / "memories" / "repo" / "project_structure.md"
    if structure_file.exists():
        owners[str(structure_file.relative_to(root))] = (
            "project metrics (counts, dimensions, phases)"
        )

    for adr_file in (root / "docs" / "adr").glob("*.md"):
        owners[str(adr_file.relative_to(root))] = "architecture decision"

    return owners


def main():
    parser = argparse.ArgumentParser(
        description="Detect fact drift across markdown files."
    )
    parser.add_argument(
        "--strict", action="store_true", help="Enforce FL02 (consistent copies)"
    )
    args = parser.parse_args()

    root = get_project_root()
    all_files = collect_files(root)
    canonical_owners = load_canonical_owners(root)

    # For each file, extract all metrics
    file_metrics: dict[str, dict[str, list[int]]] = {}
    for f in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        rel = str(f.relative_to(root))
        metrics = extract_metrics(content, f)
        if metrics:
            file_metrics[rel] = metrics

    # Find metrics that appear in multiple files
    # metric_key → {file: [values]}
    global_metrics: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for file_rel, metrics in file_metrics.items():
        for key, values in metrics.items():
            for val in values:
                global_metrics[key][file_rel].append(val)

    findings = []
    for metric_key, file_map in global_metrics.items():
        if len(file_map) < 2:
            continue  # Only in one file — no drift possible

        # Check for value consistency
        all_values = {v for values in file_map.values() for v in values}

        # Find if any canonical owner has this metric
        canonical_files = [f for f in file_map if f in canonical_owners]
        non_canonical_files = [f for f in file_map if f not in canonical_owners]

        if len(all_values) > 1:
            # FL03: different values in different files — drift detected
            details = [f"  {f}: {file_map[f]}" for f in sorted(file_map)]
            findings.append(
                {
                    "code": "FL03",
                    "key": metric_key,
                    "msg": f"Drift: '{metric_key}' has different values across files",
                    "details": details,
                }
            )
        elif canonical_files and non_canonical_files:
            # FL01: constant exists in non-canonical file (but same value)
            for f in non_canonical_files:
                findings.append(
                    {
                        "code": "FL01",
                        "key": metric_key,
                        "msg": f"'{metric_key}' copied from canonical owner to non-canonical file: {f}",
                        "details": [
                            f"  canonical: {canonical_files[0]}",
                            f"  copy: {f}",
                        ],
                    }
                )
        elif len(file_map) >= 2 and args.strict:
            # FL02: consistent copies but no known canonical owner (advisory under --strict)
            details = [f"  {f}: {file_map[f]}" for f in sorted(file_map)]
            findings.append(
                {
                    "code": "FL02",
                    "key": metric_key,
                    "msg": f"'{metric_key}' appears in {len(file_map)} files (consistent; no canonical owner declared)",
                    "details": details,
                }
            )

    # Report
    errors = [f for f in findings if f["code"] in ("FL01", "FL03")]
    warnings = [f for f in findings if f["code"] == "FL02"]

    if errors:
        print(f"\n=== ERRORS ({len(errors)}) — fix before committing ===")
        for f in errors:
            print(f"\n  [{f['code']}] {f['msg']}")
            for d in f["details"]:
                print(d)

    if warnings:
        print(f"\n=== WARNINGS ({len(warnings)}) — advisory ===")
        for f in warnings:
            print(f"\n  [{f['code']}] {f['msg']}")
            for d in f["details"]:
                print(d)

    if not findings:
        print("Fact lint: clean — no duplicate constants detected")

    print(f"\nScanned {len(all_files)} files, {len(file_metrics)} contained metrics")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
