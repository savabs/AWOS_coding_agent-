#!/usr/bin/env python3
"""
extract_patterns.py — Mine completed tasks and checkpoints for reusable patterns.

Produces a draft pattern extraction report at docs/memory/extracted_patterns_YYYY-MM.md.
Patterns that meet the Reviewed Memory threshold (≥3 appearances, ≥2 runs, ≥80% consistent)
are candidates for promotion to AWOS.md.

Usage:
    python scripts/extract_patterns.py
    python scripts/extract_patterns.py --since 2026-01-01  # only look at files after this date
"""

import argparse
import datetime
import re
from collections import defaultdict
from pathlib import Path


def get_project_root() -> Path:
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "AWOS.md").exists():
            return parent
    return here


# Keywords that often signal a reusable pattern or lesson
PATTERN_KEYWORDS = re.compile(
    r"""
    (?:
        # Decision patterns
        decided?\s+to|
        chose\s+|
        use\s+X\s+instead|
        switched\s+to|
        replaced\s+with|

        # Lesson patterns
        lesson:|
        learned:|
        discovered\s+that|
        turned\s+out\s+|
        root\s+cause:|
        fixed\s+by|
        resolved\s+by|

        # Pattern patterns
        pattern:|
        rule:|
        always\s+|
        never\s+|
        prefer\s+|
        avoid\s+|

        # Bug patterns (in regression comments)
        regression:|
        bug\s+was|
        the\s+issue\s+was|
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def extract_decisions_from_file(content: str, source_file: str) -> list[dict]:
    """Extract decision records from task files and checkpoints."""
    decisions = []

    # Look for decision tables in task files
    # Format: | Decision | Rationale | Date |
    table_pattern = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|",
        re.MULTILINE,
    )
    for match in table_pattern.finditer(content):
        decision, rationale, date = match.groups()
        if not decision.strip().lower().startswith("decision"):  # skip header
            decisions.append(
                {
                    "type": "decision",
                    "content": f"{decision.strip()}: {rationale.strip()}",
                    "date": date.strip(),
                    "source": source_file,
                }
            )

    # Look for bullet points with pattern keywords
    for i, line in enumerate(content.split("\n")):
        if PATTERN_KEYWORDS.search(line) and len(line.strip()) > 20:
            decisions.append(
                {
                    "type": "pattern",
                    "content": line.strip().lstrip("-").lstrip("*").strip(),
                    "date": "unknown",
                    "source": source_file,
                }
            )

    return decisions


def collect_patterns_from_done_tasks(
    root: Path, since: datetime.date | None
) -> list[dict]:
    done_dir = root / "tasks" / "done"
    if not done_dir.exists():
        return []

    patterns = []
    for f in sorted(done_dir.glob("*.md")):
        if since:
            mtime = datetime.date.fromtimestamp(f.stat().st_mtime)
            if mtime < since:
                continue
        content = f.read_text(encoding="utf-8", errors="replace")
        patterns.extend(extract_decisions_from_file(content, str(f.name)))

    return patterns


def collect_patterns_from_checkpoints(
    root: Path, since: datetime.date | None
) -> list[dict]:
    memory_dir = root / "docs" / "memory"
    if not memory_dir.exists():
        return []

    patterns = []
    for f in sorted(memory_dir.rglob("checkpoint_*.md")):
        if "TEMPLATE" in f.name:
            continue
        if since:
            mtime = datetime.date.fromtimestamp(f.stat().st_mtime)
            if mtime < since:
                continue
        content = f.read_text(encoding="utf-8", errors="replace")
        patterns.extend(extract_decisions_from_file(content, str(f.name)))

    return patterns


def cluster_patterns(patterns: list[dict]) -> dict[str, list[dict]]:
    """Simple keyword clustering of patterns."""
    clusters: dict[str, list[dict]] = defaultdict(list)

    for p in patterns:
        content_lower = p["content"].lower()
        # Assign to cluster based on keywords
        if any(w in content_lower for w in ["test", "pytest", "coverage", "assertion"]):
            clusters["Testing"].append(p)
        elif any(
            w in content_lower
            for w in ["debug", "bug", "fix", "issue", "error", "exception"]
        ):
            clusters["Debugging"].append(p)
        elif any(
            w in content_lower
            for w in ["performance", "speed", "slow", "memory", "latency"]
        ):
            clusters["Performance"].append(p)
        elif any(
            w in content_lower
            for w in ["api", "endpoint", "request", "response", "rate limit"]
        ):
            clusters["API / External Services"].append(p)
        elif any(
            w in content_lower for w in ["schema", "database", "db", "sql", "migration"]
        ):
            clusters["Data / Schema"].append(p)
        elif any(
            w in content_lower
            for w in ["architecture", "design", "pattern", "module", "layer"]
        ):
            clusters["Architecture"].append(p)
        elif any(
            w in content_lower for w in ["agent", "llm", "prompt", "context", "session"]
        ):
            clusters["Agent / LLM"].append(p)
        else:
            clusters["Other"].append(p)

    return dict(clusters)


def main():
    parser = argparse.ArgumentParser(
        description="Extract patterns from completed tasks and checkpoints."
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Only look at files modified after this date (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    root = get_project_root()
    since = datetime.date.fromisoformat(args.since) if args.since else None

    print("Extracting patterns from completed tasks...")
    task_patterns = collect_patterns_from_done_tasks(root, since)

    print("Extracting patterns from checkpoints...")
    checkpoint_patterns = collect_patterns_from_checkpoints(root, since)

    all_patterns = task_patterns + checkpoint_patterns
    print(f"Found {len(all_patterns)} raw pattern candidates")

    clusters = cluster_patterns(all_patterns)

    # Generate report
    today = datetime.date.today()
    month_str = today.strftime("%Y-%m")
    output_path = root / "docs" / "memory" / f"extracted_patterns_{month_str}.md"

    lines = [
        "---",
        f'title: "Extracted Patterns {month_str}"',
        "tags:",
        "  - doc/memory",
        "---",
        "",
        f"# Extracted Patterns — {month_str}",
        "",
        "> **Draft — review before promoting to AWOS.md**",
        "> Promotion criteria: appears ≥3 times, across ≥2 separate runs, ≥80% consistent.",
        "> Patterns below this threshold should stay here as session notes only.",
        "",
        f"Extracted from {len(task_patterns)} done-task entries and {len(checkpoint_patterns)} checkpoint entries.",
        f"Total raw candidates: {len(all_patterns)}",
        "",
        "---",
        "",
    ]

    for cluster_name, items in sorted(clusters.items()):
        if not items:
            continue
        lines.append(f"## {cluster_name} ({len(items)} candidates)")
        lines.append("")
        for item in items[:20]:  # cap at 20 per cluster to avoid noise
            lines.append(f"- **[{item['type']}]** {item['content']}")
            lines.append(f"  *Source: {item['source']} | Date: {item['date']}*")
        if len(items) > 20:
            lines.append(f"  *... and {len(items) - 20} more*")
        lines.append("")

    lines += [
        "---",
        "",
        "## Promotion Candidates",
        "",
        "> Fill in manually: patterns that appear ≥3 times with consistent outcome.",
        "",
        "| Pattern | Appearances | Consistent? | Promote to |",
        "|---|---|---|---|",
        "| (fill in) | N | Yes/No | AWOS.md §X |",
        "",
        "## Related",
        "",
        "- [[project_structure]] — canonical project facts",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nPattern report written to: {output_path.relative_to(root)}")
    print("Review the report and manually identify promotion candidates.")
    print(
        "Patterns meeting the threshold (≥3x, ≥2 runs, ≥80% consistent) belong in AWOS.md."
    )


if __name__ == "__main__":
    main()
