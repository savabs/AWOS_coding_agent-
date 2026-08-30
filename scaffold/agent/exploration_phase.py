"""
exploration_phase.py — Deterministic pre-plan repo exploration.

Runs grep/find before planning so the planner sees real file locations
instead of guessing paths from the goal text alone.
"""

from __future__ import annotations

import hashlib
import json
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


_SOURCE_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".php", ".py", ".rb", ".rs", ".swift", ".ts", ".tsx",
})
_CONFIG_NAMES = frozenset({
    "Cargo.toml", "Makefile", "Pipfile", "package.json", "poetry.lock",
    "pyproject.toml", "pytest.ini", "setup.cfg", "setup.py", "tox.ini",
})
_SKIP_DIRS = frozenset({
    ".git", ".awos", ".mypy_cache", ".pytest_cache", ".venv", "__pycache__",
    "build", "dist", "node_modules", "venv",
})


def normalize_goal_references(goal: str, limit: int = 12) -> dict[str, list[str]]:
    """Separate exact paths/symbols from fuzzy search keywords."""
    explicit_paths: list[str] = []
    explicit_symbols: list[str] = []
    seen_paths: set[str] = set()
    seen_symbols: set[str] = set()

    def add_path(value: str) -> None:
        value = value.strip().strip("'\"`").rstrip(".,;:)")
        if value and ("/" in value or "\\" in value or Path(value).suffix) and value not in seen_paths:
            seen_paths.add(value)
            explicit_paths.append(value)

    def add_symbol(value: str) -> None:
        value = value.strip().strip("'\"`").rstrip(".,;:()")
        for part in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", value):
            if len(part) >= 3 and part not in seen_symbols:
                seen_symbols.add(part)
                explicit_symbols.append(part)

    for match in re.finditer(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", goal):
        value = match.group(1) or match.group(2) or match.group(3) or ""
        if Path(value).suffix or "/" in value or "\\" in value:
            add_path(value)
        elif re.search(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_]", value):
            add_symbol(value)
    for value in re.findall(r"\b[\w./-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|rb|php)\b", goal):
        add_path(value)
    for value in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b", goal):
        add_symbol(value)
    for value in re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", goal):
        add_symbol(value)

    keywords = []
    for keyword in _extract_keywords(goal, limit=limit * 2):
        if keyword not in explicit_paths and keyword not in explicit_symbols:
            keywords.append(keyword)
        if len(keywords) >= limit:
            break
    return {
        "explicit_paths": explicit_paths[:limit],
        "explicit_symbols": explicit_symbols[:limit * 2],
        "keywords": keywords[:limit],
    }


def _bounded_files(root: Path, max_files: int = 40) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        files.append(relative.as_posix())
        if len(files) >= max_files:
            break
    return files


def _named_files(root: Path, names: frozenset[str], max_files: int = 40) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if path.is_file() and path.name in names and not any(part in _SKIP_DIRS for part in relative.parts):
            files.append(relative.as_posix())
            if len(files) >= max_files:
                break
    return files


def build_exploration_snapshot(
    goal: str, project_root: str, max_grep_hits: int = 30, max_files: int = 40
) -> dict[str, Any]:
    """Build a bounded, deterministic, planner-facing repository snapshot."""
    root = Path(project_root).resolve()
    references = normalize_goal_references(goal)
    base: dict[str, Any] = {
        "schema_version": "planning_exploration_v1",
        "explicit_paths": references["explicit_paths"],
        "explicit_symbols": references["explicit_symbols"],
        "keywords": references["keywords"],
        "grep_hits": [],
        "candidate_files": [],
        "hit_files": [],
        "test_files": [],
        "config_files": [],
        "negative_evidence": [],
        "bounds": {"max_grep_hits": max_grep_hits, "max_files": max_files},
        "cost": {"grep_queries": 0, "grep_hits": 0, "files_listed": 0},
    }
    if not root.is_dir():
        base["negative_evidence"] = [f"invalid project root: {root}"]
        encoded = json.dumps(base, sort_keys=True).encode("utf-8")
        base["snapshot_id"] = hashlib.sha256(encoded).hexdigest()[:16]
        return base

    grep_tool = GrepTool()
    grep_hits: list[dict[str, Any]] = []
    search_terms = references["keywords"] + references["explicit_symbols"]
    for term in search_terms:
        base["cost"]["grep_queries"] += 1
        result = grep_tool.execute({
            "pattern": re.escape(term), "root": str(root), "file_glob": "*.py",
            "literal": "true", "max_results": min(12, max_grep_hits),
        })
        if result.success:
            for hit in result.data.get("hits", []):
                grep_hits.append({**hit, "keyword": term})
                if len(grep_hits) >= max_grep_hits:
                    break
        if len(grep_hits) >= max_grep_hits:
            break

    bounded_candidates = _bounded_files(root, max_files=max_files)
    explicit_candidates = [
        path for path in references["explicit_paths"]
        if (root / path).is_file()
    ]
    candidates = list(dict.fromkeys(explicit_candidates + bounded_candidates))[:max_files]
    test_files = [path for path in candidates if Path(path).name.startswith("test_") or "/tests/" in f"/{path}"]
    fixture_files = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
        and path.suffix.lower() in {".yml", ".yaml"}
        and any(part in {"test", "tests"} for part in path.relative_to(root).parts)
        and not any(part in _SKIP_DIRS for part in path.relative_to(root).parts)
    ]
    explicit_fixture_files = [
        path for path in fixture_files
        if Path(path).stem in references["explicit_symbols"]
    ]
    test_files = list(dict.fromkeys(test_files + explicit_fixture_files + fixture_files))[:40]
    config_files = _named_files(root, _CONFIG_NAMES)
    hit_files = sorted({str(hit["file"]) for hit in grep_hits})[:20]
    negative: list[str] = []
    if not search_terms:
        negative.append("no search terms extracted from goal")
    if not grep_hits:
        negative.append("no Python grep matches; planner must use bounded tool exploration")
    if references["explicit_paths"]:
        for explicit in references["explicit_paths"]:
            if not (root / explicit).is_file():
                negative.append(f"explicit path not found: {explicit}")

    if not negative:
        negative.append("no negative findings in bounded exploration")

    base.update({
        "grep_hits": grep_hits[:max_grep_hits],
        "candidate_files": candidates,
        "hit_files": hit_files,
        "test_files": test_files,
        "config_files": config_files,
        "negative_evidence": negative,
    })
    base["cost"]["grep_hits"] = len(base["grep_hits"])
    base["cost"]["files_listed"] = len(candidates) + len(config_files)
    stable = json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
    base["snapshot_id"] = hashlib.sha256(stable).hexdigest()[:16]
    return base


def run_exploration(goal: str, project_root: str, max_grep_hits: int = 30) -> dict[str, Any]:
    """Backward-compatible text/context view over the v1 snapshot."""
    snapshot = build_exploration_snapshot(goal, project_root, max_grep_hits=max_grep_hits)
    keywords = snapshot["keywords"]
    candidate_files = snapshot["candidate_files"]
    grep_hits = snapshot["grep_hits"]
    hit_files = snapshot["hit_files"]
    lines = [
        f"Exploration snapshot: {snapshot['snapshot_id']}",
        f"Goal keywords: {', '.join(keywords) or '(none)'}",
        f"Explicit paths: {', '.join(snapshot['explicit_paths']) or '(none)'}",
        f"Explicit symbols: {', '.join(snapshot['explicit_symbols']) or '(none)'}",
        f"Source files (sample): {', '.join(candidate_files[:15])}",
    ]
    if hit_files:
        lines.append("Grep matches:")
        lines.extend(f"  {h['file']}:{h['line']}: {h['text'][:100]}" for h in grep_hits[:max_grep_hits])
    else:
        lines.append("Grep matches: (none — planner should use bounded tools during execution)")
    if snapshot["negative_evidence"]:
        lines.append("Negative evidence: " + "; ".join(snapshot["negative_evidence"]))
    return {**snapshot, "exploration_summary": "\n".join(lines)}


def enrich_codebase_context(codebase_context: dict, exploration: dict[str, Any]) -> dict:
    """Merge the structured snapshot and compatibility summary into planner context."""
    merged = dict(codebase_context or {})
    merged["exploration"] = exploration.get("exploration_summary", "")
    merged["exploration_snapshot_id"] = exploration.get("snapshot_id", "")
    for key in (
        "explicit_paths", "explicit_symbols", "keywords", "grep_hits", "hit_files",
        "test_files", "config_files", "negative_evidence", "bounds", "cost",
    ):
        if key in exploration:
            merged[f"exploration_{key}"] = exploration[key]
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
