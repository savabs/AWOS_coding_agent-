"""
project_spatial.py — Repository layout context for planner path decisions.

Builds a compact directory tree + artifact placement conventions so the LLM
planner can choose paths that match project structure (not repo root dumps).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_SKIP_DIR_NAMES = frozenset({
    ".git", ".awos", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "dist", "build", ".cursor",
})
_SKIP_FILE_PREFIXES = (".",)
_ARTIFACT_EXT = {".md", ".html", ".htm", ".rst", ".txt"}

# Paths that must never receive new doc deliverables
_FORBIDDEN_CREATE_TARGETS = frozenset({
    "run_awos_demo.py",
    "awos.py",
    "setup.py",
    "pyproject.toml",
})

_ARTIFACT_KIND_BY_FOLDER: dict[str, str] = {
    "docs/research": "research",
    "docs/specs": "spec",
    "tasks/active": "task",
    "docs/memory": "checkpoint",
    "docs/adr": "adr",
    "wiki": "wiki",
    "docs": "guide",
    "tests": "test",
    "scaffold/agent": "code",
}

# Fallback when AGENT_INDEX.md is missing (minimal — planner still decides slug)
_DEFAULT_PLACEMENT = """\
ARTIFACT PLACEMENT (follow these — never write new deliverables at repo root):
- Research notes     → docs/research/<feature>.html (+ thin .md stub)
- Specs              → docs/specs/<feature>_spec.html (+ stub)
- Task checklists    → tasks/active/<task>.html (+ stub)
- Session checkpoints→ docs/memory/checkpoint_YYYY-MM-DD.html (+ stub)
- ADRs               → docs/adr/NNNN-<slug>.html (+ stub)
- Wiki pages         → wiki/<slug>.html (+ stub)
- Guides / tutorials → docs/<topic>.md or docs/<topic>.html
- Python source      → scaffold/agent/ or existing package layout
- Tests              → tests/test_<module>.py
- NEVER create new artifacts as bare README.md or foo.md at repository root.
"""

_AGENT_INDEX_CANDIDATES = (
    "AGENT_INDEX.md",
    "docs/AGENT_INDEX.md",
    ".awos/PLACEMENT.md",
)


def build_directory_tree(
    root: Path,
    *,
    max_depth: int = 3,
    max_lines: int = 80,
) -> str:
    """Compact ASCII tree of top-level project folders (depth-limited)."""
    lines: list[str] = []
    root = root.resolve()

    def _walk(path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth or len(lines) >= max_lines:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        dirs = [
            e for e in entries
            if e.is_dir() and e.name not in _SKIP_DIR_NAMES and not e.name.startswith(".")
        ]
        for i, d in enumerate(dirs):
            if len(lines) >= max_lines:
                return
            connector = "└── " if i == len(dirs) - 1 else "├── "
            lines.append(f"{prefix}{connector}{d.name}/")
            extension = "    " if i == len(dirs) - 1 else "│   "
            _walk(d, prefix + extension, depth + 1)

    lines.append(f"{root.name}/")
    _walk(root, "", 1)
    return "\n".join(lines[:max_lines])


def load_placement_conventions(root: Path) -> str:
    """Load artifact placement rules from AGENT_INDEX.md or defaults."""
    for rel in _AGENT_INDEX_CANDIDATES:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel.endswith("PLACEMENT.md"):
            return text.strip()[:4000]
        extracted = _extract_agent_index_placement(text)
        if extracted:
            return extracted
    return _DEFAULT_PLACEMENT.strip()


def _extract_agent_index_placement(text: str) -> str:
    """Pull placement-relevant sections from AGENT_INDEX.md."""
    chunks: list[str] = []

    # Research → Spec → Task table block
    m = re.search(
        r"(### Research → Spec → Task Triad.*?)(?=\n### |\n---|\Z)",
        text,
        re.DOTALL,
    )
    if m:
        chunks.append(m.group(1).strip())

    m2 = re.search(
        r"(## HTML-First Artifacts Rule.*?)(?=\n## |\n---|\Z)",
        text,
        re.DOTALL,
    )
    if m2:
        chunks.append(m2.group(1).strip())

    m3 = re.search(
        r"(## Artifact Creation Quick Reference.*?)(?=\n## |\n---|\Z)",
        text,
        re.DOTALL,
    )
    if m3:
        chunks.append(m3.group(1).strip()[:1500])

    if not chunks:
        return ""
    return "\n\n".join(chunks)[:4500]


def sample_files_by_folder(
    rel_paths: list[str],
    *,
    max_per_folder: int = 3,
    max_folders: int = 12,
) -> dict[str, list[str]]:
    """Group relative paths by top-level folder for neighbor examples."""
    buckets: dict[str, list[str]] = {}
    for rel in rel_paths:
        parts = Path(rel).parts
        key = parts[0] if len(parts) > 1 else "(root)"
        buckets.setdefault(key, [])
        if len(buckets[key]) < max_per_folder:
            buckets[key].append(rel)
    # Prefer folders that look like artifact homes
    priority = ("docs", "tasks", "wiki", "tests", "scaffold", "memories", "protocols")
    ordered: dict[str, list[str]] = {}
    for p in priority:
        if p in buckets:
            ordered[p] = buckets[p]
    for k, v in sorted(buckets.items()):
        if k not in ordered:
            ordered[k] = v
    return dict(list(ordered.items())[:max_folders])


def format_folder_samples(samples: dict[str, list[str]]) -> str:
    if not samples:
        return "(no indexed files yet)"
    lines = []
    for folder, files in samples.items():
        lines.append(f"  {folder}/ → {', '.join(files)}")
    return "\n".join(lines)


def build_spatial_context(codebase_root: str | Path) -> dict[str, Any]:
    """Full spatial bundle merged into codebase_context for planners."""
    root = Path(codebase_root).resolve()
    doc_files: list[str] = []
    doc_ext = {".md", ".html", ".htm", ".json", ".yaml", ".yml", ".txt", ".rst", ".xml"}

    for doc in root.rglob("*"):
        if not doc.is_file():
            continue
        rel = str(doc.relative_to(root)).replace("\\", "/")
        if any(skip in rel for skip in ("__pycache__", ".git/", "node_modules/", ".awos/")):
            continue
        if doc.suffix.lower() in doc_ext:
            doc_files.append(rel)

    doc_files = sorted(doc_files)[:80]
    samples = sample_files_by_folder(doc_files)

    artifact_roots: list[str] = []
    for candidate in (
        "docs/research", "docs/specs", "docs/memory", "docs/adr",
        "tasks/active", "tasks/done", "wiki", "docs", "tests", "scaffold/agent",
    ):
        if (root / candidate).is_dir():
            artifact_roots.append(candidate + "/")

    return {
        "directory_tree": build_directory_tree(root),
        "placement_conventions": load_placement_conventions(root),
        "artifact_roots": artifact_roots,
        "doc_samples_by_folder": samples,
    }


def format_spatial_prompt_block(codebase_context: dict[str, Any]) -> str:
    """Render spatial context for injection into planner prompts."""
    tree = codebase_context.get("directory_tree", "")
    placement = codebase_context.get("placement_conventions", "")
    roots = codebase_context.get("artifact_roots", [])
    samples = codebase_context.get("doc_samples_by_folder", {})

    parts = [
        "PROJECT LAYOUT (directory tree — use this to pick paths):",
        tree or "(empty)",
        "",
        "EXISTING ARTIFACT FOLDERS:",
        ", ".join(roots) if roots else "(none detected)",
        "",
        "EXAMPLES BY FOLDER (match these patterns for new files):",
        format_folder_samples(samples),
        "",
        "PLACEMENT CONVENTIONS:",
        placement or _DEFAULT_PLACEMENT.strip(),
    ]
    return "\n".join(parts)


def infer_artifact_folder(goal: str, action: str = "") -> str | None:
    """Suggest target folder from goal/action keywords (fallback for bad planner paths)."""
    text = f"{goal} {action}".lower()
    if re.search(r"\bresearch\b|\binvestigat|\bexplore\b|\bstudy\b", text):
        return "docs/research"
    if re.search(r"\bspec\b|\bspecification\b", text):
        return "docs/specs"
    if re.search(r"\btask file\b|\bchecklist\b|\batomic step", text):
        return "tasks/active"
    if re.search(r"\bcheckpoint\b|\bsession summary\b", text):
        return "docs/memory"
    if re.search(r"\badr\b|architecture decision", text):
        return "docs/adr"
    if re.search(r"\bwiki\b", text):
        return "wiki"
    if re.search(r"\bguide\b|\btutorial\b|\bhow to\b|\bhow-to\b", text):
        return "docs"
    if re.search(r"\btest\b|\bpytest\b", text):
        return "tests"
    if re.search(r"\bmodule\b|\bimplement\b.*\b(python|class|function)\b", text):
        return "scaffold/agent"
    return None


def classify_artifact_kind(goal: str, action: str = "") -> str:
    folder = infer_artifact_folder(goal, action)
    if folder and folder in _ARTIFACT_KIND_BY_FOLDER:
        return _ARTIFACT_KIND_BY_FOLDER[folder]
    return "general"


def path_under_folder(path: str, folder: str) -> bool:
    rel = path.strip().lstrip("./").replace("\\", "/")
    folder = folder.strip("/")
    return rel == folder or rel.startswith(folder + "/")


@dataclass
class PlacementValidation:
    ok: bool
    path: str
    artifact_kind: str = "general"
    expected_folder: str | None = None
    issues: list[str] = field(default_factory=list)
    score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_create_placement(
    path: str,
    goal: str,
    action: str = "",
    codebase_root: str | Path = ".",
) -> PlacementValidation:
    """Check whether a create_file path matches project placement conventions."""
    rel = (path or "").strip().lstrip("./").replace("\\", "/")
    kind = classify_artifact_kind(goal, action)
    expected = infer_artifact_folder(goal, action)
    issues: list[str] = []

    if not rel:
        issues.append("empty path")
    elif is_root_level_path(rel):
        issues.append("deliverable at repository root — use docs/, tasks/, or wiki/")
    elif rel in _FORBIDDEN_CREATE_TARGETS:
        issues.append(f"forbidden target: {rel} (not a deliverable location)")

    suffix = Path(rel).suffix.lower()
    if suffix in _ARTIFACT_EXT and rel in _FORBIDDEN_CREATE_TARGETS:
        issues.append(f"refusing to write {suffix} to {rel}")

    if expected and rel:
        if expected == "docs":
            if not path_under_folder(rel, "docs"):
                issues.append(f"guide/doc expected under docs/, got {rel}")
        elif not path_under_folder(rel, expected):
            issues.append(f"{kind} expected under {expected}/, got {rel}")

    root = Path(codebase_root)
    if expected and rel and not (root / expected).is_dir():
        # Convention folder missing — warn but don't fail hard
        issues.append(f"expected folder missing: {expected}/")

    ok = not any(
        i for i in issues
        if not i.startswith("expected folder missing")
    )
    score = 1.0 if ok else 0.0
    return PlacementValidation(
        ok=ok,
        path=rel,
        artifact_kind=kind,
        expected_folder=expected,
        issues=issues,
        score=score,
    )


def is_root_level_path(path: str) -> bool:
    """True if path is a single filename at repository root (no subdirs)."""
    p = Path(path.strip().lstrip("./"))
    return len(p.parts) == 1


def fix_create_file_placement(
    task: dict[str, Any],
    goal: str,
    codebase_root: str | Path,
) -> dict[str, Any]:
    """
    Relocate create_file tasks that landed at repo root when a convention folder exists.
    Only adjusts bare filenames — does not override explicit nested paths.
    """
    from scaffold.agent.plan_actions import CREATE_FILE, task_path

    out = dict(task)
    tt = (out.get("task_type") or "").strip()
    if tt and tt != CREATE_FILE:
        return out

    path = task_path(out)
    if not path or not is_root_level_path(path):
        return out

    root = Path(codebase_root)
    folder = infer_artifact_folder(goal, out.get("action", ""))
    name = Path(path).name

    candidates: list[str] = []
    if folder and (root / folder).is_dir():
        candidates.append(f"{folder}/{name}")
    elif (root / "docs").is_dir() and Path(name).suffix.lower() in _ARTIFACT_EXT:
        candidates.append(f"docs/{name}")

    for new_path in candidates:
        if not (root / new_path).exists() or _allows_overwrite(goal):
            out["path"] = new_path
            out["file"] = new_path
            out["_placement_fixed"] = True
            out["_placement_from"] = path
            break

    return out


def _allows_overwrite(goal: str) -> bool:
    return bool(re.search(r"\b(overwrite|replace|update)\b", goal or "", re.I))


def fix_wrong_folder_placement(
    task: dict[str, Any],
    goal: str,
    codebase_root: str | Path,
) -> dict[str, Any]:
    """Relocate create_file paths that are in the wrong subfolder (e.g. research in docs/)."""
    from scaffold.agent.plan_actions import CREATE_FILE, task_path

    out = dict(task)
    tt = (out.get("task_type") or "").strip()
    if tt and tt != CREATE_FILE:
        return out

    path = task_path(out)
    if not path or is_root_level_path(path):
        return out

    expected = infer_artifact_folder(goal, out.get("action", ""))
    if not expected or expected == "docs":
        return out  # docs/ is broad — only root fix applies

    if path_under_folder(path, expected):
        return out

    root = Path(codebase_root)
    if not (root / expected).is_dir():
        return out

    new_path = f"{expected}/{Path(path).name}"
    if (root / new_path).exists() and not _allows_overwrite(goal):
        return out

    out["path"] = new_path
    out["file"] = new_path
    out["_placement_fixed"] = True
    out["_placement_from"] = path
    return out


def prepare_plan_placements(
    tasks: list[dict[str, Any]],
    goal: str,
    codebase_root: str | Path,
) -> list[dict[str, Any]]:
    """Fix + validate placement for all create_file tasks in a plan."""
    prepared: list[dict[str, Any]] = []
    for raw in tasks:
        task = fix_create_file_placement(dict(raw), goal, codebase_root)
        task = fix_wrong_folder_placement(task, goal, codebase_root)
        tt = (task.get("task_type") or "").strip()
        if tt == "create_file":
            from scaffold.agent.plan_actions import task_path as _tp
            pv = validate_create_placement(
                _tp(task), goal, task.get("action", ""), codebase_root,
            )
            task["_placement_validation"] = pv.to_dict()
            task["artifact_kind"] = pv.artifact_kind
            task["placement_ok"] = pv.ok
        prepared.append(task)
    return prepared


def fix_plan_placements(
    tasks: list[dict[str, Any]],
    goal: str,
    codebase_root: str | Path,
) -> list[dict[str, Any]]:
    return prepare_plan_placements(tasks, goal, codebase_root)
