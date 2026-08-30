"""
CI Discovery — Test failure discovery and parsing for CI rescue.

Runs pytest on a target repo, parses output to extract failing tests,
groups failures by source module, and returns structured failure list.

Cost: FREE (local execution).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
_DEFAULT_TIMEOUT_SEC = 120


@dataclass
class TestFailure:
    """A single failing test with its context."""
    __test__ = False  # suppress pytest collection warning

    test_file: str           # e.g., "tests/test_auth.py"
    test_name: str           # e.g., "test_login"
    module: str              # e.g., "auth.py" (inferred source module)
    error_msg: str           # e.g., "AssertionError: expected True, got False"
    line_number: Optional[int] = None  # line in test file where failure occurred
    error_type: str = ""     # e.g., "AssertionError", "TypeError"


@dataclass
class DiscoveryResult:
    """Result of CI failure discovery."""
    __test__ = False  # suppress pytest collection warning

    failures: List[TestFailure] = field(default_factory=list)
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    raw_output: str = ""
    timed_out: bool = False
    no_tests_found: bool = False
    test_framework: str = "pytest"
    # Set when pytest could not be run at all. Distinguishes "this repo is
    # clean" from "we never managed to look" — without it, an unrunnable
    # command reports zero failures and CI repair silently finds nothing.
    execution_error: Optional[str] = None

    @property
    def modules_affected(self) -> List[str]:
        """Unique list of source modules with failures."""
        return sorted(set(f.module for f in self.failures))

    @property
    def failure_count(self) -> int:
        return len(self.failures)


class CIDiscovery:
    """Discovers CI failures by running tests and parsing output."""
    __test__ = False  # suppress pytest collection warning

    def __init__(self, project_root: str = ".", timeout_sec: int = _DEFAULT_TIMEOUT_SEC):
        self.project_root = Path(project_root).resolve()
        self.timeout_sec = timeout_sec

    # ── Public API ────────────────────────────────────────────────────────

    def discover(self) -> DiscoveryResult:
        """
        Run pytest, parse failures, return structured result.

        Returns:
            DiscoveryResult with list of TestFailure objects.
        """
        # Run pytest with verbose output for detailed failure info
        result = self._run_pytest()

        if result.timed_out:
            return DiscoveryResult(
                raw_output=result.raw_output,
                timed_out=True,
            )

        if result.no_tests_found:
            return DiscoveryResult(
                raw_output=result.raw_output,
                no_tests_found=True,
            )

        if result.execution_error:
            return DiscoveryResult(
                raw_output=result.raw_output,
                execution_error=result.execution_error,
            )

        # Parse failures from pytest output
        failures = self._parse_failures(result.raw_output)

        return DiscoveryResult(
            failures=failures,
            total_tests=result.passed + result.failed + result.errors,
            passed=result.passed,
            failed=result.failed,
            errors=result.errors,
            raw_output=result.raw_output,
            test_framework="pytest",
        )

    # ── Pytest Execution ──────────────────────────────────────────────────

    def _run_pytest(self) -> _TestResult:
        """Run pytest with verbose output for detailed failure parsing."""
        # Use -v for verbose (shows each test name)
        # Use --tb=short for concise tracebacks
        # Use --no-header to skip pytest header
        interpreter = os.getenv("AWOS_TEST_PYTHON", "").strip() or sys.executable
        command = [
            interpreter, "-m", "pytest",
            "-v",              # verbose: show each test
            "--tb=short",      # short tracebacks
            "--no-header",     # skip header
            "-q",              # quiet summary at end
            "--color=no",      # output is parsed, not displayed
        ]
        # This output is parsed. A parent environment carrying FORCE_COLOR
        # wraps every result line in ANSI escapes, which the failure parser
        # cannot match — discovery then reports a broken repository as clean.
        env = {**os.environ, "PY_COLORS": "0", "NO_COLOR": "1", "FORCE_COLOR": "0"}

        logger.info("[ci_discovery] Running: %s", " ".join(command))

        try:
            result = subprocess.run(
                command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[ci_discovery] Timed out after %ds", self.timeout_sec)
            return _TestResult(
                raw_output=f"Test execution timed out after {self.timeout_sec}s.",
                timed_out=True,
            )
        except FileNotFoundError as e:
            logger.error("[ci_discovery] Command not found: %s", e)
            return _TestResult(
                raw_output=f"Command not found: {e}",
                execution_error=f"Command not found: {e}",
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = f"{stdout}\n{stderr}".strip()

        # Parse summary counts
        passed = self._extract_count(combined, "passed")
        failed = self._extract_count(combined, "failed")
        errors = self._extract_count(combined, "error")

        # Check for no tests
        if "no tests ran" in combined.lower() and passed + failed + errors == 0:
            return _TestResult(
                raw_output=combined,
                no_tests_found=True,
            )

        # Nonzero exit with no parseable test summary means pytest never ran
        # (missing module, import crash, usage error). Reporting that as zero
        # failures would tell CI repair the repository is clean.
        if (
            passed + failed + errors == 0
            and getattr(result, "returncode", 0) != 0
        ):
            logger.error(
                "[ci_discovery] pytest produced no test summary (exit=%s): %s",
                getattr(result, "returncode", None),
                combined[:200],
            )
            return _TestResult(
                raw_output=combined,
                execution_error=(
                    f"pytest produced no test summary (exit="
                    f"{getattr(result, 'returncode', None)})"
                ),
            )

        return _TestResult(
            passed=passed,
            failed=failed,
            errors=errors,
            raw_output=combined,
        )

    def _extract_count(self, output: str, keyword: str) -> int:
        """Extract count from pytest summary (e.g., '3 failed' → 3)."""
        # Handle both "3 failed" and "3 errors"
        pattern = rf"(\d+)\s+{keyword}"
        match = re.search(pattern, output, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    # ── Failure Parsing ───────────────────────────────────────────────────

    def _parse_failures(self, output: str) -> List[TestFailure]:
        """
        Parse pytest verbose output to extract individual test failures.

        Pytest -v output format:
            tests/test_auth.py::test_login PASSED
            tests/test_auth.py::test_session FAILED
            ...
            FAILED tests/test_auth.py::test_session - AssertionError: ...
        """
        failures = []

        # Pattern 1: "FAILED tests/test_auth.py::test_login - AssertionError: ..."
        # This is the short test summary at the end
        failed_pattern = re.compile(
            r"^FAILED\s+"
            r"(?P<test_file>[^\s:]+)::"
            r"(?P<test_name>[^\s]+)"
            r"(?:\s*-\s*(?P<error>.+))?",
            re.MULTILINE,
        )

        for match in failed_pattern.finditer(output):
            test_file = match.group("test_file")
            test_name = match.group("test_name")
            error_msg = match.group("error") or ""

            # Infer source module from test file name
            module = self._infer_module(test_file, test_name)

            # Extract error type
            error_type = self._extract_error_type(error_msg)

            # Try to extract line number from traceback
            line_number = self._extract_line_number(output, test_file, test_name)

            failures.append(TestFailure(
                test_file=test_file,
                test_name=test_name,
                module=module,
                error_msg=error_msg.strip(),
                line_number=line_number,
                error_type=error_type,
            ))

        # If no FAILED lines found, try alternative pattern
        if not failures:
            failures = self._parse_failures_alternative(output)

        return failures

    def _parse_failures_alternative(self, output: str) -> List[TestFailure]:
        """Alternative parser for different pytest output formats."""
        failures = []

        # Pattern 2: "tests/test_auth.py::test_login FAILED" (inline status)
        inline_pattern = re.compile(
            r"^(?P<test_file>[^\s:]+)::(?P<test_name>[^\s]+)\s+FAILED",
            re.MULTILINE,
        )

        for match in inline_pattern.finditer(output):
            test_file = match.group("test_file")
            test_name = match.group("test_name")
            module = self._infer_module(test_file, test_name)

            # Try to find error message in traceback section
            error_msg = self._find_error_for_test(output, test_file, test_name)

            failures.append(TestFailure(
                test_file=test_file,
                test_name=test_name,
                module=module,
                error_msg=error_msg,
                error_type=self._extract_error_type(error_msg),
            ))

        return failures

    def _infer_module(self, test_file: str, test_name: str) -> str:
        """
        Infer source module from test file name and test name.

        Examples:
            tests/test_auth.py, test_login → auth.py
            tests/test_billing.py, test_invoice → billing.py
            test_inventory.py, test_stock → inventory.py
        """
        # Extract module name from test file
        # tests/test_auth.py → auth
        # test_billing.py → billing
        test_path = Path(test_file)
        stem = test_path.stem  # e.g., "test_auth"

        # Remove "test_" prefix
        if stem.startswith("test_"):
            module_name = stem[5:]  # "auth"
        else:
            module_name = stem

        return f"{module_name}.py"

    def _extract_error_type(self, error_msg: str) -> str:
        """Extract error type from error message (e.g., 'AssertionError: ...' → 'AssertionError')."""
        if not error_msg:
            return ""
        # Match common error types
        match = re.match(r"^(\w+Error|\w+Exception):", error_msg)
        if match:
            return match.group(1)
        # Fallback: first word
        return error_msg.split(":")[0].split()[0] if error_msg else ""

    def _extract_line_number(self, output: str, test_file: str, test_name: str) -> Optional[int]:
        """Try to extract line number from traceback for a specific test."""
        # Look for pattern: "tests/test_auth.py:45: AssertionError"
        pattern = re.compile(
            rf"{re.escape(test_file)}:(\d+):.*{re.escape(test_name)}",
            re.MULTILINE,
        )
        match = pattern.search(output)
        if match:
            return int(match.group(1))
        return None

    def _find_error_for_test(self, output: str, test_file: str, test_name: str) -> str:
        """Find error message for a specific test in the output."""
        # Look for traceback section after the test
        # Pattern: "___ test_login ___\n...Error: message..."
        pattern = re.compile(
            rf"_{3,}\s*{re.escape(test_name)}\s*_{3,}\s*\n"
            r"(.*?)"
            r"(?=_{3,}|\Z)",
            re.DOTALL,
        )
        match = pattern.search(output)
        if match:
            traceback_text = match.group(1)
            # Extract last line (usually the error)
            lines = traceback_text.strip().split("\n")
            if lines:
                return lines[-1].strip()
        return ""


# ── Convenience Functions ─────────────────────────────────────────────────

def discover_ci_failures(repo_root: str) -> List[dict]:
    """
    Convenience function: discover failures and return as list of dicts.

    Returns:
        [
            {
                "module": "auth.py",
                "test_name": "test_login",
                "test_file": "tests/test_auth.py",
                "error_msg": "AssertionError: expected True, got False",
                "line_number": 45,
                "error_type": "AssertionError",
            },
            ...
        ]
    """
    discovery = CIDiscovery(repo_root)
    result = discovery.discover()

    return [
        {
            "module": f.module,
            "test_name": f.test_name,
            "test_file": f.test_file,
            "error_msg": f.error_msg,
            "line_number": f.line_number,
            "error_type": f.error_type,
        }
        for f in result.failures
    ]


def group_failures_by_module(failures: List[dict]) -> dict:
    """
    Group failures by source module.

    Returns:
        {
            "auth.py": [
                {"test_name": "test_login", "error_msg": "...", ...},
                ...
            ],
            "billing.py": [...],
        }
    """
    grouped = {}
    for f in failures:
        module = f["module"]
        if module not in grouped:
            grouped[module] = []
        grouped[module].append(f)
    return grouped


# ── Internal TestResult (simplified) ──────────────────────────────────────

@dataclass
class _TestResult:
    """Internal test result (simplified from test_runner.py)."""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    raw_output: str = ""
    timed_out: bool = False
    no_tests_found: bool = False
    execution_error: Optional[str] = None
