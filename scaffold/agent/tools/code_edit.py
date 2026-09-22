"""
tools/code_edit.py — Tools that let a model change code and observe the result.

Two tools, both thin wrappers over machinery the repository already has:

  EditFileTool  surgical old/new replacement, routed through Verifier so the
                edit inherits AST/syntax checking, contract compliance and
                fuzzy matching, and only reaches disk once those pass
  RunTestsTool  wraps TestRunner so the model can see whether its own change
                passes, instead of being told after the loop has ended

Deliberately not `write_file`: whole-file rewrites cost output tokens
proportional to file size and let a model silently drop code it did not mean to
touch. A failed edit here returns Verifier's error_context, which names what
did not match and shows nearby content, so the model can correct itself on the
next turn.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from .base import Tool, ToolResult
except ImportError:  # direct execution / sys.path-style import
    from base import Tool, ToolResult


def _load_verifier():
    try:
        from ..verifier import Verifier
    except ImportError:
        from verifier import Verifier
    return Verifier


def _load_test_runner():
    try:
        from ..test_runner import TestRunner
    except ImportError:
        from test_runner import TestRunner
    return TestRunner


class EditFileTool(Tool):
    """Replace an exact snippet in a file, validated before it is written."""

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = Path(project_root)

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace an exact snippet of a file with new content. old_string must "
            "match the file exactly, including indentation, and must appear only "
            "once — include surrounding lines to make it unique. The edit is "
            "syntax-checked before it is written, and is rejected if it would "
            "break the file. To create a new file, pass an empty old_string."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "path": "Path to the file to modify, relative to the project root",
            "old_string": (
                "Exact text to replace, copied verbatim from the file. "
                "Empty string creates a new file with new_string as its content."
            ),
            "new_string": "Text to write in its place",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        errors = []
        if not args.get("path", "").strip():
            errors.append("Missing required parameter: 'path'")
        if "old_string" not in args:
            errors.append("Missing required parameter: 'old_string'")
        if "new_string" not in args:
            errors.append("Missing required parameter: 'new_string'")
        return errors

    def _resolve(self, raw_path: str) -> Path:
        path = Path(raw_path.strip()).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        return path

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve(args["path"])
        old_string = args["old_string"]
        new_string = args["new_string"]

        # Creation path: Verifier's SEARCH/REPLACE contract assumes an existing
        # file, so an empty old_string is handled here instead.
        if old_string == "":
            if path.exists() and path.read_text(encoding="utf-8", errors="replace").strip():
                return ToolResult.fail(
                    f"{path} already exists and is not empty. Pass the text you "
                    "want to replace as old_string rather than an empty string."
                )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_string, encoding="utf-8")
            except OSError as exc:
                return ToolResult.fail(f"Could not create {path}: {exc}")
            return ToolResult.ok(
                f"Created {path} ({len(new_string.splitlines())} lines).",
                {"path": str(path), "created": True},
            )

        if not path.exists():
            return ToolResult.fail(
                f"File not found: {path}. Use list_dir or find_files to locate it, "
                "or pass an empty old_string to create it."
            )

        content = path.read_text(encoding="utf-8", errors="replace")
        occurrences = content.count(old_string)
        if occurrences > 1:
            return ToolResult.fail(
                f"old_string appears {occurrences} times in {path}; the edit would "
                "be ambiguous. Include more surrounding context to make it unique."
            )

        Verifier = _load_verifier()
        outcome = Verifier().verify_and_apply(
            {"search": old_string, "replace": new_string},
            str(path),
            check_type="syntax",
        )

        if outcome.get("success"):
            return ToolResult.ok(
                f"Edited {path}.",
                {"path": str(path), "created": False},
            )

        # error_context is written for a model to act on: it explains what failed
        # and, for a non-matching search, shows nearby file content.
        detail = outcome.get("error_context") or "; ".join(outcome.get("errors", []))
        return ToolResult.fail(detail or f"Edit to {path} was rejected.")


class RunTestsTool(Tool):
    """Run the project's test suite and report what failed."""

    #: TestRunner's own guard (test_runner._SAFETY_ENV_VAR). Reused rather than
    #: introducing a second flag, so there is one switch for running tests.
    SAFETY_ENV_VAR = "AWOS_SAFE_TO_RUN_TESTS"

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = Path(project_root)

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Run the project's test suite and return pass/fail counts plus the "
            "output of failing tests. Use this to check your own change before "
            "declaring the task finished."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "changed_files": (
                "Optional comma-separated paths just modified, used to narrow "
                "the run to the tests covering them"
            ),
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        return []  # every argument is optional

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if os.environ.get(self.SAFETY_ENV_VAR) != "1":
            return ToolResult.fail(
                f"Test execution is disabled. Set {self.SAFETY_ENV_VAR}=1 to allow it."
            )

        raw = (args.get("changed_files") or "").strip()
        changed = [p.strip() for p in raw.split(",") if p.strip()] or None

        TestRunner = _load_test_runner()
        result = TestRunner(project_root=str(self.project_root)).run(changed_files=changed)

        if getattr(result, "no_tests_found", False):
            return ToolResult.ok(
                "No tests were found for this project.",
                {"no_tests_found": True, "pass_rate": None},
            )

        passed = getattr(result, "passed", 0)
        failed = getattr(result, "failed", 0)
        pass_rate = getattr(result, "pass_rate", None)
        summary = f"{passed} passed, {failed} failed."

        if failed:
            output = getattr(result, "raw_output", "") or ""
            # Tail, not head: pytest puts the failure summary at the end.
            return ToolResult.ok(
                f"{summary}\n\n{output[-3000:]}",
                {"passed": passed, "failed": failed, "pass_rate": pass_rate},
            )

        return ToolResult.ok(
            summary,
            {"passed": passed, "failed": failed, "pass_rate": pass_rate},
        )
