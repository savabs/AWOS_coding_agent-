"""
resolve_file.py — Disk-first, fail-safe file resolution.

The index (repo map, symbol index, semantic retriever) is NEVER authoritative
about whether a file exists. A stale or bad index hint must not hide a file
that is actually present on disk. All hints go through this resolver, which
searches the real filesystem in increasing-cost order before giving up:

    1. exact path (as given, or relative to root)
    2. basename glob across the tree (root.rglob)
    3. case-insensitive basename matches
    4. content search (grep) for a distinctive token

Every resolution records the method used so callers can tell a confident
exact hit from a fuzzy disk search. Result path is None ONLY when the file is
genuinely absent from disk — never because an index missed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tools.filesystem import GrepTool


def resolve_path(root: str | Path, hint: str, *, byte_exact: bool = True) -> str | None:
    """Resolve *hint* to a real on-disk path.

    Returns the matching absolute path, or None only when the file (or a
    clear basename variant of it) genuinely does not exist under *root*.

    ``byte_exact`` controls whether identical basename / case-insensitive
    variants are accepted (default True — the fail-safe behavior). Callers
    that must reject fuzzy matches can pass byte_exact=False.
    """
    root_p = Path(root).expanduser().resolve()
    if not root_p.is_dir():
        return None

    hint = str(hint or "").strip()
    if not hint:
        return None

    # 1. Exact path (absolute or relative to root).
    cand = Path(hint).expanduser()
    if not cand.is_absolute():
        cand = root_p / cand
    cand = cand.resolve(strict=False)
    if cand.exists():
        return str(cand)

    # 2. Basename glob, then case-insensitive scan when allowed.
    name = Path(hint).name
    if name:
        candidates: list[tuple[int, str]] = []
        folded = name.lower()
        for p in root_p.rglob("*"):
            if not p.is_file():
                continue
            matches = p.name == name or (not byte_exact and p.name.lower() == folded)
            if matches:
                candidates.append((len(p.relative_to(root_p).parts), str(p)))
        if candidates:
            candidates.sort(key=lambda pair: (pair[0], pair[1]))
            return str(Path(candidates[0][1]).resolve())

    return None


def _search_token(hint: str) -> str | None:
    """Extract a distinctive bare-word token from a hint for grep fallback."""
    if not hint:
        return None
    stem = Path(hint).name
    # Prefer the longest identifier-like fragment, ignoring extensions.
    alt = Path(hint).stem
    for tok in (alt, stem):
        cleaned = "".join(c for c in tok if c.isalnum() or c in "._-")
        if len(cleaned) >= 3:
            return cleaned
    return None


def resolve_with_evidence(
    root: str | Path, hint: str
) -> dict[str, Any]:
    """Disk-first resolution returning {path, rel_path, method, confidence}.

    Search order: exact → case-insensitive basename → grep. Always
    terminates; ``path`` is None only if the file is truly absent on disk.
    """
    root_p = Path(root).expanduser().resolve()
    result: dict[str, Any] = {"path": None, "rel_path": None, "method": "not_found", "confidence": 0.0}

    direct = resolve_path(root_p, hint, byte_exact=True)
    if direct:
        result.update(path=direct, method="exact", confidence=1.0)
        return _finalize(root_p, result)

    fuzzy = resolve_path(root_p, hint, byte_exact=False)
    if fuzzy:
        result.update(path=fuzzy, method="fuzzy", confidence=0.8)
        return _finalize(root_p, result)

    # Last resort: grep the tree for a distinctive token from the hint.
    token = _search_token(hint)
    if token:
        grep_result = GrepTool().execute(
            {
                "pattern": token,
                "root": str(root_p),
                "file_glob": "*",
                "literal": "true",
                "max_results": 20,
            }
        )
        if grep_result.success and grep_result.data.get("hits"):
            for hit in grep_result.data["hits"]:
                rel = Path(str(hit["file"]))
                hit_path = (root_p / rel).resolve()
                if hit_path.is_file():
                    result.update(
                        path=str(hit_path),
                        method="grep",
                        confidence=0.6,
                        grep_line=int(hit.get("line", 0)),
                    )
                    break

    return _finalize(root_p, result)


def _finalize(root_p: Path, result: dict[str, Any]) -> dict[str, Any]:
    path = result.get("path")
    if path is None:
        return result
    try:
        rel = str(Path(str(path)).resolve().relative_to(root_p.resolve()))
    except ValueError:
        # Path not under root after resolution (e.g. cross-volume): keep rel.
        rel = Path(str(path)).name
    result["rel_path"] = rel
    return result


def format_resolution(evidence: dict[str, Any]) -> str:
    """Human-readable one-liner for a resolution evidence dict."""
    if not evidence.get("path"):
        return "NOT FOUND (verified absent under root)"
    return (
        f"RESOLVED to {evidence['rel_path']} "
        f"(method={evidence.get('method', '?')}, "
        f"confidence={evidence.get('confidence', 0.0):.2f})"
    )
