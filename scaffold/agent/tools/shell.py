"""
tools/shell.py — Shell command execution tool.

Safety model:
  - ALLOW_SHELL env var must be set to "1" / "true" to enable destructive commands.
  - Read-only commands (git status, ls, cat, echo, find, grep, python -c, etc.) are always allowed.
  - Write/destructive commands require ALLOW_SHELL=1 or explicit allow_destructive=true in args.
  - Commands are run with a configurable timeout (default: 30s).

Usage:
    result = ShellTool()(command="git log --oneline -10", cwd="/path/to/repo")
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from .base import Tool, ToolResult

# Commands / patterns that are always read-only safe
_SAFE_PATTERNS = re.compile(
    r"^\s*("
    r"git\s+(status|log|diff|show|branch|remote|fetch|ls-files|ls-tree|describe|rev-parse|shortlog|tag|stash list)|"
    r"ls(\s|$)|ll(\s|$)|dir(\s|$)|find(\s|$)|cat(\s|$)|head(\s|$)|tail(\s|$)|wc(\s|$)|"
    r"echo(\s|$)|printf(\s|$)|pwd(\s|$)|date(\s|$)|whoami(\s|$)|hostname(\s|$)|uname(\s|$)|"
    r"python3?\s+-c|python3?\s+-m\s+(pytest|unittest)|"
    r"grep(\s|$)|rg(\s|$)|ag(\s|$)|awk(\s|$)|sed\s+-n|"
    r"pip\s+(list|show|freeze)|"
    r"which(\s|$)|type(\s|$)|env(\s|$)|printenv(\s|$)|"
    r"curl\s+-s|wget\s+--quiet"
    r")",
    re.IGNORECASE,
)


class ShellTool(Tool):
    """Run a shell command and return stdout + stderr."""

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Execute a shell (bash) command and return its output. "
            "Safe read-only commands run freely. "
            "Destructive commands require ALLOW_SHELL=1 env var."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "command": "Shell command to execute",
            "cwd": "Working directory for the command (default: current directory)",
            "timeout": "Timeout in seconds (default: 30)",
            "allow_destructive": "Set to 'true' to bypass safety check (still requires ALLOW_SHELL=1)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "command" not in args or not args["command"].strip():
            return ["Missing required parameter: 'command'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        command = args["command"].strip()
        cwd = args.get("cwd", os.getcwd())
        timeout = int(args.get("timeout", 30))
        allow_destructive = str(args.get("allow_destructive", "false")).lower() in ("true", "1", "yes")

        # Safety gate
        if not self._is_safe(command) and not allow_destructive:
            env_ok = os.getenv("ALLOW_SHELL", "").lower() in ("1", "true", "yes")
            if not env_ok:
                return ToolResult.fail(
                    f"Blocked: command may be destructive. "
                    f"Set ALLOW_SHELL=1 env var or pass allow_destructive=true. "
                    f"Command was: {command!r}"
                )

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"Command timed out after {timeout}s: {command!r}")
        except Exception as e:
            return ToolResult.fail(f"Command execution failed: {e}")

        output = proc.stdout + (f"\n[stderr]\n{proc.stderr}" if proc.stderr.strip() else "")
        success = proc.returncode == 0

        if success:
            return ToolResult.ok(
                text=output or "(no output)",
                data={"command": command, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr},
            )
        else:
            return ToolResult(
                success=False,
                text=output,
                error=f"Command exited with code {proc.returncode}",
                data={"command": command, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr},
            )

    @staticmethod
    def _is_safe(command: str) -> bool:
        return bool(_SAFE_PATTERNS.match(command))
