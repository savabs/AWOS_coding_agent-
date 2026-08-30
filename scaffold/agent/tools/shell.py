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
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult

# Commands / patterns that are always read-only safe
# The complete command must match; prefix-only matching would allow a safe
# command followed by a destructive shell expression.
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
    r")[^;&|<>`\n\r]*$",
    re.IGNORECASE,
)

# These operators change the command graph or redirect data. Keep them behind
# the explicit ALLOW_SHELL/allow_destructive escape hatch.
_UNSAFE_SYNTAX = re.compile(r"[;&|<>`\n\r]|\$\(|\$\{", re.IGNORECASE)


@dataclass(frozen=True)
class BoundedCommandResult:
    """Result of a process-group-supervised command."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_limited: bool = False


def _truncate_output(value: bytes, max_output_bytes: int) -> tuple[str, bool]:
    limited = len(value) > max_output_bytes
    return value[:max_output_bytes].decode("utf-8", errors="replace"), limited


def run_bounded_command(
    command: str,
    cwd: str | Path,
    timeout_seconds: float,
    max_output_bytes: int = 1_048_576,
    env: dict[str, str] | None = None,
) -> BoundedCommandResult:
    """Run a command in its own process group with bounded result output."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")

    process = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()

    bounded_stdout, stdout_limited = _truncate_output(stdout, max_output_bytes)
    bounded_stderr, stderr_limited = _truncate_output(stderr, max_output_bytes)
    return BoundedCommandResult(
        returncode=None if timed_out else process.returncode,
        stdout=bounded_stdout,
        stderr=bounded_stderr,
        timed_out=timed_out,
        output_limited=stdout_limited or stderr_limited,
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
        timeout = float(args.get("timeout", 30))
        max_output_bytes = int(args.get("max_output_bytes", 1_048_576))
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
            result = run_bounded_command(
                command,
                cwd=cwd,
                timeout_seconds=timeout,
                max_output_bytes=max_output_bytes,
            )
        except (OSError, ValueError) as exc:
            return ToolResult.fail(f"Command execution failed: {exc}")

        output = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr.strip() else "")
        if result.timed_out:
            return ToolResult(
                success=False,
                text=output,
                error=f"Command timed out after {timeout}s: {command!r}",
                data={"command": command, "returncode": None, "stdout": result.stdout, "stderr": result.stderr, "timed_out": True},
            )

        success = result.returncode == 0
        data = {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "output_limited": result.output_limited,
        }
        if success:
            return ToolResult.ok(text=output or "(no output)", data=data)
        return ToolResult(
            success=False,
            text=output,
            error=f"Command exited with code {result.returncode}",
            data=data,
        )

    @staticmethod
    def _is_safe(command: str) -> bool:
        if _UNSAFE_SYNTAX.search(command):
            return False
        return bool(_SAFE_PATTERNS.fullmatch(command))
