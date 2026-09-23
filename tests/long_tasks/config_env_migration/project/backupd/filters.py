"""Which files under source_dir go into a backup."""

import fnmatch
import os
from pathlib import Path
from typing import Iterator, List

ALWAYS_EXCLUDED = (".last_run",)


def excluded_patterns(cfg) -> List[str]:
    """Glob patterns from the ``exclude`` setting."""
    raw = cfg.parser["backup"].get("exclude", "") if cfg.parser.has_section("backup") else ""
    patterns = [p.strip() for p in raw.split(",") if p.strip()]
    return patterns + list(ALWAYS_EXCLUDED)


def is_excluded(cfg, relpath, patterns=None) -> bool:
    """True if *relpath* (relative to source_dir) matches an exclude pattern.

    A pattern matches either the whole relative path or any single component,
    so ``node_modules`` excludes that directory wherever it appears.
    """
    patterns = excluded_patterns(cfg) if patterns is None else patterns
    rel = Path(relpath).as_posix()
    parts = rel.split("/")
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def iter_files(cfg) -> Iterator[Path]:
    """Yield paths (relative to source_dir) of files to back up, sorted."""
    root = Path(cfg.source_dir)
    patterns = excluded_patterns(cfg)
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [
            d for d in dirnames if not is_excluded(cfg, rel_dir / d, patterns)
        ]
        for name in filenames:
            rel = rel_dir / name
            if not is_excluded(cfg, rel, patterns):
                collected.append(rel)
    return iter(sorted(collected))
