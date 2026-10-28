"""
exploration_phase.py — Deterministic pre-plan repo exploration.

Runs grep/find before planning so the planner sees real file locations
instead of guessing paths from the goal text alone.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .tools.filesystem import FindFilesTool, GrepTool


_STOPWORDS = frozenset(
    """
    a an the and or but in on at to for of with from by as is are was were be been
    being have has had do does did will would could should may might must can
    add create update fix implement write build make use into through using
    this that these those it its they them their we you your all any each
    file files function class module code repo project
    """.split()
)


def _extract_keywords(goal: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", goal)
    seen: set[str] = set()
    keywords: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        keywords.append(tok)
        if len(keywords) >= limit:
            break
    return keywords


def run_exploration(goal: str, project_root: str, max_grep_hits: int = 30) -> dict[str, Any]:
    """
    Explore the repo before planning.

    Returns dict with exploration_summary (str) plus structured hits for planner context.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        return {"exploration_summary": f"(invalid project root: {root})", "keywords": [], "grep_hits": []}

    keywords = _extract_keywords(goal)
    grep_tool = GrepTool()
    find_tool = FindFilesTool()

    grep_hits: list[dict[str, Any]] = []
    for kw in keywords:
        result = grep_tool.execute(
            {
                "pattern": re.escape(kw),
                "root": str(root),
                "file_glob": "*.py",
                "literal": "true",
                "max_results": 12,
            }
        )
        if result.success and result.data.get("hits"):
            for hit in result.data["hits"]:
                grep_hits.append({**hit, "keyword": kw})
        if len(grep_hits) >= max_grep_hits:
            break

    py_files = find_tool.execute(
        {"pattern": "**/*.py", "root": str(root), "max_results": 40}
    )
    candidate_files = py_files.data.get("matches", []) if py_files.success else []

    # Deduplicate files mentioned in grep hits
    hit_files = sorted({h["file"] for h in grep_hits})
    lines = [
        f"Goal keywords: {', '.join(keywords) or '(none)'}",
        f"Python files (sample): {', '.join(candidate_files[:15])}",
    ]
    if hit_files:
        lines.append("Grep matches:")
        for h in grep_hits[:max_grep_hits]:
            lines.append(f"  {h['file']}:{h['line']}: {h['text'][:100]}")
    else:
        lines.append("Grep matches: (none — planner should use find_files/grep during execution)")

    summary = "\n".join(lines)
    return {
        "exploration_summary": summary,
        "keywords": keywords,
        "grep_hits": grep_hits[:max_grep_hits],
        "candidate_files": candidate_files[:40],
        "hit_files": hit_files[:20],
    }


def enrich_codebase_context(codebase_context: dict, exploration: dict[str, Any]) -> dict:
    """Merge exploration results into planner context."""
    merged = dict(codebase_context or {})
    merged["exploration"] = exploration.get("exploration_summary", "")
    merged["exploration_files"] = exploration.get("hit_files", [])
    if exploration.get("candidate_files"):
        existing = list(merged.get("files") or [])
        for f in exploration["candidate_files"]:
            if f not in existing:
                existing.append(f)
        merged["files"] = existing[:50]
    return merged


def exploration_enabled() -> bool:
    import os

    return os.getenv("AWOS_EXPLORATION_PHASE", "1").lower() in ("1", "true", "yes")
