"""
tools/run_command.py — Let the model run code, but only inside a sandbox.

Without this the agent can read, search, edit and run the test suite, and
nothing else: it cannot run a script it just wrote, produce the output files a
data job asks for, or time code before and after an optimisation. Those jobs
need code execution.

Execution is never direct. The tool is constructed with a Sandbox
(scaffold/agent/sandbox.py) and does nothing but hand it the command, so the
confinement — writes limited to the project, no network — is the sandbox's
job, and a registry with no sandbox simply does not offer this tool.

The report is compact and tail-biased: a failing script puts its traceback at
the end of stderr, and a long-running job's final summary at the end of stdout.
"""

from __future__ import annotations

from typing import Any

try:
    from .base import Tool, ToolResult
except ImportError:  # direct execution / sys.path-style import
    from base import Tool, ToolResult

DEFAULT_TIMEOUT_SEC = 120
MAX_TIMEOUT_SEC = 600
#: Per-stream character budget. Two streams plus headers stay under the agent
#: loop's 8000-character result cap, which truncates from the end and would
#: otherwise cut exactly the tail this report keeps.
MAX_STREAM_CHARS = 3500


def _tail(text: str, limit: int = MAX_STREAM_CHARS) -> str:
    """Keep the end of `text`, saying how much was dropped from the front."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"[... {dropped} earlier characters truncated ...]\n{text[-limit:]}"


def format_report(exit_code: int, stdout: str, stderr: str, timed_out: bool) -> str:
    header = f"exit_code: {exit_code}" + (" (timed out)" if timed_out else "")
    return (
        f"{header}\n"
        f"--- stdout ---\n{_tail(stdout)}\n"
        f"--- stderr ---\n{_tail(stderr)}"
    )


class RunCommandTool(Tool):
    """Run a shell command in the project's sandboxed workspace."""

    def __init__(self, sandbox: Any) -> None:
        self.sandbox = sandbox

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        # SYSTEM_PROMPT is covered by cassette fingerprints and must not change,
        # so this description is where the model learns when to reach for it.
        return (
            "Run a shell command in this project's sandboxed workspace: run a "
            "script you wrote, generate output files, profile or time code, "
            "inspect data. The command runs with /bin/sh from the project root. "
            "Writes are confined to the project; there is no network. Returns "
            "the exit code plus the tail of stdout and stderr. Use run_tests "
            "for the test suite."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "command": "Shell command to run, e.g. 'python scripts/report.py'",
            "timeout_sec": (
                f"Optional time limit in seconds (default {DEFAULT_TIMEOUT_SEC}, "
                f"max {MAX_TIMEOUT_SEC})"
            ),
        }

    @property
    def input_schema(self) -> dict[str, Any]:
        schema = super().input_schema
        schema["properties"]["timeout_sec"]["type"] = "integer"
        return schema

    def validate(self, args: dict[str, Any]) -> list[str]:
        errors = []
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append("Missing required parameter: 'command'")
        timeout = args.get("timeout_sec")
        if timeout is not None:
            try:
                int(timeout)
            except (TypeError, ValueError):
                errors.append("timeout_sec must be an integer number of seconds")
        return errors

    @staticmethod
    def _timeout(raw: Any) -> int:
        if raw is None or raw == "":
            return DEFAULT_TIMEOUT_SEC
        return max(1, min(int(raw), MAX_TIMEOUT_SEC))

    def close(self) -> None:
        # The tool owns the sandbox it was handed: a docker backend holds a
        # running container until this is called (via ToolRegistry.close()).
        self.sandbox.close()

    def execute(self, args: dict[str, Any]) -> ToolResult:
        result = self.sandbox.run(args["command"], timeout_sec=self._timeout(args.get("timeout_sec")))
        data = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_sec": result.duration_sec,
        }
        text = format_report(result.exit_code, result.stdout, result.stderr, result.timed_out)
        success = result.exit_code == 0 and not result.timed_out
        # A failed command still carries its full report: the model needs the
        # traceback to fix its script, not just "failed".
        return ToolResult(success=success, text=text, data=data, error="" if success else text)
