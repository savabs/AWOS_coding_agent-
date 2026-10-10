"""
coding_tools.py — Workspace-scoped tool registry for the ReAct coding worker.

Wraps filesystem/shell tools with project-root confinement and adds:
  - edit_file      SEARCH/REPLACE or create-new
  - run_tests      TestRunner gate (requires AWOS_SAFE_TO_RUN_TESTS=1)
  - web_search     Internet search (Tavily / DDG / SerpAPI)
  - fetch_url      Read documentation pages from URLs
  - github_*       GitHub search, read file, list issues, search code
  - hf_*           HuggingFace model/dataset search and scoped downloads
  - run_python     Isolated Python snippet execution (ALLOW_CODE_EXEC=1)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from .test_runner import TestRunner
from .tools.base import Tool, ToolRegistry, ToolResult
from .tools.code_runner import PythonRunnerTool
from .tools.fetch_url import FetchURLTool
from .tools.filesystem import (
    FindFilesTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from .tools.github_tool import (
    GitHubCreateIssueTool,
    GitHubListIssuesTool,
    GitHubReadFileTool,
    GitHubSearchCodeTool,
    GitHubSearchReposTool,
)
from .tools.huggingface_tool import (
    HFDownloadFileTool,
    HFModelInfoTool,
    HFSearchDatasetsTool,
    HFSearchModelsTool,
)
from .tools.shell import ShellTool
from .tools.web_search import WebSearchTool


def _truthy(val: str | None) -> bool:
    return (val or "").lower() in ("1", "true", "yes")


def research_tools_enabled() -> bool:
    """Internet research tools (web_search, fetch_url). Default ON."""
    return os.getenv("AWOS_ENABLE_WEB_TOOLS", "1").lower() in ("1", "true", "yes")


def github_tools_enabled() -> bool:
    """GitHub API tools. Default ON (read-only; write gated separately)."""
    return os.getenv("AWOS_ENABLE_GITHUB_TOOLS", "1").lower() in ("1", "true", "yes")


def github_write_enabled() -> bool:
    """Allow github_create_issue (requires GITHUB_TOKEN with write access). Default OFF."""
    return os.getenv("AWOS_ALLOW_GITHUB_WRITE", "").lower() in ("1", "true", "yes")


def hf_tools_enabled() -> bool:
    """HuggingFace Hub tools. Default ON."""
    return os.getenv("AWOS_ENABLE_HF_TOOLS", "1").lower() in ("1", "true", "yes")


def code_runner_enabled() -> bool:
    """Expose run_python in tool catalog. Execution still requires ALLOW_CODE_EXEC=1."""
    return os.getenv("AWOS_ENABLE_CODE_RUNNER", "1").lower() in ("1", "true", "yes")


class WorkspacePathMixin:
    """Resolve paths relative to project_root and block escape."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _resolve(self, path: str) -> Path:
        raw = (path or "").strip()
        if not raw:
            raise ValueError("Empty path")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve()
        root = str(self.project_root)
        if resolved != self.project_root and not str(resolved).startswith(root + os.sep):
            raise ValueError(f"Path escapes workspace: {path}")
        return resolved


class WorkspaceReadFileTool(ReadFileTool, WorkspacePathMixin):
    def __init__(self, project_root: Path) -> None:
        WorkspacePathMixin.__init__(self, project_root)

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            path = self._resolve(args["path"])
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        return super().execute({**args, "path": str(path)})


class WorkspaceWriteFileTool(WriteFileTool, WorkspacePathMixin):
    def __init__(self, project_root: Path, on_change: Optional[Callable[[str], None]] = None) -> None:
        WorkspacePathMixin.__init__(self, project_root)
        self._on_change = on_change

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            path = self._resolve(args["path"])
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        result = super().execute({**args, "path": str(path)})
        if result.success and self._on_change:
            self._on_change(str(path.relative_to(self.project_root)))
        return result


class WorkspaceGrepTool(GrepTool, WorkspacePathMixin):
    def __init__(self, project_root: Path) -> None:
        WorkspacePathMixin.__init__(self, project_root)

    def execute(self, args: dict[str, Any]) -> ToolResult:
        root = args.get("root", ".")
        try:
            resolved_root = self._resolve(root) if root != "." else self.project_root
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        return super().execute({**args, "root": str(resolved_root)})


class WorkspaceFindFilesTool(FindFilesTool, WorkspacePathMixin):
    def __init__(self, project_root: Path) -> None:
        WorkspacePathMixin.__init__(self, project_root)

    def execute(self, args: dict[str, Any]) -> ToolResult:
        root = args.get("root", ".")
        try:
            resolved_root = self._resolve(root) if root != "." else self.project_root
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        return super().execute({**args, "root": str(resolved_root)})


class WorkspaceListDirTool(ListDirTool, WorkspacePathMixin):
    def __init__(self, project_root: Path) -> None:
        WorkspacePathMixin.__init__(self, project_root)

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path = args.get("path", ".")
        try:
            resolved = self._resolve(path) if path != "." else self.project_root
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        return super().execute({**args, "path": str(resolved)})


class WorkspaceShellTool(ShellTool, WorkspacePathMixin):
    def __init__(self, project_root: Path) -> None:
        WorkspacePathMixin.__init__(self, project_root)

    def execute(self, args: dict[str, Any]) -> ToolResult:
        cwd = args.get("cwd") or str(self.project_root)
        try:
            resolved_cwd = self._resolve(cwd)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        return super().execute({**args, "cwd": str(resolved_cwd)})


class EditFileTool(Tool, WorkspacePathMixin):
    """Apply a SEARCH/REPLACE edit or create a new file (empty old_string)."""

    def __init__(self, project_root: Path, on_change: Optional[Callable[[str], None]] = None) -> None:
        WorkspacePathMixin.__init__(self, project_root)
        self._on_change = on_change

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file using exact SEARCH/REPLACE. "
            "Use empty old_string to create a new file with new_string content."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "path": "Relative path to file within the project",
            "old_string": "Exact text to find (empty to create new file)",
            "new_string": "Replacement text",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "path" not in args or not str(args.get("path", "")).strip():
            return ["Missing required parameter: 'path'"]
        if "new_string" not in args:
            return ["Missing required parameter: 'new_string'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            path = self._resolve(args["path"])
        except ValueError as exc:
            return ToolResult.fail(str(exc))

        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_string, encoding="utf-8")
        elif old_string.strip():
            content = path.read_text(encoding="utf-8")
            if old_string not in content:
                return ToolResult.fail(f"SEARCH text not found in {path}")
            if content.count(old_string) > 1:
                return ToolResult.fail(f"SEARCH text matches multiple locations in {path}")
            path.write_text(content.replace(old_string, new_string), encoding="utf-8")
        else:
            path.write_text(new_string, encoding="utf-8")

        if path.suffix == ".py":
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                return ToolResult.fail(f"Edit produced invalid Python: {exc}")

        rel = str(path.relative_to(self.project_root))
        if self._on_change:
            self._on_change(rel)

        return ToolResult.ok(
            text=f"Applied edit to {rel}",
            data={"path": rel, "created": not bool(old_string.strip())},
        )


class RunTestsTool(Tool, WorkspacePathMixin):
    """Run project tests via TestRunner (requires AWOS_SAFE_TO_RUN_TESTS=1)."""

    def __init__(self, project_root: Path) -> None:
        WorkspacePathMixin.__init__(self, project_root)
        self._runner = TestRunner(project_root=str(project_root))

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Run the project's test suite. "
            "Requires AWOS_SAFE_TO_RUN_TESTS=1. Returns pass/fail counts."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "reason": "Why you are running tests (optional, for logging)",
            "paths": "Optional comma-separated test files/dirs (e.g. 'test_case.py')",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if not _truthy(os.environ.get("AWOS_SAFE_TO_RUN_TESTS")):
            return ToolResult.fail(
                "Tests blocked — set AWOS_SAFE_TO_RUN_TESTS=1 to enable run_tests tool."
            )
        result = self._runner.run()
        paths = str(args.get("paths", "")).strip()
        if paths:
            import subprocess
            parts = [p.strip() for p in paths.split(",") if p.strip()]
            cmd = ["pytest", "--tb=short", "-q", *parts]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True,
                    timeout=self._runner.timeout_sec,
                )
            except subprocess.TimeoutExpired:
                return ToolResult.fail("Tests timed out.")
            result = self._runner._parse_pytest(proc.stdout or "", proc.stderr or "", cmd)
        summary = (
            f"Tests: {result.passed} passed, {result.failed} failed, "
            f"{result.errors} errors (pass_rate={result.pass_rate:.0%})"
        )
        if result.no_tests_found:
            return ToolResult.ok(
                text=result.raw_output or "No tests found.",
                data={"pass_rate": 0.0, "no_tests_found": True},
            )
        if result.timed_out:
            return ToolResult.fail(f"Tests timed out.\n{result.raw_output[:2000]}")

        ok = result.pass_rate >= 1.0 and result.failed == 0 and result.errors == 0
        payload = {
            "passed": result.passed,
            "failed": result.failed,
            "errors": result.errors,
            "pass_rate": result.pass_rate,
            "raw_output": result.raw_output[:4000],
        }
        if ok:
            return ToolResult.ok(text=summary, data=payload)
        return ToolResult(
            success=False,
            text=summary + "\n\n" + result.raw_output[:3000],
            error=f"{result.failed} failed, {result.errors} errors",
            data=payload,
        )


class WorkspaceHFDownloadFileTool(HFDownloadFileTool, WorkspacePathMixin):
    """HF download with save_to confined to project workspace."""

    def __init__(self, project_root: Path, on_change: Optional[Callable[[str], None]] = None) -> None:
        WorkspacePathMixin.__init__(self, project_root)
        self._on_change = on_change

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            save_path = self._resolve(args["save_to"])
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        result = super().execute({**args, "save_to": str(save_path)})
        if result.success and self._on_change:
            try:
                rel = str(save_path.relative_to(self.project_root))
                self._on_change(rel)
            except ValueError:
                pass
        return result


class FinishTool(Tool):
    """Sentinel tool — model signals task completion."""

    @property
    def name(self) -> str:
        return "finish"

    @property
    def description(self) -> str:
        return "Call when the task is complete. Provide a short summary."

    @property
    def parameters(self) -> dict[str, str]:
        return {"summary": "What was accomplished"}

    def execute(self, args: dict[str, Any]) -> ToolResult:
        summary = str(args.get("summary", "Task complete")).strip()
        return ToolResult.ok(text=summary, data={"finished": True, "summary": summary})


def build_coding_tool_registry(
    project_root: str | Path,
    on_file_change: Optional[Callable[[str], None]] = None,
) -> ToolRegistry:
    """Build a workspace-scoped tool registry for ReAct coding."""
    root = Path(project_root).resolve()
    registry = ToolRegistry()
    # Shell FIRST — bash-only editing (sed, python -c, cat)
    registry.register(WorkspaceShellTool(root))
    registry.register(WorkspaceReadFileTool(root))
    registry.register(WorkspaceWriteFileTool(root, on_change=on_file_change))
    registry.register(EditFileTool(root, on_change=on_file_change))
    registry.register(WorkspaceGrepTool(root))
    registry.register(WorkspaceFindFilesTool(root))
    registry.register(WorkspaceListDirTool(root))
    registry.register(RunTestsTool(root))
    if research_tools_enabled():
        registry.register(WebSearchTool())
        registry.register(FetchURLTool())
    if github_tools_enabled():
        registry.register(GitHubSearchReposTool())
        registry.register(GitHubReadFileTool())
        registry.register(GitHubListIssuesTool())
        registry.register(GitHubSearchCodeTool())
        if github_write_enabled():
            registry.register(GitHubCreateIssueTool())
    if hf_tools_enabled():
        registry.register(HFSearchModelsTool())
        registry.register(HFSearchDatasetsTool())
        registry.register(HFModelInfoTool())
        registry.register(WorkspaceHFDownloadFileTool(root, on_change=on_file_change))
    if code_runner_enabled():
        registry.register(PythonRunnerTool())
    registry.register(FinishTool())
    return registry


def format_tool_catalog(registry: ToolRegistry) -> str:
    """Human-readable tool list for ReAct system prompt."""
    lines = []
    for tool in registry.list_tools():
        if tool["name"] == "finish":
            continue
        params = ", ".join(f"{k}: {v}" for k, v in tool["parameters"].items())
        lines.append(f"- {tool['name']}({params}) — {tool['description']}")
    lines.append("- finish(summary) — call when done")
    return "\n".join(lines)
