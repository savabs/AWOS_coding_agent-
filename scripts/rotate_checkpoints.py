#!/usr/bin/env python3
"""
rotate_checkpoints.py — Archive old checkpoints when count exceeds threshold.

Moves checkpoints older than the last N (default: 15) to docs/memory/archive/YYYY/.
This keeps the hot path clean for cold-start navigation without losing history.

Usage:
    python scripts/rotate_checkpoints.py           # keep last 15
    python scripts/rotate_checkpoints.py --keep 20
    python scripts/rotate_checkpoints.py --dry-run  # show what would be moved
"""

import argparse
import shutil
from pathlib import Path


def get_project_root() -> Path:
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "AWOS.md").exists():
            return parent
    return here


def main():
    parser = argparse.ArgumentParser(description="Archive old session checkpoints.")
    parser.add_argument(
        "--keep",
        type=int,
        default=15,
        help="Number of recent checkpoints to keep (default: 15)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be moved without moving"
    )
    args = parser.parse_args()

    root = get_project_root()
    memory_dir = root / "docs" / "memory"

    if not memory_dir.exists():
        print("docs/memory/ does not exist — nothing to rotate")
        return

    checkpoints = sorted(
        [f for f in memory_dir.glob("checkpoint_*.md") if "TEMPLATE" not in f.name],
        key=lambda f: f.stat().st_mtime,
        reverse=True,  # newest first
    )

    to_keep = checkpoints[: args.keep]
    to_archive = checkpoints[args.keep :]

    if not to_archive:
        print(
            f"Only {len(checkpoints)} checkpoint(s) — nothing to archive (threshold: {args.keep})"
        )
        return

    print(f"Keeping {len(to_keep)} recent checkpoints, archiving {len(to_archive)}")

    for checkpoint in to_archive:
        # Determine archive year from filename (checkpoint_YYYY-MM-DD*.md)
        name = checkpoint.name
        year = "unknown"
        if name.startswith("checkpoint_") and len(name) > 21:
            try:
                year = name[len("checkpoint_") : len("checkpoint_") + 4]
                int(year)  # validate it's a number
            except (ValueError, IndexError):
                year = "unknown"

        archive_dir = memory_dir / "archive" / year
        dest = archive_dir / name

        if args.dry_run:
            print(f"  [DRY RUN] Would move: {name} → archive/{year}/{name}")
            continue

        archive_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            # Avoid overwriting — add suffix
            suffix = 2
            while dest.exists():
                dest = archive_dir / f"{checkpoint.stem}_{suffix}.md"
                suffix += 1

        shutil.move(str(checkpoint), str(dest))
        print(f"  Archived: {name} → archive/{year}/{name}")

    if not args.dry_run:
        print(f"\nDone. {len(to_keep)} checkpoints remain in docs/memory/")
        print(
            "Archived checkpoints are in docs/memory/archive/ — history is preserved."
        )


if __name__ == "__main__":
    main()
