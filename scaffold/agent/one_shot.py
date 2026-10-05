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
DEFAULT_BUDGET_TOKENS = 24000
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
    return os.getenv(ONE_SHOT_ENV, "0").strip().lower() in ("1", "on", "true", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def budget_tokens() -> int:
    return max(1000, _env_int(BUDGET_ENV, DEFAULT_BUDGET_TOKENS))


def max_reply_tokens() -> int:
    return max(MIN_MAX_TOKENS, _env_int(MAX_TOKENS_ENV, MIN_MAX_TOKENS))


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


def _repo_map(root: Path, files: list[str], whole: set[str], cap_tokens: int) -> str:
    """File list + signatures for Python files not already included whole."""
    lines = []
    for rel in files:
        if rel in whole:
            lines.append(f"{rel}  (full content below)")
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


@dataclass
class OneShotContext:
    text: str
    files: list = field(default_factory=list)       # included whole
    read_only: list = field(default_factory=list)   # test files among them
    tokens: int = 0


def build_context(project_root: str, task: str, exploration: Optional[dict] = None,
                  budget: Optional[int] = None) -> OneShotContext:
    """
    A compact repo map plus whole candidate files, ranked: exploration grep
    hits first (most hits first), then files the task names, then the rest
    (sources before tests, best name overlap first). Each file is included
    whole while it fits the budget (chars/4); one that does not is skipped.
    """
    root = Path(project_root).resolve()
    budget = budget or budget_tokens()
    all_files = _walk(root)
    code = [f for f in all_files if Path(f).suffix in CODE_EXTS]
    goal_lower = (task or "").lower()

    hit_counts: dict[str, int] = {}
    for hit in (exploration or {}).get("grep_hits") or []:
        rel = _rel_to(root, hit.get("file", ""))
        if rel:
            hit_counts[rel] = hit_counts.get(rel, 0) + 1
    for f in (exploration or {}).get("hit_files") or []:
        rel = _rel_to(root, f)
        if rel:
            hit_counts.setdefault(rel, 1)

    def rank(rel: str) -> tuple:
        if rel in hit_counts:
            return (0, -hit_counts[rel], is_test_file(rel), rel)
        score = _goal_score(rel, goal_lower)
        if score >= 50:
            return (1, -score, is_test_file(rel), rel)
        return (2, is_test_file(rel), -score, rel.count("/"), rel)

    ranked = sorted(code, key=rank)
    file_budget = budget - min(MAP_BUDGET_TOKENS, budget // 4)
    chosen, used = [], 0
    blocks = []
    for rel in ranked:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ro = is_test_file(rel)
        fence = "````" if "```" in content else "```"
        header = f"{rel}" + ("  (read-only test file)" if ro else "")
        block = f"{header}\n{fence}\n{content}{'' if content.endswith(chr(10)) else chr(10)}{fence}\n"
        cost = estimate_tokens(block)
        if used + cost > file_budget:
            continue
        chosen.append(rel)
        blocks.append(block)
        used += cost

    repo_map = _repo_map(root, all_files, set(chosen), min(MAP_BUDGET_TOKENS, budget // 4))
    text = "## Repository map\n" + repo_map + "\n\n## Files\n\n" + "\n".join(blocks)
    return OneShotContext(
        text=text,
        files=chosen,
        read_only=[f for f in chosen if is_test_file(f)],
        tokens=estimate_tokens(text),
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
    for b in blocks:
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
            failed.append({"path": rel, "reason": str(why)[:600],
                           "search": b.search[:300]})
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
- Use several small blocks rather than one huge one; blocks for the same file are applied in order.
- To create a new file, use an empty SEARCH section and put the whole file in REPLACE.
- Cover every requirement in the task. Follow the existing patterns, helpers and style of the code.
- Files marked read-only are tests: do not edit them unless the task asks for tests. Make the code satisfy them.
- Do not explain at length; the blocks are what gets applied."""


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
    elapsed_s: float = 0.0
    error: Optional[str] = None
    reply: str = ""


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


def build_messages(task: str, context: OneShotContext) -> list:
    user = (
        f"# Task\n\n{task.strip()}\n\n# Repository\n\n{context.text}\n\n"
        "Reply with the SEARCH/REPLACE blocks that complete the task."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def run_one_shot(client: Any, model: str, project_root: str, task: str,
                 exploration: Optional[dict] = None, *, tracker: Any = None,
                 allow_test_edits: bool = False, budget: Optional[int] = None,
                 max_tokens: Optional[int] = None) -> OneShotResult:
    """One call, then apply. Never raises: a failed call sets `error`."""
    try:
        from .providers import _is_routed, utility_chat
    except ImportError:
        from providers import _is_routed, utility_chat

    start = time.time()
    result = OneShotResult()
    try:
        ctx = build_context(project_root, task, exploration, budget)
    except Exception as exc:  # noqa: BLE001
        result.error = f"context build failed: {exc}"
        return result
    result.context_tokens = ctx.tokens
    result.context_files = list(ctx.files)

    tap = _UsageTap(client)
    try:
        text, info = utility_chat(
            tap, "one_shot", model, build_messages(task, ctx),
            max_tokens=max(int(max_tokens or 0), max_reply_tokens()),
            tracker=tracker,
            request_type="one_shot",
            temperature=0,
            send_reasoning=_is_routed(client),
        )
        result.cost_usd = float(info.get("cost_usd") or 0.0)
        result.calls = int(info.get("calls") or 0)
    except Exception as exc:  # noqa: BLE001 — ModelReplyError or transport
        result.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        result.cost_usd = float(getattr(exc, "cost_usd", 0.0) or 0.0)
        result.calls = 1
        text = ""
    result.input_tokens, result.output_tokens = tap.input_tokens, tap.output_tokens
    result.reply = text
    result.reply_chars = len(text)
    if result.error is None:
        blocks, malformed = parse_blocks(text)
        result.blocks = len(blocks)
        applied, failed = apply_blocks(project_root, blocks,
                                       allow_test_edits=allow_test_edits,
                                       read_only=set(ctx.read_only))
        result.applied = applied
        result.failed = malformed + failed
    result.elapsed_s = time.time() - start
    return result
