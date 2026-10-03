"""
tools/filesystem.py — Local filesystem tools for the coding agent.

Tools provided:
  - ReadFileTool    read file contents (with optional line range)
  - WriteFileTool   write / overwrite a file
  - ListDirTool     list directory contents
  - FindFilesTool   find files matching a glob pattern
  - GrepTool        search for a pattern across files
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Optional

from .base import Tool, ToolResult

#: .env and .env.<anything>: the same names the sandbox denies.
_DOTENV = re.compile(r"^\.env(\.[^/]*)?$")


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
        return "Read the contents of a file on the local filesystem. Supports optional start/end line numbers."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "path": "Absolute or relative path to the file",
            "start_line": "First line to read, 1-indexed (optional)",
            "end_line": "Last line to read, inclusive (optional)",
        }

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

        hits: list[dict] = []
        for filepath in sorted(root.rglob(file_glob)):
            if not filepath.is_file() or self._refusal(filepath.resolve()):
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append({"file": str(filepath.relative_to(root)), "line": lineno, "text": line.rstrip()})
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
