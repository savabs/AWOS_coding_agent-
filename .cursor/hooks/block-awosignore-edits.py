#!/usr/bin/env python3
"""preToolUse hook: deny Write/Edit to paths listed in .awosignore."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AWOSIGNORE = ROOT / ".awosignore"

WRITE_TOOLS = {"Write", "StrReplace", "search_replace", "ApplyPatch", "EditNotebook"}


def load_patterns() -> list[str]:
    if not AWOSIGNORE.exists():
        return []
    patterns: list[str] = []
    for line in AWOSIGNORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def normalize_path(raw: str) -> str:
    p = raw.strip().replace("\\", "/")
    if p.startswith(str(ROOT)):
        p = p[len(str(ROOT)) :].lstrip("/")
    return p.lstrip("./")


def extract_paths(payload: dict) -> list[str]:
    paths: list[str] = []
    for key in ("file_path", "path", "target_file", "filePath"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            paths.append(normalize_path(val))

    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        for key in ("file_path", "path", "target_file", "filePath"):
            val = tool_input.get(key)
            if isinstance(val, str) and val:
                paths.append(normalize_path(val))
        args = tool_input.get("arguments") or tool_input.get("args")
        if isinstance(args, dict):
            for key in ("file_path", "path", "target_file", "filePath"):
                val = args.get(key)
                if isinstance(val, str) and val:
                    paths.append(normalize_path(val))

    return list(dict.fromkeys(paths))


def matches_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern.rstrip("/") + "/") or path == pattern.rstrip("/")
    if "*" in pattern:
        regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
        return bool(re.match(regex, path))
    return path == pattern or path.endswith("/" + pattern)


def is_blocked(path: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if matches_pattern(path, pattern):
            return pattern
    return None


def deny(path: str, pattern: str) -> None:
    msg = (
        f"Edit blocked: `{path}` is in `.awosignore` (matched `{pattern}`). "
        "These hot-path files are dogfood-protected. Stop AWOS sessions before editing, "
        "or remove the path from `.awosignore` deliberately."
    )
    print(
        json.dumps(
            {
                "permission": "deny",
                "user_message": msg,
                "agent_message": msg,
            }
        )
    )
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"permission": "allow"}))
        return

    tool = (
        payload.get("tool_name")
        or payload.get("tool")
        or payload.get("tool_type")
        or ""
    )
    if tool and tool not in WRITE_TOOLS and "write" not in tool.lower() and "edit" not in tool.lower():
        print(json.dumps({"permission": "allow"}))
        return

    patterns = load_patterns()
    if not patterns:
        print(json.dumps({"permission": "allow"}))
        return

    for path in extract_paths(payload):
        hit = is_blocked(path, patterns)
        if hit:
            deny(path, hit)
            return

    print(json.dumps({"permission": "allow"}))


if __name__ == "__main__":
    main()
