"""
tools/filesystem.py — Local filesystem tools for the coding agent.

Tools provided:
  - ReadFileTool    read file contents (with optional line range)
  - WriteFileTool   write / overwrite a file
  - ListDirTool     list directory contents
  - FindFilesTool   find files matching a glob pattern
  - GrepTool        search for a pattern across files
  - ShowSymbolTool  one class/function body by name (AWOS_SKELETON_VIEW)

AWOS_SKELETON_VIEW=1 (default 0) bounds read_file: a large .py file read
without a range comes back as a skeleton, ranges are capped at ~100 lines,
and `symbol=` shows one body. See docs/specs/skeleton_viewer.md.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Optional

from .base import Tool, ToolResult

try:
    from .. import skeleton as _skel
except ImportError:  # tools/ imported as a top-level package
    import skeleton as _skel  # type: ignore[no-redef]

#: .env and .env.<anything>: the same names the sandbox denies.
_DOTENV = re.compile(r"^\.env(\.[^/]*)?$")


def skeleton_view_enabled() -> bool:
    """AWOS_SKELETON_VIEW: bounded file views (trick T5). Default off."""
    return os.getenv("AWOS_SKELETON_VIEW", "0").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def skeleton_min_lines() -> int:
    """Files at least this long open as a skeleton/window when read whole."""
    return _env_int("AWOS_SKELETON_MIN_LINES", 300)


def skeleton_max_lines() -> int:
    """Most lines one read_file call returns while the view is on."""
    return _env_int("AWOS_SKELETON_MAX_LINES", _skel.WINDOW_LINES)


def _skeleton_log(path: Path, total: int, what: str, chars: int) -> None:
    try:
        print(f"[SKELETON] {path.name} {total} lines -> {what} {chars} chars", flush=True)
    except Exception:
        pass


def _show_symbol(path: Path, content: str, query: str) -> ToolResult:
    """One symbol's body from `content`, capped like a range read."""
    if path.suffix != ".py":
        return ToolResult.fail(f"symbol= works on Python files only; use start_line/end_line for {path.name}")
    try:
        match, _ = _skel.find_symbol(content, query)
        body = _skel.show_symbol(content, query)
    except SyntaxError as e:
        return ToolResult.fail(f"Cannot parse {path.name} ({e}); use start_line/end_line")
    except KeyError as e:
        return ToolResult.fail(f"{e.args[0]} in {path.name}. read_file without a range lists them.")
    cap = 3 * skeleton_max_lines()  # a whole body is one unit; only giants are cut
    span = match.last - match.first + 1
    if span > cap:
        lines = content.splitlines(keepends=True)
        end = match.first + skeleton_max_lines() - 1
        body = (f"{match.kind} {match.qualname}: lines {match.first}-{match.last} "
                f"({span} lines; showing {match.first}-{end}). Read a method by name, "
                f"or continue with start_line={end + 1}.\n"
                + _skel.numbered(lines[match.first - 1:end], match.first))
    _skeleton_log(path, len(content.splitlines()), f"symbol {query}", len(body))
    return ToolResult.ok(
        text=f"File: {path}\n\n{body}",
        data={"path": str(path), "content": body, "symbol": match.qualname,
              "start_line": match.first, "end_line": match.last},
    )


def _line_numbers(value: Any) -> list[int]:
    """
    The integers in a line-number argument. Models send "20", 20, "`160",
    "[20, 55]", "20-55" or [20, 55]; int() raised on all but the first two and
    cost the caller a turn. Unsigned: in "20-55" the hyphen is a range, and a
    negative line number is not one (-55 sliced from the end and returned the
    wrong lines as success).
    """
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [abs(int(value))]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in _line_numbers(v)]
    return [int(n) for n in re.findall(r"\d+", str(value))]


class _RootedTool(Tool):
    """
    Mixin giving a filesystem tool a project root to resolve against.

    Without it, a relative path means "relative to wherever the process was
    started", which differs from EditFileTool's project_root — so `read_file`
    and `edit_file` in the same registry would disagree about what "calc.py"
    means. Defaults to the process cwd, preserving prior behaviour for every
    existing caller.

    confine=True keeps the tool inside project_root and away from .env files.
    These tools run on the host, not in the sandbox, so the sandbox's deny
    rules never applied to them: an absolute path or a symlink read the
    owner's real ~/.zshrc or the project's .env into the model's context.
    """

    def __init__(self, project_root: str = ".", confine: bool = False) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.confine = confine

    def _resolve(self, raw: str) -> Path:
        path = Path(str(raw).strip()).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        return path.resolve()

    def _refusal(self, path: Path) -> Optional[str]:
        """Why a confined tool may not use `path`, or None. Pass it resolved."""
        if not self.confine:
            return None
        if not path.is_relative_to(self.project_root):
            return f"Refused: {path} is outside the project ({self.project_root})"
        if any(_DOTENV.match(part) for part in path.relative_to(self.project_root).parts):
            return f"Refused: {path.name} holds secrets and is not readable"
        return None


class ReadFileTool(_RootedTool):
    """Read the contents of a local file."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        if skeleton_view_enabled():
            return (
                "Read a file. Large Python files read without a range return a skeleton "
                "(class/def signatures with line ranges); then read a body with symbol= "
                f"or a start_line/end_line range (at most {skeleton_max_lines()} lines per call)."
            )
        return "Read the contents of a file on the local filesystem. Supports optional start/end line numbers."

    @property
    def parameters(self) -> dict[str, str]:
        params = {
            "path": "Absolute or relative path to the file",
            "start_line": "First line to read, 1-indexed (optional)",
            "end_line": "Last line to read, inclusive (optional)",
        }
        if skeleton_view_enabled():
            params["symbol"] = "Class, function or Class.method whose body to show (optional, Python)"
        return params

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "path" not in args or not args["path"].strip():
            return ["Missing required parameter: 'path'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args["path"])
        refusal = self._refusal(path)
        if refusal:
            return ToolResult.fail(refusal)
        if not path.exists():
            return ToolResult.fail(f"File not found: {path}")
        if not path.is_file():
            return ToolResult.fail(f"Not a file: {path}")

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult.fail(f"Cannot read file: {e}")

        lines = content.splitlines(keepends=True)
        total = len(lines)

        if skeleton_view_enabled():
            bounded = self._bounded(path, content, lines, args)
            if bounded is not None:
                return bounded

        start_nums = _line_numbers(args.get("start_line"))
        end_nums = _line_numbers(args.get("end_line"))
        # A range packed into start_line ("[20, 55]") supplies the end too.
        start = (start_nums[0] if start_nums else 1) - 1
        end = end_nums[0] if end_nums else (start_nums[1] if len(start_nums) > 1 else total)
        start = max(0, start)
        if end < 1:
            end = total  # end_line 0: no end given
        # An end before the start ("55-20") still shows the start line.
        end = min(total, max(end, start + 1))
        selected = lines[start:end]
        excerpt = "".join(selected)

        return ToolResult.ok(
            text=f"File: {path} (lines {start+1}–{end} of {total})\n\n{excerpt}",
            data={"path": str(path), "content": excerpt, "total_lines": total},
        )

    def _bounded(self, path: Path, content: str, lines: list[str],
                 args: dict[str, Any]) -> Optional[ToolResult]:
        """
        The AWOS_SKELETON_VIEW answer, or None for the unbounded path (a small
        file read whole). symbol= shows one body; a range is capped at
        skeleton_max_lines(); a large file read whole opens as a skeleton
        (.py) or its first window (anything else).
        """
        total = len(lines)
        query = args.get("symbol")
        if isinstance(query, str) and query.strip():
            return _show_symbol(path, content, query)

        start_nums = _line_numbers(args.get("start_line"))
        end_nums = _line_numbers(args.get("end_line"))
        cap = skeleton_max_lines()
        if not start_nums and not end_nums:
            if total < skeleton_min_lines():
                return None
            if path.suffix == ".py":
                try:
                    skel = _skel.skeleton(content, path.name)
                except (SyntaxError, ValueError):
                    skel = None
                if skel is not None:
                    _skeleton_log(path, total, "skeleton", len(skel))
                    text = (f"File: {path} ({total} lines, too long to show whole)\n\n{skel}\n"
                            f"Next: read_file with start_line/end_line from the L<a>-<b> ranges "
                            f"above (at most {cap} lines per call), or symbol=\"Class.method\" "
                            "for one whole body.")
                    return ToolResult.ok(text=text, data={"path": str(path), "content": skel,
                                                          "total_lines": total, "skeleton": True})
            start, end = 1, min(total, cap)
        else:
            start = max(1, start_nums[0] if start_nums else 1)
            end = end_nums[0] if end_nums else (start_nums[1] if len(start_nums) > 1 else total)
            if end < 1:
                end = total
            end = min(total, max(end, start))
            start = min(start, max(1, total))
        asked_end = end
        end = min(end, start + cap - 1)
        excerpt = _skel.numbered(lines[start - 1:end], start)
        note = ""
        if end < asked_end or (not start_nums and not end_nums and end < total):
            note = (f"\n[showing {end - start + 1} of {total} lines; at most {cap} per call. "
                    f"Continue with start_line={end + 1}"
                    + (", or use symbol= for one body" if path.suffix == ".py" else "") + ".]")
            _skeleton_log(path, total, f"window {start}-{end}", len(excerpt))
        return ToolResult.ok(
            text=f"File: {path} (lines {start}–{end} of {total})\n\n{excerpt}{note}",
            data={"path": str(path), "content": excerpt, "total_lines": total},
        )


class ShowSymbolTool(_RootedTool):
    """Show one class or function from a Python file, by (dotted) name."""

    @property
    def name(self) -> str:
        return "show_symbol"

    @property
    def description(self) -> str:
        return ("Show the full source of one class or function in a Python file, with line "
                "numbers. Name it as in the read_file skeleton: Class, function or Class.method.")

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "path": "Absolute or relative path to the Python file",
            "symbol": "Class, function or Class.method",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        missing = [k for k in ("path", "symbol") if not str(args.get(k) or "").strip()]
        return [f"Missing required parameter: '{k}'" for k in missing]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args["path"])
        refusal = self._refusal(path)
        if refusal:
            return ToolResult.fail(refusal)
        if not path.is_file():
            return ToolResult.fail(f"File not found: {path}")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult.fail(f"Cannot read file: {e}")
        return _show_symbol(path, content, str(args["symbol"]))


class WriteFileTool(_RootedTool):
    """Write (create or overwrite) a local file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a local file. Creates parent directories if needed. DANGEROUS: overwrites without warning."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "path": "Absolute or relative path to write",
            "content": "File content to write",
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args["path"])
        content = args.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult.fail(f"Cannot write file: {e}")
        return ToolResult.ok(
            text=f"Wrote {len(content)} characters to {path}",
            data={"path": str(path), "bytes": len(content.encode())},
        )


class ListDirTool(_RootedTool):
    """List the contents of a directory."""

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and subdirectories inside a directory."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "path": "Directory path to list (default: current working directory)",
            "recursive": "List recursively (true/false, default: false)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        dir_path = self._resolve(args.get("path", "."))
        refusal = self._refusal(dir_path)
        if refusal:
            return ToolResult.fail(refusal)
        recursive = str(args.get("recursive", "false")).lower() in ("true", "1", "yes")

        if not dir_path.exists():
            return ToolResult.fail(f"Directory not found: {dir_path}")
        if not dir_path.is_dir():
            return ToolResult.fail(f"Not a directory: {dir_path}")

        entries = []
        if recursive:
            for p in sorted(dir_path.rglob("*")):
                rel = p.relative_to(dir_path)
                kind = "dir" if p.is_dir() else "file"
                entries.append({"path": str(rel), "type": kind})
        else:
            for p in sorted(dir_path.iterdir()):
                kind = "dir" if p.is_dir() else "file"
                size = p.stat().st_size if p.is_file() else None
                entries.append({"path": p.name, "type": kind, "size": size})

        lines = [f"Directory: {dir_path}\n"]
        for e in entries:
            prefix = "📁 " if e["type"] == "dir" else "📄 "
            size_str = f"  ({e['size']} B)" if e.get("size") is not None else ""
            lines.append(f"{prefix}{e['path']}{size_str}")

        return ToolResult.ok(
            text="\n".join(lines),
            data={"path": str(dir_path), "entries": entries},
        )


class FindFilesTool(_RootedTool):
    """Find files matching a glob pattern under a directory."""

    @property
    def name(self) -> str:
        return "find_files"

    @property
    def description(self) -> str:
        return "Find files matching a glob pattern (e.g. '**/*.py') under a directory."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "pattern": "Glob pattern to match (e.g. '**/*.py', '*.md')",
            "root": "Root directory to search from (default: current working directory)",
            "max_results": "Maximum number of matches to return (default: 50)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "pattern" not in args or not args["pattern"].strip():
            return ["Missing required parameter: 'pattern'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        pattern = args["pattern"].strip()
        root = self._resolve(args.get("root", "."))
        max_results = int(args.get("max_results", 50))

        refusal = self._refusal(root)
        if refusal:
            return ToolResult.fail(refusal)
        if not root.exists():
            return ToolResult.fail(f"Root directory not found: {root}")

        # A pattern can climb out ("../*") and a match can be a symlink out.
        matches = [m for m in sorted(root.glob(pattern)) if not self._refusal(m.resolve())]
        matches = matches[:max_results]
        lines = [f"Find: {pattern!r} under {root}\n"]
        results = []
        for m in matches:
            rel = m.relative_to(root)
            lines.append(str(rel))
            results.append(str(rel))

        if not results:
            lines.append("(no matches)")

        return ToolResult.ok(
            text="\n".join(lines),
            data={"pattern": pattern, "root": str(root), "matches": results},
        )


class GrepTool(_RootedTool):
    """Search for a regex or literal pattern across files in a directory."""

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search for a text pattern (regex or literal) in files. Returns matching lines with context."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "pattern": "Regex or literal string to search for",
            "root": "Directory to search (default: current working directory)",
            "file_glob": "Glob filter for filenames (e.g. '*.py'). Default: '*'",
            "literal": "Treat pattern as literal string instead of regex (true/false, default: false)",
            "max_results": "Maximum number of matching lines (default: 40)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "pattern" not in args or not args["pattern"].strip():
            return ["Missing required parameter: 'pattern'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        pattern = args["pattern"].strip()
        root = self._resolve(args.get("root", "."))
        file_glob = args.get("file_glob", "*")
        literal = str(args.get("literal", "false")).lower() in ("true", "1", "yes")
        max_results = int(args.get("max_results", 40))

        try:
            regex = re.compile(re.escape(pattern) if literal else pattern, re.IGNORECASE)
        except re.error as e:
            return ToolResult.fail(f"Invalid regex: {e}")

        refusal = self._refusal(root)
        if refusal:
            return ToolResult.fail(refusal)

        if not root.exists():
            return ToolResult.fail(f"path not found: {root}")

        # A single file as root: rglob on a file yields nothing, which used to
        # read as a silent "(no matches)". Search that file; file_glob is moot
        # because the caller named the file explicitly.
        if root.is_file():
            candidates = [root]
            base = root.parent
        else:
            candidates = sorted(root.rglob(file_glob))
            base = root

        hits: list[dict] = []
        for filepath in candidates:
            if not filepath.is_file() or self._refusal(filepath.resolve()):
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append({"file": str(filepath.relative_to(base)), "line": lineno, "text": line.rstrip()})
                    if len(hits) >= max_results:
                        break
            if len(hits) >= max_results:
                break

        lines = [f"Grep: {pattern!r} in {root} ({file_glob})\n"]
        for h in hits:
            lines.append(f"{h['file']}:{h['line']}: {h['text']}")

        if not hits:
            lines.append("(no matches)")

        return ToolResult.ok(
            text="\n".join(lines),
            data={"pattern": pattern, "hits": hits},
        )
