"""
index_retrieval.py — Disk-grounded hierarchical retrieval from the symbol index.

The symbol index SUGGESTS candidate files/symbols; the disk is the authority.
Every index entry is grounded via resolve_file.resolve_with_evidence() before
it can be injected into the agent context. A stale index (moved file, renamed
symbol, deleted file) can never hide an existing file or produce a read window
pointing at nothing.

Retrieval levels:
  - map:    bounded compact 'file::symbol start-end' lines (level-1 map)
  - windows: exact [start, end] addresses backed by a real on-disk file
            (level-2 on-demand reads)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .resolve_file import resolve_with_evidence
from .symbol_index import SymbolIndex

logger = logging.getLogger(__name__)

DEFAULT_INDEX_PATH = ".awos/symbol_index.json"
DEFAULT_MAP_LIMIT = 40
DEFAULT_WINDOW_LIMIT = 20


def ensure_index(
    root: str | Path, *, index_path: str | None = None, max_files: int = 200
) -> SymbolIndex:
    """Return a fresh, disk-grounded SymbolIndex, persisted per repo root.

    Loads the persisted snapshot when present, reparses only mtime-changed
    files, else builds from scratch. Never raises on I/O failure. The index
    path defaults to ``<root>/.awos/symbol_index.json`` so different repos
    never collide.
    """
    root_p = Path(root).expanduser().resolve()
    path = index_path or str(root_p / ".awos" / "symbol_index.json")
    try:
        idx = SymbolIndex.load(path, str(root))
        changed = idx.rebuild_changed()
        logger.info("[IndexRetrieval] loaded %d files (%d changed)", len(idx._cache), changed)
        return idx
    except (FileNotFoundError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        logger.info("[IndexRetrieval] building from scratch (%s)", exc.__class__.__name__)
        idx = SymbolIndex(str(root_p))
        idx.build(max_files=max_files)
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            idx.save(path)
        except OSError:
            pass
        return idx


def retrieve(
    root: str | Path,
    *,
    index: SymbolIndex | None = None,
    primary_file: str | None = None,
    map_limit: int = DEFAULT_MAP_LIMIT,
    window_limit: int = DEFAULT_WINDOW_LIMIT,
) -> dict[str, Any]:
    """Disk-grounded retrieval: bounded map + verified read windows.

    Returns:
        {
          "symbol_map": "rel/path.py::sym 1-12\\n...",        # bounded
          "read_windows": [ {file, symbol, start, end}, ... ],# all disk-verified
          "stale_dropped": int,                              # rejected entries
        }
    """
    root_p = Path(root).expanduser().resolve()
    if index is None:
        index = ensure_index(root_p)

    # Compact map, grounding every entry against disk.
    map_lines: list[str] = []
    stale_dropped = 0
    for line in index.compact_map(limit=map_limit).splitlines():
        rel_hint = line.split("::", 1)[0]
        if not rel_hint.strip():
            continue
        evidence = resolve_with_evidence(str(root_p), rel_hint)
        if not evidence.get("path"):
            stale_dropped += 1
            continue
        map_lines.append(line)
    symbol_map = "\n".join(map_lines)

    # Read windows for primary file + a few related files, all verified.
    windows: list[dict[str, Any]] = []
    seen_windows: set[tuple[str, int, int]] = set()
    for hint in _candidate_hints(primary_file, root_p, index):
        evidence = resolve_with_evidence(str(root_p), hint)
        if not evidence.get("path"):
            stale_dropped += 1
            continue
        actual = str(evidence["path"])
        for window in index.symbol_windows(actual, limit=window_limit):
            target = Path(window["file"])
            if not target.is_absolute():
                target = root_p / target
            target = target.resolve()
            if not target.is_file():
                stale_dropped += 1
                continue
            rel = str(target.relative_to(root_p))
            key = (rel, int(window["start"]), int(window["end"]))
            if key in seen_windows:
                continue
            seen_windows.add(key)
            windows.append(
                {
                    "file": rel,
                    "symbol": window["symbol"],
                    "start": int(window["start"]),
                    "end": int(window["end"]),
                }
            )
            if len(windows) >= window_limit:
                break
        if len(windows) >= window_limit:
            break

    return {
        "symbol_map": symbol_map,
        "read_windows": windows,
        "stale_dropped": stale_dropped,
    }


def _candidate_hints(
    primary_file: str | None, root_p: Path, index: SymbolIndex
) -> list[str]:
    """Candidate file hints: primary + first few indexed files."""
    hints: list[str] = []
    if primary_file:
        hints.append(str(primary_file))
    for fp in sorted(index._cache):
        p = Path(fp)
        if not p.is_absolute():
            p = root_p / p
        if str(p) not in hints:
            hints.append(str(p))
        if len(hints) >= 4:
            break
    return hints
