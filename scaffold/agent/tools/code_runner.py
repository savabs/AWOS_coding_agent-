"""
tools/code_runner.py — Python code execution tool.

Runs Python code snippets in a subprocess for isolation (not a full sandbox,
but prevents crashes from killing the agent process).

Safety model:
  - Code runs in a subprocess with a configurable timeout.
  - ALLOW_CODE_EXEC env var must be "1" to enable (default: off).
  - Imports of dangerous modules (os.system, subprocess, shutil.rmtree, etc.)
    are warned about in the result but not blocked — the subprocess boundary
    provides the primary protection.

Usage:
    result = PythonRunnerTool()(code="import math; print(math.pi)")
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from typing import Any

from .base import Tool, ToolResult

_DANGEROUS_PATTERNS = re.compile(
    r"(os\.system|subprocess\.call|shutil\.rmtree|os\.remove|os\.unlink|"
    r"open\s*\(.*['\"]w['\"]|__import__\s*\(\s*['\"]os['\"])",
    re.IGNORECASE,
)


class PythonRunnerTool(Tool):
    """Execute a Python code snippet and return its stdout/stderr."""

    @property
    def name(self) -> str:
        return "run_python"

    @property
    def description(self) -> str:
        return (
            "Execute a Python code snippet in an isolated subprocess. "
            "Returns stdout and stderr. "
            "Requires ALLOW_CODE_EXEC=1 env var."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "code": "Python source code to execute",
            "timeout": "Timeout in seconds (default: 15)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "code" not in args or not args["code"].strip():
            return ["Missing required parameter: 'code'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        code = args["code"]
        timeout = int(args.get("timeout", 15))

        # Safety gate
        if os.getenv("ALLOW_CODE_EXEC", "").lower() not in ("1", "true", "yes"):
            return ToolResult.fail(
                "Code execution is disabled. Set ALLOW_CODE_EXEC=1 env var to enable."
            )

        # Warn about dangerous patterns (but still run — subprocess is the boundary)
        warnings = []
        if _DANGEROUS_PATTERNS.search(code):
            warnings.append("[WARNING] Code contains potentially dangerous operations.")

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmpfile = f.name

        try:
            proc = subprocess.run(
                [sys.executable, tmpfile],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"Code execution timed out after {timeout}s.")
        except Exception as e:
            return ToolResult.fail(f"Execution failed: {e}")
        finally:
            try:
                os.unlink(tmpfile)
            except OSError:
                pass

        output_parts = []
        if warnings:
            output_parts.extend(warnings)
        if proc.stdout.strip():
            output_parts.append(proc.stdout)
        if proc.stderr.strip():
            output_parts.append(f"[stderr]\n{proc.stderr}")

        output = "\n".join(output_parts) or "(no output)"
        success = proc.returncode == 0

        if success:
            return ToolResult.ok(
                text=output,
                data={"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr},
            )
        else:
            return ToolResult(
                success=False,
                text=output,
                error=f"Exited with code {proc.returncode}",
                data={"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr},
            )
