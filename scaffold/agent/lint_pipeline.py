"""
lint_pipeline.py — LSP/Ruff feedback loop for AWOS (P3.1).

After every file write, runs fast linting (ruff) and optional type checking
(pyright) to emit structured diagnostics that feed back into the retry prompt.

Research basis:
    Claude Code LSP registry analysis — after every file write, diagnostics
    are emitted instantly. Agents that see these fix type errors in 1-2 turns
    vs 5+ without LSP feedback.

Levels:
    Level 1: Ruff (always available, free, ~80% coverage)
    Level 2: Pyright (type checking, optional — requires 'pyright' on PATH)

Usage:
    pipeline = DiagnosticPipeline(use_pyright=False)
    diags = pipeline.check(file_path, new_content, project_root)
    if diags:
        critique = pipeline.format_for_prompt(diags)
        task["past_critiques"].append(critique)
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Ruff error codes that block the write (not just warnings)
BLOCKING_CODES = {"E999", "F821", "F811"}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class LintDiagnostic:
    """One structured lint / type diagnostic."""
    file: str
    line: int
    col: int
    code: str        # e.g. "E501", "F401", "E303"
    message: str
    severity: str    # "error" | "warning"


# ── Level 1: Ruff ─────────────────────────────────────────────────────────────

class RuffLinter:
    """Fast linting via ruff subprocess. Returns structured diagnostics."""

    MAX_DIAGNOSTICS = 15  # Match Claude Code's limit

    def lint_file(self, file_path: str) -> List[LintDiagnostic]:
        """Run ruff on a file and return structured diagnostics. Silent fallback if ruff missing."""
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", "--quiet", file_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not result.stdout.strip():
                return []
            raw = json.loads(result.stdout)
            diagnostics: List[LintDiagnostic] = []
            for item in raw[: self.MAX_DIAGNOSTICS]:
                code = item.get("code", "?")
                diagnostics.append(LintDiagnostic(
                    file=item.get("filename", file_path),
                    line=item.get("location", {}).get("row", 0),
                    col=item.get("location", {}).get("column", 0),
                    code=code,
                    message=item.get("message", ""),
                    severity="error" if code[:1] in ("E", "F") else "warning",
                ))
            return diagnostics
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, Exception):
            return []

    def ruff_autofix(self, content: str) -> str:
        """
        Apply ruff auto-fixes to content.
        Returns fixed content; falls back to original on error or timeout.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["ruff", "check", "--fix", "--quiet", tmp_path],
                capture_output=True, timeout=5,
            )
            return Path(tmp_path).read_text(encoding="utf-8", errors="ignore")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return content
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def format_for_prompt(self, diagnostics: List[LintDiagnostic]) -> str:
        """Format diagnostics for injection into the retry prompt (Block C)."""
        if not diagnostics:
            return ""
        lines = ["## Linting Diagnostics (ruff):"]
        for d in diagnostics:
            lines.append(f"  Line {d.line}:{d.col} [{d.code}] {d.message}")
        lines.append("")
        lines.append("Fix these issues in your edit.")
        return "\n".join(lines)


# ── Level 2: Pyright ──────────────────────────────────────────────────────────

class PyrightChecker:
    """Type checking via pyright. More thorough but requires pyright on PATH."""

    def check_file(self, file_path: str, project_root: str) -> List[LintDiagnostic]:
        """Run pyright on file_path. Returns [] if pyright not installed."""
        try:
            result = subprocess.run(
                ["pyright", "--outputjson", file_path],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=project_root,
            )
            data = json.loads(result.stdout)
            diagnostics: List[LintDiagnostic] = []
            for diag in data.get("generalDiagnostics", [])[:10]:
                if diag.get("severity") in ("error", "warning"):
                    diagnostics.append(LintDiagnostic(
                        file=diag.get("file", file_path),
                        line=diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                        col=diag.get("range", {}).get("start", {}).get("character", 0),
                        code=f"PY{diag.get('rule', 'type')}",
                        message=diag.get("message", ""),
                        severity=diag.get("severity", "warning"),
                    ))
            return diagnostics
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, Exception):
            return []


# ── Unified pipeline ──────────────────────────────────────────────────────────

class DiagnosticPipeline:
    """
    Runs ruff (always) + optional pyright on in-memory content.
    Writes to a temp file for analysis — does NOT write to the target path.

    Integration in orchestrator._execute_single_task (P3.1):
        After syntax OK, before writing to disk:
            diags = pipeline.check(task["file"], new_content, project_root)
            blocking = [d for d in diags if d.code in BLOCKING_CODES]
            if blocking:
                task["past_critiques"].append(pipeline.format_for_prompt(blocking))
                continue  # retry
    """

    def __init__(self, use_pyright: bool = False) -> None:
        self.ruff = RuffLinter()
        self.pyright = PyrightChecker() if use_pyright else None

    def check(
        self,
        file_path: str,
        content: str,
        project_root: str,
    ) -> List[LintDiagnostic]:
        """
        Write content to a temp file in the same dir as file_path, run checks.
        Returns combined diagnostic list. The actual file is NOT modified.
        """
        target = Path(file_path)
        tmp_dir = target.parent if target.parent.exists() else Path(project_root)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, dir=str(tmp_dir)
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            diagnostics: List[LintDiagnostic] = []
            diagnostics.extend(self.ruff.lint_file(tmp_path))
            if self.pyright:
                diagnostics.extend(self.pyright.check_file(tmp_path, project_root))
            return diagnostics
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def has_blocking_errors(self, diagnostics: List[LintDiagnostic]) -> bool:
        """Return True if any diagnostic has a blocking error code."""
        return any(d.code in BLOCKING_CODES for d in diagnostics)

    def format_for_prompt(self, diagnostics: List[LintDiagnostic]) -> str:
        """Delegate to RuffLinter.format_for_prompt."""
        return self.ruff.format_for_prompt(diagnostics)
