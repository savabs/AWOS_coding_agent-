"""
one_shot.py — localize → repair → validate in one model call (ablation 3).

The agent loop spends ~20 turns exploring, editing one hunk at a time and
checking, re-sending ~7–10k tokens each turn. On the same jobs and model a
fixed pass (Agentless, Aider) solves many tasks for a fraction of that: put
the relevant files in front of the model, ask for every change as
SEARCH/REPLACE blocks, apply them, run the tests. The orchestrator tries this
first when AWOS_ONE_SHOT is on and falls back to the agent loop — with these
edits kept — when the tests do not pass.

  build_context(project_root, task, exploration, budget_tokens)
      repo map + whole candidate files, ranked by relevance, within a budget
  parse_blocks(reply) / apply_blocks(project_root, blocks)
      Aider-format SEARCH/REPLACE, applied through EditFileTool (exact match,
      then Verifier's fuzzy matcher and syntax check)
  run_one_shot(client, model, project_root, task, exploration, ...)
      one utility_chat call (role "one_shot") + apply; never raises

Env:
  AWOS_ONE_SHOT                  1/on | 0/off (default off)
  AWOS_ONE_SHOT_BUDGET_TOKENS    context budget, chars/4 (default 24000)
  AWOS_ONE_SHOT_MAX_TOKENS       reply cap (default 16384, floor 16384)
  AWOS_ONE_SHOT_FILE_CAP_TOKENS  a file above this (chars/4) is shown as its
                                 relevant sections + outline (default 6000)
  AWOS_REASONING_ONE_SHOT        reasoning for the one-shot and repair calls
                                 (default off: DeepSeek V4 Flash spent the
                                 16k reply cap on hidden reasoning)

Ablation 3b: a cut-off reply is never retried (its complete blocks are
applied, the rest counts as failed and the job falls back); blocks that fail
to apply get ONE short repair call showing the exact current text around
where each was meant to go.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ONE_SHOT_ENV = "AWOS_ONE_SHOT"
BUDGET_ENV = "AWOS_ONE_SHOT_BUDGET_TOKENS"
MAX_TOKENS_ENV = "AWOS_ONE_SHOT_MAX_TOKENS"
FILE_CAP_ENV = "AWOS_ONE_SHOT_FILE_CAP_TOKENS"
WHOLE_SOURCE_FRACTION_ENV = "AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION"
REASONING_ENV = "AWOS_REASONING_ONE_SHOT"
DEFAULT_BUDGET_TOKENS = 24000
DEFAULT_FILE_CAP_TOKENS = 6000
DEFAULT_WHOLE_SOURCE_FRACTION = 0.5  # the top source goes whole up to this share of the budget
SECTION_RADIUS = 60        # lines shown around each anchor of a large file
REPAIR_RADIUS = 40         # lines shown around a failed block's best match
REPAIR_WHOLE_LINES = 300   # a file up to this many lines goes to repair whole
MIN_MAX_TOKENS = 16384  # a reasoning model thinks before it writes the edits
MAP_BUDGET_TOKENS = 2000

SKIP_DIRS = {
    ".git", ".awos", ".hg", ".svn", ".venv", "venv", "env", ".env", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "node_modules",
    "build", "dist", ".eggs", "site-packages", ".idea", ".vscode", "htmlcov",
}
PROTECTED_PARTS = {".git", ".awos"}
CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cc", ".cpp", ".hpp",
}
MAX_FILE_BYTES = 400_000


def one_shot_enabled() -> bool:
    """On by default since ablation 3b (docs/specs/ablation_one_shot_b.md): off-arm
    cost -48% and time -49% on held-out backupd, solves within the guardrail.
    AWOS_ONE_SHOT=0 turns it off."""
    return os.getenv(ONE_SHOT_ENV, "1").strip().lower() in ("1", "on", "true", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def budget_tokens() -> int:
    return max(1000, _env_int(BUDGET_ENV, DEFAULT_BUDGET_TOKENS))


def max_reply_tokens() -> int:
    return max(MIN_MAX_TOKENS, _env_int(MAX_TOKENS_ENV, MIN_MAX_TOKENS))


def file_cap_tokens() -> int:
    return max(500, _env_int(FILE_CAP_ENV, DEFAULT_FILE_CAP_TOKENS))


def whole_source_fraction() -> float:
    """AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION: the top-ranked source file is sent
    whole while it costs at most this share of the budget, even above the
    per-file cap. 0 turns the rule off; clamped to [0, 1]."""
    try:
        value = float(os.getenv(WHOLE_SOURCE_FRACTION_ENV, "").strip()
                      or DEFAULT_WHOLE_SOURCE_FRACTION)
    except ValueError:
        value = DEFAULT_WHOLE_SOURCE_FRACTION
    return min(1.0, max(0.0, value))


def one_shot_reasoning() -> Optional[dict]:
    """The `reasoning` field for one-shot calls: AWOS_REASONING_ONE_SHOT, default off."""
    try:
        from .providers import _reasoning_value
    except ImportError:
        from providers import _reasoning_value
    return _reasoning_value(os.getenv(REASONING_ENV, "").strip() or "off")


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def is_test_file(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or ".test." in name
        or any(p in ("tests", "test") for p in parts[:-1])
    )


# ── Context ──────────────────────────────────────────────────────────────────

def _walk(root: Path) -> list[str]:
    """Relative paths of the project's text files, skipping VCS/state/venv/caches."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
            and not (d.startswith(".") and d not in (".github",))
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                    continue
                with open(path, "rb") as fh:
                    if b"\0" in fh.read(4096):
                        continue  # binary
            except OSError:
                continue
            out.append(path.relative_to(root).as_posix())
    return out


def _signatures(source: str) -> list[str]:
    """Top-level def/class signatures (class methods one level down)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    def _sig(node) -> str:
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = "..."
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({args})"

    lines = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_sig(node))
        elif isinstance(node, ast.ClassDef):
            try:
                bases = ", ".join(ast.unparse(b) for b in node.bases)
            except Exception:
                bases = ""
            lines.append(f"class {node.name}({bases})" if bases else f"class {node.name}")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.append("    " + _sig(sub))
    return lines


def _outline(source: str) -> list[str]:
    """_signatures, each prefixed with its line number."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            nodes.append(node)
            nodes.extend(sub for sub in node.body
                         if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return [f"L{n.lineno}: {sig}" for n, sig in zip(nodes, _signatures(source))]


def _definitions(source: str) -> dict[str, list[int]]:
    """name -> line numbers of every def/class and module-level assignment."""
    out: dict[str, list[int]] = {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return out
    top = {id(x) for x in tree.body}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign) and id(node) in top:
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for name in names:
            out.setdefault(name, []).append(node.lineno)
    return out


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _task_symbols(task: str) -> set[str]:
    """Identifier-shaped words of the task (filtered later against definitions)."""
    return {m.group(0) for m in _IDENT.finditer(task or "")}


def _merge(windows: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for w in sorted(windows):
        if merged and w[0] <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(list(w))
    return merged


def _section_text(rel: str, lines: list[str], lo: int, hi: int) -> str:
    body = "\n".join(lines[lo - 1:hi])
    fence = "````" if "```" in body else "```"
    return f"{rel} (lines {lo}–{hi} of {len(lines)})\n{fence}\n{body}\n{fence}\n"


def file_sections(rel: str, content: str, task: str, hit_lines: list,
                  cap_tokens: int, radius: int = SECTION_RADIUS) -> Optional[str]:
    """
    A large file as its relevant sections: ±radius-line windows around the
    definitions of symbols the task names and around exploration grep hits,
    merged, each under a `path (lines a–b of N)` header, after the file's
    outline with line numbers. Definitions first, then hits in order, while
    `cap_tokens` holds. None when nothing in the file is relevant.
    """
    lines = content.splitlines()
    n = len(lines)
    if not n:
        return None
    anchors: list[int] = []
    if rel.endswith(".py"):
        defs = _definitions(content)
        for name in sorted(_task_symbols(task)):
            anchors.extend(defs.get(name, [])[:3])
    anchors.extend(h for h in hit_lines if isinstance(h, int) and 1 <= h <= n)
    if not anchors:
        return None

    outline = _outline(content) if rel.endswith(".py") else []
    head = f"Outline of {rel} (line: signature):\n"
    outline_text = (head + "\n".join(outline) + "\n") if outline else ""
    if outline and estimate_tokens(outline_text) > cap_tokens // 4:
        # Too long: keep the entries nearest the anchors, in file order.
        def _line(entry: str) -> int:
            return int(entry[1:entry.index(":")])
        near = sorted(outline, key=lambda e: min(abs(_line(e) - a) for a in anchors))
        kept, size = [], estimate_tokens(head) + 10
        for entry in near:
            cost = estimate_tokens(entry + "\n")
            if size + cost > cap_tokens // 4:
                break
            kept.append(entry)
            size += cost
        outline = sorted(kept, key=_line)
        outline_text = (head + "\n".join(outline) + "\n... (outline truncated to the "
                        "entries nearest the sections)\n") if outline else ""
    used = estimate_tokens(outline_text)

    windows: list[list[int]] = []
    for a in anchors:
        trial = _merge(windows + [[max(1, a - radius), min(n, a + radius)]])
        cost = sum(estimate_tokens(_section_text(rel, lines, lo, hi)) for lo, hi in trial)
        if used + cost <= cap_tokens:
            windows = trial
    if not windows:
        return None
    sections = "\n".join(_section_text(rel, lines, lo, hi) for lo, hi in windows)
    return (outline_text + "\n" if outline_text else "") + sections


def _repo_map(root: Path, files: list[str], whole: set[str], cap_tokens: int) -> str:
    """File list + signatures for Python files not already included whole."""
    lines = []
    for rel in files:
        if rel in whole:
            lines.append(f"{rel}  (shown below)")
            continue
        lines.append(rel)
        if rel.endswith(".py") and not is_test_file(rel):
            try:
                src = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines.extend("    " + s for s in _signatures(src))
    text, used = [], 0
    for line in lines:
        cost = estimate_tokens(line + "\n")
        if used + cost > cap_tokens:
            text.append("... (map truncated)")
            break
        text.append(line)
        used += cost
    return "\n".join(text)


def _goal_score(rel: str, goal_lower: str) -> int:
    """How strongly the task text names this file (path, name, or stem parts)."""
    name = rel.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    if rel.lower() in goal_lower or name.lower() in goal_lower:
        return 100
    if len(stem) >= 4 and stem != "__init__" and re.search(
            rf"\b{re.escape(stem.lower())}\b", goal_lower):
        return 50
    parts = [p for p in stem.lower().split("_") if len(p) >= 2 and p not in ("test", "tests")]
    return sum(1 for p in parts if p in goal_lower)


def _relevance(rel: str, goal_lower: str, hit_counts: dict) -> tuple:
    """(tier, weight): tier 0 = exploration hit, 1 = named by the task, 2 = other."""
    if rel in hit_counts:
        return (0, hit_counts[rel])
    score = _goal_score(rel, goal_lower)
    return (1, score) if score >= 50 else (2, score)


def rank_files(code: list, goal_lower: str, hit_counts: dict) -> list:
    """Fill order: exploration grep hits first (most hits first), then files
    the task names, then the rest (sources before tests, best name overlap)."""
    def key(rel: str) -> tuple:
        tier, weight = _relevance(rel, goal_lower, hit_counts)
        if tier == 0:
            return (0, -weight, is_test_file(rel), rel)
        if tier == 1:
            return (1, -weight, is_test_file(rel), rel)
        return (2, is_test_file(rel), -weight, rel.count("/"), rel)
    return sorted(code, key=key)


def _top_source(ranked: list, goal_lower: str, hit_counts: dict) -> Optional[str]:
    """The highest-ranked relevant source file (not a test, not a .pyi stub)."""
    for rel in ranked:
        if _relevance(rel, goal_lower, hit_counts)[0] >= 2:
            return None
        if not is_test_file(rel) and not rel.endswith(".pyi"):
            return rel
    return None


def _file_block(rel: str, content: str) -> str:
    fence = "````" if "```" in content else "```"
    header = f"{rel}" + ("  (read-only test file)" if is_test_file(rel) else "")
    return f"{header}\n{fence}\n{content}{'' if content.endswith(chr(10)) else chr(10)}{fence}\n"


@dataclass
class OneShotContext:
    text: str
    files: list = field(default_factory=list)       # included whole
    read_only: list = field(default_factory=list)   # test files among them
    tokens: int = 0
    sections: list = field(default_factory=list)    # large files shown in part
    extra: str = ""   # extra context ahead of the repository (AWOS_EXPERIENCE)


def build_context(project_root: str, task: str, exploration: Optional[dict] = None,
                  budget: Optional[int] = None,
                  extra_context: Optional[str] = None) -> OneShotContext:
    """
    A compact repo map plus whole candidate files, ranked (rank_files):
    exploration grep hits first (most hits first), then files the task
    names, then the rest (sources before tests, best name overlap first).
    Each file is included whole while it fits the budget (chars/4). A file
    above the per-file cap (AWOS_ONE_SHOT_FILE_CAP_TOKENS) is shown as its
    relevant sections (see file_sections) instead, except the top-ranked
    relevant source (not a test, not a .pyi stub): while it costs at most
    whole_source_fraction() of the budget it goes whole and first, so the
    code to change is not cut to keyword-hit windows while tests fill
    the budget. Any other file that does not fit is skipped.

    extra_context (default: exploration["extra_context"], e.g. past verified
    changes under AWOS_EXPERIENCE) is carried as `extra`, outside the budget,
    and shown ahead of the repository by build_messages.
    """
    if extra_context is None:
        extra_context = str((exploration or {}).get("extra_context") or "")
    root = Path(project_root).resolve()
    budget = budget or budget_tokens()
    all_files = _walk(root)
    code = [f for f in all_files if Path(f).suffix in CODE_EXTS]
    goal_lower = (task or "").lower()

    hit_counts: dict[str, int] = {}
    hit_lines: dict[str, list] = {}
    for hit in (exploration or {}).get("grep_hits") or []:
        rel = _rel_to(root, hit.get("file", ""))
        if rel:
            hit_counts[rel] = hit_counts.get(rel, 0) + 1
            try:
                hit_lines.setdefault(rel, []).append(int(hit.get("line")))
            except (TypeError, ValueError):
                pass
    for f in (exploration or {}).get("hit_files") or []:
        rel = _rel_to(root, f)
        if rel:
            hit_counts.setdefault(rel, 1)

    ranked = rank_files(code, goal_lower, hit_counts)
    file_budget = budget - min(MAP_BUDGET_TOKENS, budget // 4)
    cap = file_cap_tokens()
    # Whole-source rule: the top relevant source above the per-file cap but
    # within whole_source_fraction() of the budget goes whole and first; the
    # rest keeps its order and sectioning.
    whole_src = _top_source(ranked, goal_lower, hit_counts)
    if whole_src is not None:
        try:
            src_cost = estimate_tokens(_file_block(
                whole_src, (root / whole_src).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            src_cost = 0
        if cap < src_cost <= min(file_budget, int(budget * whole_source_fraction())):
            ranked = [whole_src] + [r for r in ranked if r != whole_src]
        else:
            whole_src = None
    chosen, sectioned, used = [], [], 0
    blocks = []
    for rel in ranked:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ro = is_test_file(rel)
        block = _file_block(rel, content)
        cost = estimate_tokens(block)
        if cost > cap and rel != whole_src:
            room = min(cap, file_budget - used)
            if room < 500:
                continue
            part = file_sections(rel, content, task, hit_lines.get(rel, []), room)
            if part is None:
                continue
            if ro:
                part = f"({rel} is a read-only test file)\n" + part
            cost = estimate_tokens(part)
            if used + cost > file_budget:
                continue
            sectioned.append(rel)
            blocks.append(part)
            used += cost
            continue
        if used + cost > file_budget:
            continue
        chosen.append(rel)
        blocks.append(block)
        used += cost

    repo_map = _repo_map(root, all_files, set(chosen) | set(sectioned),
                         min(MAP_BUDGET_TOKENS, budget // 4))
    text = "## Repository map\n" + repo_map + "\n\n## Files\n\n" + "\n".join(blocks)
    return OneShotContext(
        text=text,
        files=chosen,
        read_only=[f for f in chosen + sectioned if is_test_file(f)],
        tokens=estimate_tokens(text),
        sections=sectioned,
        extra=extra_context or "",
    )


def _rel_to(root: Path, path: str) -> Optional[str]:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


# ── SEARCH/REPLACE blocks ────────────────────────────────────────────────────

_HEAD = re.compile(r"^\s*<{5,9} ?SEARCH\b.*$")
_DIV = re.compile(r"^\s*={5,9}\s*$")
_TAIL = re.compile(r"^\s*>{5,9} ?REPLACE\b.*$")
_FENCE = re.compile(r"^\s*`{3,}")


@dataclass
class EditBlock:
    path: str
    search: str
    replace: str


def _filename(line: str) -> Optional[str]:
    s = line.strip().strip("*").strip()
    s = re.sub(r"^#+\s*", "", s).strip().strip("`").strip().rstrip(":").strip()
    if not s or len(s) > 300 or _FENCE.match(line):
        return None
    if re.fullmatch(r"[\w\-./\\]+", s) and re.search(r"\.\w{1,8}$", s):
        return s
    return None


def parse_blocks(reply: str) -> tuple[list[EditBlock], list[dict]]:
    """
    Aider-format blocks: a path line (optionally followed by a ``` fence), then
    <<<<<<< SEARCH / ======= / >>>>>>> REPLACE. A block with no path line reuses
    the previous block's path. Returns (blocks, malformed) — malformed entries
    are {"path", "reason"}.
    """
    lines = (reply or "").splitlines()
    blocks: list[EditBlock] = []
    bad: list[dict] = []
    last_path: Optional[str] = None
    i = 0
    while i < len(lines):
        if not _HEAD.match(lines[i]):
            i += 1
            continue
        path = None
        j, looked = i - 1, 0
        while j >= 0 and looked < 3:
            if lines[j].strip() and not _FENCE.match(lines[j]):
                path = _filename(lines[j])
                break
            if lines[j].strip():
                looked += 1
            j -= 1
        path = path or last_path
        k = i + 1
        search: list[str] = []
        while k < len(lines) and not _DIV.match(lines[k]) and not _HEAD.match(lines[k]):
            search.append(lines[k])
            k += 1
        if k >= len(lines) or not _DIV.match(lines[k]):
            bad.append({"path": path or "?", "reason": "malformed block: no ======= divider"})
            i = k
            continue
        k += 1
        replace: list[str] = []
        while k < len(lines) and not _TAIL.match(lines[k]) and not _HEAD.match(lines[k]):
            replace.append(lines[k])
            k += 1
        if k >= len(lines) or not _TAIL.match(lines[k]):
            bad.append({"path": path or "?", "reason": "malformed block: no >>>>>>> REPLACE"})
            i = k
            continue
        if not path:
            bad.append({"path": "?", "reason": "block has no file path"})
        else:
            blocks.append(EditBlock(path, "\n".join(search), "\n".join(replace)))
            last_path = path
        i = k + 1
    return blocks, bad


NO_OP_REASON = "no-op edit (replace identical to search)"
NO_NET_CHANGE_REASON = "no net change: the applied edits left every file exactly as it was"
_WS_RUN = re.compile(r"[ \t]+")


def _norm_lines(text: str) -> list[str]:
    """
    Per-line normalisation consistent with the Verifier's whitespace tier
    ([ \t]+ -> " ", rstrip, outer blank lines dropped) except that leading
    indentation is kept as-is: a re-indent is a real change in Python.
    """
    out = []
    for line in text.splitlines():
        body = line.lstrip(" \t")
        indent = line[:len(line) - len(body)]
        out.append((indent + _WS_RUN.sub(" ", body)).rstrip())
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def is_no_op(b: EditBlock) -> bool:
    """True when REPLACE changes nothing SEARCH matches (exactly or up to
    in-line whitespace). An empty SEARCH (file create/append) is never a no-op."""
    if not b.search.strip():
        return False
    return b.replace == b.search or _norm_lines(b.replace) == _norm_lines(b.search)


def _resolve_rel(root: Path, path: str) -> Optional[str]:
    raw = path.strip().strip("`")
    target = (root / raw) if not os.path.isabs(raw) else Path(raw)
    try:
        return target.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def _snapshot(root: Path, blocks: list[EditBlock], snap: dict) -> None:
    """Record each block's file bytes (None when absent) the first time it is seen."""
    for b in blocks:
        rel = _resolve_rel(root, b.path)
        if rel is None or rel in snap:
            continue
        try:
            snap[rel] = (root / rel).read_bytes()
        except OSError:
            snap[rel] = None


def _edit_tool(root: Path):
    try:
        from .tools.code_edit import EditFileTool
    except ImportError:
        from tools.code_edit import EditFileTool
    return EditFileTool(str(root))


def apply_blocks(project_root: str, blocks: list[EditBlock], *,
                 allow_test_edits: bool = True,
                 read_only: Optional[set] = None) -> tuple[list[str], list[dict]]:
    """
    Apply blocks in order. Each goes through EditFileTool: exact unique match,
    then Verifier's fuzzy matcher, with a syntax check before it reaches disk.
    An empty SEARCH creates a file (or appends to an existing one). Paths
    outside the project, under .git/.awos, or (unless allowed) existing test
    files are refused. Returns (applied relative paths, failed blocks).
    """
    root = Path(project_root).resolve()
    tool = _edit_tool(root)
    applied: list[str] = []
    failed: list[dict] = []
    read_only = read_only or set()
    for idx, b in enumerate(blocks):
        raw = b.path.strip().strip("`")
        target = (root / raw) if not os.path.isabs(raw) else Path(raw)
        try:
            target = target.resolve()
            rel = target.relative_to(root).as_posix()
        except (ValueError, OSError):
            failed.append({"path": b.path, "reason": "path outside the project root"})
            continue
        if PROTECTED_PARTS & set(rel.split("/")):
            failed.append({"path": rel, "reason": "edits under .git/.awos are not allowed"})
            continue
        if not allow_test_edits and (rel in read_only or (is_test_file(rel) and target.exists())):
            failed.append({"path": rel, "reason": "read-only test file"})
            continue
        if is_no_op(b):
            # X -> X "succeeds" in EditFileTool but changes nothing; let it
            # reach the repair call as a failed block instead of looking applied.
            failed.append({"path": rel, "reason": NO_OP_REASON,
                           "search": b.search[:300], "index": idx})
            continue
        if b.search.strip() == "" and target.exists() and target.read_text(
                encoding="utf-8", errors="replace").strip():
            try:
                existing = target.read_text(encoding="utf-8")
                sep = "" if existing.endswith("\n") else "\n"
                target.write_text(existing + sep + b.replace.rstrip("\n") + "\n", encoding="utf-8")
                ok, why = True, ""
            except OSError as exc:
                ok, why = False, f"append failed: {exc}"
        else:
            new = b.replace
            if b.search.strip() == "" and new and not new.endswith("\n"):
                new += "\n"
            res = tool.execute({"path": rel, "old_string": "" if not b.search.strip() else b.search,
                                "new_string": new})
            ok, why = res.success, (res.error or "")
        if ok:
            if rel not in applied:
                applied.append(rel)
        else:
            # "index" marks a block the repair call may fix (it reached the file).
            failed.append({"path": rel, "reason": str(why)[:600],
                           "search": b.search[:300], "index": idx})
    return applied, failed


# ── The call ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert software engineer. You are given a coding task and the relevant files of a repository.

Reply with every change needed to complete the task as SEARCH/REPLACE blocks, in this exact format:

path/to/file.py
<<<<<<< SEARCH
exact lines copied from the current file
=======
the lines that replace them
>>>>>>> REPLACE

Rules:
- The path line comes right before each block, relative to the repository root.
- SEARCH must match the current file exactly, character for character, including indentation and comments. Include just enough lines to make it unique.
- Some large files are shown only in sections, each headed "path (lines a–b of N)". Copy SEARCH text exactly from a shown section, never from memory or from the outline; the header's line numbers are not part of the file.
- Use several small blocks rather than one huge one; blocks for the same file are applied in order.
- To create a new file, use an empty SEARCH section and put the whole file in REPLACE.
- Cover every requirement in the task. Follow the existing patterns, helpers and style of the code.
- Files marked read-only are tests: do not edit them unless the task asks for tests. Make the code satisfy them.
- Do not explain at length; the blocks are what gets applied."""


REPAIR_PROMPT = """You are an expert software engineer. Some SEARCH/REPLACE edit blocks did not apply: their SEARCH text does not match the current file exactly.

For each failed block you get the block and the exact current text of the file around where it was meant to go, headed "path (lines a–b of N)" (the line numbers are not part of the file).

Reply with corrected SEARCH/REPLACE blocks for just those changes, in the same format:

path/to/file.py
<<<<<<< SEARCH
exact lines copied from the current text shown
=======
the lines that replace them
>>>>>>> REPLACE

Rules:
- Copy SEARCH character for character from the current text shown, including indentation. Keep it short but unique.
- Make the same change the failed block intended; do not make other changes.
- If a change is already present in the current text, leave it out.
- A block whose "Why" says "no-op edit" changed nothing: its REPLACE was identical to its SEARCH. Write the real change the task needs there; REPLACE must differ from SEARCH.
- No explanations; only the blocks."""


@dataclass
class OneShotResult:
    applied: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    blocks: int = 0
    reply_chars: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    context_tokens: int = 0
    context_files: list = field(default_factory=list)
    context_sections: list = field(default_factory=list)
    elapsed_s: float = 0.0
    error: Optional[str] = None
    reply: str = ""
    truncated: bool = False      # the reply hit its token cap (not retried)
    repair_calls: int = 0
    repaired: int = 0            # repair blocks that applied
    repair_failed: int = 0       # repair blocks that still failed
    repair_reply: str = ""
    repair_error: Optional[str] = None


class _UsageTap:
    """Wraps an OpenAI-shaped client to total the usage of every response."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.input_tokens = 0
        self.output_tokens = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        response = self._inner.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        return response


def one_shot_client() -> Any:
    """OpenAI-shaped client for the call (OpenRouter when keyed; else AWOS_BASE_URL)."""
    try:
        from .providers import chat_client, client_options
    except ImportError:
        from providers import chat_client, client_options
    client = chat_client(max_retries=0)
    if client is None and os.getenv("AWOS_BASE_URL"):
        from openai import OpenAI
        opts = client_options()
        opts["max_retries"] = 0
        client = OpenAI(api_key=os.getenv("AWOS_BASE_URL_KEY", "not-needed"),
                        base_url=os.getenv("AWOS_BASE_URL"), **opts)
    return client


def _sends_reasoning(client: Any) -> bool:
    """Only OpenRouter takes the `reasoning` field."""
    try:
        from .providers import _is_routed
    except ImportError:
        from providers import _is_routed
    return _is_routed(client)


def build_messages(task: str, context: OneShotContext) -> list:
    extra = (getattr(context, "extra", "") or "").strip()
    user = (
        f"# Task\n\n{task.strip()}\n\n"
        + (f"{extra}\n\n" if extra else "")
        + f"# Repository\n\n{context.text}\n\n"
        "Reply with the SEARCH/REPLACE blocks that complete the task."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


@dataclass
class _Reply:
    text: str = ""
    cost_usd: float = 0.0
    calls: int = 0
    truncated: bool = False
    error: Optional[str] = None


def _call(tap: _UsageTap, model: str, messages: list, *, max_tokens: int,
          tracker: Any, request_type: str, send_reasoning: bool) -> _Reply:
    """
    One call, never retried: reasoning per one_shot_reasoning(); a cut-off
    reply comes back with truncated=True and whatever text it had.
    """
    try:
        from .providers import TruncatedReplyError, utility_chat
    except ImportError:
        from providers import TruncatedReplyError, utility_chat
    try:
        text, info = utility_chat(
            tap, "one_shot", model, messages,
            max_tokens=max_tokens, tracker=tracker, request_type=request_type,
            temperature=0, send_reasoning=send_reasoning,
            reasoning=one_shot_reasoning(), retry=False,
        )
        return _Reply(text=text, cost_usd=float(info.get("cost_usd") or 0.0),
                      calls=int(info.get("calls") or 1))
    except TruncatedReplyError as exc:
        return _Reply(text=exc.text or "", cost_usd=float(exc.cost_usd or 0.0),
                      calls=1, truncated=True)
    except Exception as exc:  # noqa: BLE001 — empty reply or transport
        return _Reply(cost_usd=float(getattr(exc, "cost_usd", 0.0) or 0.0), calls=1,
                      error=f"{type(exc).__name__}: {str(exc)[:300]}")


# ── Repair ───────────────────────────────────────────────────────────────────

def _best_start(file_lines: list[str], search_lines: list[str]) -> int:
    """0-based line where `search_lines` most likely belongs in `file_lines`."""
    index: dict[str, list[int]] = {}
    for i, line in enumerate(file_lines):
        key = line.strip()
        if key:
            index.setdefault(key, []).append(i)
    votes: dict[int, int] = {}
    for j, line in enumerate(search_lines):
        hits = index.get(line.strip(), []) if line.strip() else []
        if len(hits) > 50:
            continue  # a line like "return x" says nothing about where
        for i in hits:
            votes[i - j] = votes.get(i - j, 0) + 1
    if votes:
        return max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    import difflib
    best, best_i, best_j = 0.0, 0, 0
    for j, s in enumerate(search_lines):
        s = s.strip()
        if len(s) < 6:
            continue
        for i, line in enumerate(file_lines):
            m = difflib.SequenceMatcher(None, s, line.strip())
            if m.real_quick_ratio() <= best or m.quick_ratio() <= best:
                continue
            r = m.ratio()
            if r > best:
                best, best_i, best_j = r, i, j
        break  # the first substantive line is enough
    return best_i - best_j


def repair_context(project_root: str, failed_blocks: list[EditBlock],
                   budget: Optional[int] = None) -> str:
    """
    The exact current text each failed block was meant for: the whole file
    when small, else its best-match window ± REPAIR_RADIUS lines (merged per
    file), each headed `path (lines a–b of N)`.
    """
    root = Path(project_root).resolve()
    budget = budget or budget_tokens()
    per_file: dict[str, list[list[int]]] = {}
    texts: dict[str, list[str]] = {}
    missing: list[str] = []
    for b in failed_blocks:
        rel = b.path.strip().strip("`")
        path = root / rel
        if rel not in texts:
            try:
                texts[rel] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                missing.append(rel)
                texts[rel] = []
                continue
        lines = texts[rel]
        if not lines:
            continue
        n = len(lines)
        if n <= REPAIR_WHOLE_LINES:
            per_file[rel] = [[1, n]]
            continue
        search = b.search.splitlines()
        start = _best_start(lines, search)
        lo = max(1, start + 1 - REPAIR_RADIUS)
        hi = min(n, start + len(search) + REPAIR_RADIUS)
        per_file[rel] = _merge(per_file.get(rel, []) + [[lo, max(lo, hi)]])
    parts, used = [], 0
    for rel in sorted(set(missing)):
        parts.append(f"{rel}: this file does not exist.\n")
    for rel, windows in per_file.items():
        for lo, hi in windows:
            part = _section_text(rel, texts[rel], lo, hi)
            cost = estimate_tokens(part)
            if used + cost > budget:
                continue
            parts.append(part)
            used += cost
    return "\n".join(parts)


def _block_text(b: EditBlock) -> str:
    return (f"{b.path}\n<<<<<<< SEARCH\n{b.search}\n=======\n{b.replace}\n"
            ">>>>>>> REPLACE")


def build_repair_messages(task: str, project_root: str, failed: list[tuple[EditBlock, str]],
                          budget: Optional[int] = None) -> list:
    shown = []
    for k, (b, why) in enumerate(failed, 1):
        shown.append(f"## Failed block {k} ({b.path})\nWhy: {why[:300]}\n\n{_block_text(b)}\n")
    current = repair_context(project_root, [b for b, _ in failed], budget)
    user = (
        f"# Task (for intent)\n\n{task.strip()[:3000]}\n\n"
        f"# Blocks that failed to apply\n\n" + "\n".join(shown)
        + f"\n# Current text of the files\n\n{current}\n\n"
        "Reply with corrected SEARCH/REPLACE blocks for just these changes."
    )
    return [{"role": "system", "content": REPAIR_PROMPT}, {"role": "user", "content": user}]


# ── One shot ─────────────────────────────────────────────────────────────────

def run_one_shot(client: Any, model: str, project_root: str, task: str,
                 exploration: Optional[dict] = None, *, tracker: Any = None,
                 allow_test_edits: bool = False, budget: Optional[int] = None,
                 max_tokens: Optional[int] = None,
                 extra_context: Optional[str] = None) -> OneShotResult:
    """
    One call, then apply; if blocks failed to apply, one repair call for just
    those. A cut-off reply is not retried: its complete blocks are applied
    and the cut-off counts as a failed block (no repair: the rest is unknown).
    Never raises: a failed call sets `error`.
    """
    start = time.time()
    result = OneShotResult()
    try:
        ctx = build_context(project_root, task, exploration, budget,
                            extra_context=extra_context)
    except Exception as exc:  # noqa: BLE001
        result.error = f"context build failed: {exc}"
        return result
    result.context_tokens = ctx.tokens
    result.context_files = list(ctx.files)
    result.context_sections = list(ctx.sections)

    tap = _UsageTap(client)
    cap = max(int(max_tokens or 0), max_reply_tokens())
    send_reasoning = _sends_reasoning(client)
    reply = _call(tap, model, build_messages(task, ctx), max_tokens=cap, tracker=tracker,
                  request_type="one_shot", send_reasoning=send_reasoning)
    result.cost_usd, result.calls = reply.cost_usd, reply.calls
    result.reply, result.reply_chars = reply.text, len(reply.text)
    result.truncated = reply.truncated
    if reply.error or (reply.truncated and not reply.text.strip()):
        result.error = reply.error or f"reply cut off at {cap} tokens with no text"
        result.input_tokens, result.output_tokens = tap.input_tokens, tap.output_tokens
        result.elapsed_s = time.time() - start
        return result

    read_only = set(ctx.read_only)
    blocks, malformed = parse_blocks(reply.text)
    result.blocks = len(blocks)
    root = Path(project_root).resolve()
    before: dict = {}
    _snapshot(root, blocks, before)
    applied, failed = apply_blocks(project_root, blocks, allow_test_edits=allow_test_edits,
                                   read_only=read_only)
    if reply.truncated:
        # The last block is usually the half-written one: it is malformed.
        failed = failed + [{"path": "?", "reason": f"reply cut off at {cap} tokens; any "
                            "changes after the last complete block are missing"}]
    repairable = [(blocks[f["index"]], f.get("reason", "")) for f in failed
                  if "index" in f and blocks[f["index"]].search.strip()]

    if repairable and not reply.truncated:
        others = [f for f in failed if not ("index" in f and blocks[f["index"]].search.strip())]
        messages = build_repair_messages(task, project_root, repairable, budget)
        fix = _call(tap, model, messages, max_tokens=cap, tracker=tracker,
                    request_type="one_shot_repair", send_reasoning=send_reasoning)
        result.repair_calls = 1
        result.calls += fix.calls
        result.cost_usd += fix.cost_usd
        result.repair_reply = fix.text
        if fix.error or fix.truncated:
            result.repair_error = fix.error or f"repair reply cut off at {cap} tokens"
        else:
            r_blocks, r_bad = parse_blocks(fix.text)
            _snapshot(root, r_blocks, before)
            r_applied, r_failed = apply_blocks(project_root, r_blocks,
                                               allow_test_edits=allow_test_edits,
                                               read_only=read_only)
            result.repaired = len(r_blocks) - len(r_failed)
            result.repair_failed = len(r_failed) + len(r_bad)
            for rel in r_applied:
                if rel not in applied:
                    applied.append(rel)
            for f in r_failed:
                f.pop("index", None)
                f["reason"] = "after repair: " + str(f.get("reason", ""))
            if not r_blocks and not r_bad:
                # No blocks back: the model judged nothing left to change, or
                # replied with prose. Keep the originals as failed either way.
                result.repair_error = "repair reply had no SEARCH/REPLACE blocks"
            else:
                failed = others + r_bad + r_failed

    # Only files whose bytes actually changed count (e.g. an edit undone by a
    # later block leaves nothing to test).
    changed = []
    for rel in applied:
        try:
            now = (root / rel).read_bytes()
        except OSError:
            now = None
        if now != before.get(rel):
            changed.append(rel)
    if applied and not changed:
        failed = failed + [{"path": "?", "reason": NO_NET_CHANGE_REASON}]
    applied = changed
    result.applied = applied
    result.failed = malformed + failed
    result.input_tokens, result.output_tokens = tap.input_tokens, tap.output_tokens
    result.elapsed_s = time.time() - start
    return result
