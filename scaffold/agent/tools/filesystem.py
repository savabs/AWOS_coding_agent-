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
from typing import Any

from .base import Tool, ToolResult


class ReadFileTool(Tool):
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
        path = Path(args["path"].strip()).expanduser().resolve()
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

        start = int(args.get("start_line", 1)) - 1
        end = int(args.get("end_line", total))
        start = max(0, start)
        end = min(total, end)
        selected = lines[start:end]
        excerpt = "".join(selected)

        return ToolResult.ok(
            text=f"File: {path} (lines {start+1}–{end} of {total})\n\n{excerpt}",
            data={"path": str(path), "content": excerpt, "total_lines": total},
        )


class WriteFileTool(Tool):
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
        path = Path(args["path"].strip()).expanduser().resolve()
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


class ListDirTool(Tool):
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
        dir_path = Path(args.get("path", ".")).expanduser().resolve()
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


class FindFilesTool(Tool):
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
        root = Path(args.get("root", ".")).expanduser().resolve()
        max_results = int(args.get("max_results", 50))

        if not root.exists():
            return ToolResult.fail(f"Root directory not found: {root}")

        matches = sorted(root.glob(pattern))[:max_results]
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


class GrepTool(Tool):
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
        root = Path(args.get("root", ".")).expanduser().resolve()
        file_glob = args.get("file_glob", "*")
        literal = str(args.get("literal", "false")).lower() in ("true", "1", "yes")
        max_results = int(args.get("max_results", 40))

        try:
            regex = re.compile(re.escape(pattern) if literal else pattern, re.IGNORECASE)
        except re.error as e:
            return ToolResult.fail(f"Invalid regex: {e}")

        hits: list[dict] = []
        for filepath in sorted(root.rglob(file_glob)):
            if not filepath.is_file():
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
