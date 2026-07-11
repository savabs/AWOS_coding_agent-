"""
repo_map.py — Deterministic repo map for ReAct workers.

Builds line-numbered anchors (grep + structural scan) so the agent can
read_file with start_line/end_line instead of blind sequential scrolling.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .exploration_phase import _extract_keywords, run_exploration
from .project_spatial import build_directory_tree
from .tools.filesystem import GrepTool

_SKIP_SCAN_DIRS = frozenset({
    ".git", ".awos", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "dist", "build", ".cursor",
})

_STRUCTURAL_PATTERNS = (
    (re.compile(r"^\s*def\s+(\w+)"), "def"),
    (re.compile(r"^\s*class\s+(\w+)"), "class"),
    (re.compile(r"add_argument\s*\("), "argparse"),
    (re.compile(r"sub\.add_parser\s*\("), "subparser"),
    (re.compile(r"if\s+getattr\s*\(\s*args"), "cli_flag"),
)


def repo_map_enabled() -> bool:
    return os.getenv("AWOS_REPO_MAP", "1").lower() in ("1", "true", "yes")


def _extract_search_terms(goal: str, limit: int = 12) -> list[str]:
    """Keywords plus quoted/backticked symbols and *.py paths from the goal."""
    terms: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        t = raw.strip().strip("\"'`")
        if not t or len(t) < 2 or t in seen:
            return
        seen.add(t)
        terms.append(t)

    for m in re.finditer(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", goal):
        _add(m.group(1) or m.group(2) or m.group(3))
    for m in re.finditer(r"\b[\w/]+\.py\b", goal):
        _add(m.group(0))
    for kw in _extract_keywords(goal, limit=limit):
        _add(kw)
    return terms[:limit]


def _scan_file_anchors(path: Path, root: Path, max_lines: int = 25) -> list[dict[str, Any]]:
    """Structural anchors inside one file (defs, classes, argparse)."""
    if not path.is_file():
        return []
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = str(path)
    anchors: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for lineno, line in enumerate(lines, 1):
        for regex, kind in _STRUCTURAL_PATTERNS:
            if regex.search(line):
                anchors.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "kind": kind,
                        "text": line.rstrip()[:120],
                    }
                )
                break
    return anchors[:max_lines]


def _grep_term(root: Path, term: str, file_glob: str, max_results: int) -> list[dict[str, Any]]:
    tool = GrepTool()
    result = tool.execute(
        {
            "pattern": term,
            "root": str(root),
            "file_glob": file_glob,
            "literal": "true",
            "max_results": max_results,
        }
    )
    if not result.success:
        return []
    return list(result.data.get("hits") or [])


def _term_match_score(text: str, terms: list[str]) -> int:
    """Prefer longer / more specific term matches in a line."""
    text_l = text.lower()
    return max((len(t) for t in terms if t.lower() in text_l), default=0)


def build_repo_map(
    goal: str,
    project_root: str | Path,
    *,
    primary_file: str | None = None,
    max_grep_hits: int = 35,
    max_anchors: int = 20,
) -> dict[str, Any]:
    """
    Build a repo map: directory tree + grep hits + structural anchors.

    Returns dict with summary (prompt text), grep_hits, anchors, terms.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        return {
            "summary": f"(invalid project root: {root})",
            "grep_hits": [],
            "anchors": [],
            "terms": [],
        }

    terms = _extract_search_terms(goal)
    grep_hits: list[dict[str, Any]] = []
    seen_hits: set[tuple[str, int]] = set()
    for term in terms:
        for hit in _grep_term(root, term, "*.py", max_results=10):
            key = (hit["file"], hit["line"])
            if key in seen_hits:
                continue
            seen_hits.add(key)
            grep_hits.append({**hit, "term": term})
            if len(grep_hits) >= max_grep_hits:
                break
        if len(grep_hits) >= max_grep_hits:
            break

    anchors: list[dict[str, Any]] = []
    pf_name = Path(primary_file).name if primary_file else ""
    if primary_file:
        pf = Path(primary_file)
        if not pf.is_absolute():
            pf = root / pf
        anchors.extend(_scan_file_anchors(pf, root, max_lines=max_anchors))
        # Targeted grep inside primary file for argparse / subparser lines
        tool = GrepTool()
        for sym in ("def cmd_stats", "stats.add_argument", 'add_argument("--savings"', "sub.add_parser(\"stats\""):
            gr = tool.execute(
                {
                    "pattern": sym,
                    "root": str(root),
                    "path": str(pf.relative_to(root)),
                    "literal": "true",
                    "max_results": 5,
                }
            )
            if gr.success:
                for hit in gr.data.get("hits") or []:
                    key = (hit["file"], hit["line"])
                    if key not in seen_hits:
                        seen_hits.add(key)
                        grep_hits.append({**hit, "term": sym})

    # Extra anchors from top hit files (up to 2 files)
    hit_files: list[Path] = []
    for h in grep_hits:
        fp = root / h["file"]
        if fp.is_file() and fp not in hit_files:
            hit_files.append(fp)
        if len(hit_files) >= 2:
            break
    for fp in hit_files:
        if primary_file and fp.name == Path(primary_file).name:
            continue
        for a in _scan_file_anchors(fp, root, max_lines=12):
            if a not in anchors:
                anchors.append(a)
            if len(anchors) >= max_anchors:
                break

    tree = build_directory_tree(root, max_depth=2, max_lines=40)
    lines = [
        "REPO MAP (use these line numbers with read_file start_line/end_line — do NOT scroll blindly):",
        "",
        "Directory tree:",
        tree,
        "",
        f"Search terms: {', '.join(terms) or '(none)'}",
    ]

    if grep_hits:
        lines.append("")
        lines.append("Grep hits (file:line):")
        for h in grep_hits[:max_grep_hits]:
            lines.append(f"  {h['file']}:{h['line']}: {h['text'][:100]}")
    else:
        lines.append("")
        lines.append("Grep hits: (none)")

    if anchors:
        lines.append("")
        lines.append(f"Structural anchors{f' in {primary_file}' if primary_file else ''}:")
        for a in anchors[:max_anchors]:
            lines.append(f"  {a['file']}:{a['line']} [{a['kind']}]: {a['text'][:90]}")

    if primary_file:
        pf_name = Path(primary_file).name
        pf_hits = [h for h in grep_hits if h["file"] == primary_file or h["file"].endswith(pf_name)]
        pf_anchors = [a for a in anchors if a["file"] == primary_file or a["file"].endswith(pf_name)]
        def_hits = [
            h for h in pf_hits if h.get("text", "").lstrip().startswith("def ")
        ]
        if def_hits:
            best = max(_term_match_score(h.get("text", ""), terms) for h in def_hits)
            line_pool = [
                h["line"] for h in def_hits if _term_match_score(h.get("text", ""), terms) == best
            ]
        else:
            line_pool = []
        if not line_pool:
            line_pool = [
                a["line"]
                for a in pf_anchors
                if a.get("kind") in ("def", "subparser", "argparse", "cli_flag")
                and any(t.lower() in a.get("text", "").lower() for t in terms)
            ]
        if not line_pool:
            line_pool = [
                a["line"]
                for a in pf_anchors
                if a.get("kind") in ("def", "subparser", "argparse", "cli_flag")
            ]
        if not line_pool and pf_hits:
            line_pool = [h["line"] for h in pf_hits]
        if line_pool:
            lo = max(1, min(line_pool) - 5)
            hi = max(line_pool) + 20
            arg_hits = [h for h in pf_hits if "add_argument" in h.get("text", "")]
            if arg_hits:
                best_arg = max(_term_match_score(h.get("text", ""), terms) for h in arg_hits)
                arg_lines = [
                    h["line"]
                    for h in arg_hits
                    if _term_match_score(h.get("text", ""), terms) == best_arg and best_arg > 0
                ]
            else:
                arg_lines = []
            if arg_lines:
                hi = max(hi, max(arg_lines) + 5)
            lines.append("")
            lines.append(
                f"Suggested read_file window for {primary_file}: start_line={lo}, end_line={hi}"
            )
            if arg_lines and min(arg_lines) > hi:
                lines.append(
                    f"Also read argparse section: start_line={max(1, min(arg_lines) - 3)}, "
                    f"end_line={max(arg_lines) + 5}"
                )

    summary = "\n".join(lines)
    return {
        "summary": summary,
        "grep_hits": grep_hits[:max_grep_hits],
        "anchors": anchors[:max_anchors],
        "terms": terms,
        "directory_tree": tree,
    }


def enrich_with_repo_map(
    goal: str,
    project_root: str | Path,
    codebase_context: dict | None,
    *,
    primary_file: str | None = None,
) -> dict[str, Any]:
    """Merge repo map + optional exploration into codebase_context."""
    merged = dict(codebase_context or {})
    if repo_map_enabled():
        repo_map = build_repo_map(goal, project_root, primary_file=primary_file)
        merged["repo_map"] = repo_map.get("summary", "")
        merged["repo_map_hits"] = repo_map.get("grep_hits", [])
        merged["repo_map_anchors"] = repo_map.get("anchors", [])
    if os.getenv("AWOS_EXPLORATION_PHASE", "1").lower() in ("1", "true", "yes"):
        exploration = run_exploration(goal, str(project_root))
        merged = {**merged, **{k: v for k, v in exploration.items() if k != "exploration_summary"}}
        # Prefer repo_map as primary navigation; append exploration if distinct
        expl = exploration.get("exploration_summary", "")
        if expl and not merged.get("exploration"):
            merged["exploration"] = expl
    return merged
