"""Which files under source_dir go into a backup."""

import fnmatch
import os
from pathlib import Path
from typing import Iterator, List

ALWAYS_EXCLUDED = (".last_run",)


def excluded_patterns(cfg) -> List[str]:
    """Glob patterns from the ``exclude`` setting."""
    return list(cfg.exclude) + list(ALWAYS_EXCLUDED)


def _matches_dir_pattern(dir_parts, pattern) -> bool:
    """A ``name/`` pattern: matches a directory (by name or by path from the root)."""
    pattern = pattern.rstrip("/")
    for i, part in enumerate(dir_parts):
        if fnmatch.fnmatch(part, pattern):
            return True
        if fnmatch.fnmatch("/".join(dir_parts[: i + 1]), pattern):
            return True
    return False


def is_excluded(cfg, relpath, patterns=None, is_dir=False) -> bool:
    """True if *relpath* (relative to source_dir) matches an exclude pattern.

    A pattern matches either the whole relative path or any single component,
    so ``node_modules`` excludes that directory wherever it appears.  A pattern
    ending in ``/`` (``build/``) only matches directories: everything under a
    directory of that name is excluded, a file of that name is not.
    """
    patterns = excluded_patterns(cfg) if patterns is None else patterns
    rel = Path(relpath).as_posix()
    parts = rel.split("/")
    dir_parts = parts if is_dir else parts[:-1]
    for pattern in patterns:
        if pattern.endswith("/"):
            if _matches_dir_pattern(dir_parts, pattern):
                return True
            continue
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
            d for d in dirnames if not is_excluded(cfg, rel_dir / d, patterns, is_dir=True)
        ]
        for name in filenames:
            rel = rel_dir / name
            if not is_excluded(cfg, rel, patterns):
                collected.append(rel)
    return iter(sorted(collected))
