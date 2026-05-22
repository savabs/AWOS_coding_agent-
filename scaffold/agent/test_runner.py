"""
TestRunner — Language-agnostic test executor.

Auto-detects the project's test framework, runs tests after every code patch,
and returns a pass_rate that feeds into the LinUCB reward signal.

Safety: AWOS only runs tests when AWOS_SAFE_TO_RUN_TESTS=1 is set.
This prevents accidental destructive side effects on production test suites.

Cost: FREE (local execution).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
_DEFAULT_TIMEOUT_SEC = 60
_SAFETY_ENV_VAR = "AWOS_SAFE_TO_RUN_TESTS"


@dataclass
class TestConfig:
    """Detected test framework configuration for a project."""
    __test__ = False  # suppress pytest collection warning

    runner: str        # "pytest" | "jest" | "vitest" | "make" | "go" | "cargo" | "generic"
    command: List[str] # shell command tokens
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC


@dataclass
class TestResult:
    """Result of a single test execution."""
    __test__ = False  # suppress pytest collection warning

    passed: int = 0
    failed: int = 0
    errors: int = 0
    pass_rate: float = 0.0       # 0.0–1.0
    raw_output: str = ""         # stdout + stderr
    timed_out: bool = False
    test_command: List[str] = field(default_factory=list)
    no_tests_found: bool = False


class TestRunner:
    """Language-agnostic test executor with auto-detection."""
    __test__ = False  # suppress pytest collection warning

    def __init__(self, project_root: str = ".", timeout_sec: int = _DEFAULT_TIMEOUT_SEC):
        self.project_root = Path(project_root)
        self.timeout_sec = timeout_sec

    # ── Public API ────────────────────────────────────────────────────────

    def detect(self) -> Optional[TestConfig]:
        """Inspect project_root and return a TestConfig, or None."""
        # Try each detector in order of specificity
        for detector in [
            self._detect_pytest,
            self._detect_jest,
            self._detect_vitest,
            self._detect_go,
            self._detect_cargo,
            self._detect_make,
        ]:
            cfg = detector()
            if cfg is not None:
                return cfg
        return None

    def run(self, changed_files: Optional[List[str]] = None) -> TestResult:
        """Run tests using detected config."""
        # ── Safety guard ────────────────────────────────────────────────
        if os.environ.get(_SAFETY_ENV_VAR) != "1":
            logger.info(
                "[test_runner] Safety guard active — set %s=1 to enable test execution",
                _SAFETY_ENV_VAR,
            )
            return TestResult(
                raw_output=f"Tests skipped — set {_SAFETY_ENV_VAR}=1 to enable.",
                no_tests_found=True,
            )

        cfg = self.detect()
        if cfg is None:
            logger.info("[test_runner] No test framework detected")
            return TestResult(raw_output="No test framework detected.", no_tests_found=True)

        return self._execute(cfg)

    # ── Detection helpers ───────────────────────────────────────────────

    def _detect_pytest(self) -> Optional[TestConfig]:
        """Detect pytest via pytest.ini, pyproject.toml, or setup.cfg."""
        root = self.project_root
        if (root / "pytest.ini").exists():
            return TestConfig("pytest", ["pytest", "--tb=short", "-q"])

        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.pytest" in content:
                return TestConfig("pytest", ["pytest", "--tb=short", "-q"])

        setup_cfg = root / "setup.cfg"
        if setup_cfg.exists():
            content = setup_cfg.read_text(encoding="utf-8")
            if "[tool:pytest]" in content:
                return TestConfig("pytest", ["pytest", "--tb=short", "-q"])

        return None

    def _detect_jest(self) -> Optional[TestConfig]:
        """Detect Jest via package.json dependency."""
        pkg = self.project_root / "package.json"
        if not pkg.exists():
            return None
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            if "jest" in deps or "jest" in dev_deps:
                return TestConfig("jest", ["npx", "jest", "--passWithNoTests"])
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _detect_vitest(self) -> Optional[TestConfig]:
        """Detect Vitest via package.json dependency."""
        pkg = self.project_root / "package.json"
        if not pkg.exists():
            return None
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            if "vitest" in deps or "vitest" in dev_deps:
                return TestConfig("vitest", ["npx", "vitest", "run"])
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _detect_go(self) -> Optional[TestConfig]:
        """Detect Go module via go.mod."""
        if (self.project_root / "go.mod").exists():
            return TestConfig("go", ["go", "test", "./..."])
        return None

    def _detect_cargo(self) -> Optional[TestConfig]:
        """Detect Rust via Cargo.toml."""
        if (self.project_root / "Cargo.toml").exists():
            return TestConfig("cargo", ["cargo", "test"])
        return None

    def _detect_make(self) -> Optional[TestConfig]:
        """Detect Make via Makefile with test target."""
        makefile = self.project_root / "Makefile"
        if makefile.exists():
            content = makefile.read_text(encoding="utf-8")
            if re.search(r"^[\w\-]*test\s*:", content, re.MULTILINE):
                return TestConfig("make", ["make", "test"])
        return None

    # ── Execution ───────────────────────────────────────────────────────

    def _execute(self, cfg: TestConfig) -> TestResult:
        """Run the configured test command and parse output."""
        logger.info("[test_runner] Running: %s (timeout=%ds)", " ".join(cfg.command), cfg.timeout_sec)
        try:
            result = subprocess.run(
                cfg.command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=cfg.timeout_sec,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[test_runner] Timed out after %ds", cfg.timeout_sec)
            return TestResult(
                timed_out=True,
                raw_output=f"Test execution timed out after {cfg.timeout_sec}s.",
                test_command=cfg.command,
            )
        except FileNotFoundError as e:
            logger.error("[test_runner] Command not found: %s", e)
            return TestResult(
                raw_output=f"Command not found: {e}",
                test_command=cfg.command,
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = f"{stdout}\n{stderr}".strip()

        # Dispatch to parser
        if cfg.runner == "pytest":
            return self._parse_pytest(stdout, stderr, cfg.command)
        elif cfg.runner == "jest":
            return self._parse_jest(stdout, stderr, cfg.command)
        elif cfg.runner == "vitest":
            return self._parse_vitest(stdout, stderr, cfg.command)
        else:
            return self._parse_generic(stdout, stderr, cfg.command)

    # ── Parsers ─────────────────────────────────────────────────────────

    def _parse_pytest(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Parse pytest --tb=short -q output."""
        combined = f"{stdout}\n{stderr}"

        # pytest outputs summary lines like:
        #   "3 passed, 1 failed, 2 errors in 0.12s"
        #   "3 passed in 0.12s"
        #   "1 failed, 1 passed in 0.12s"  ← order varies!
        #   "no tests ran in 0.01s"
        # Search each component independently so order doesn't matter.
        summary_match = re.search(r"in\s+[\d.]+s", combined)
        if summary_match or "no tests ran" in combined.lower():
            passed_match = re.search(r"(\d+)\s+passed", combined, re.IGNORECASE)
            failed_match = re.search(r"(\d+)\s+failed", combined, re.IGNORECASE)
            errors_match = re.search(r"(\d+)\s+errors?", combined, re.IGNORECASE)

            passed = int(passed_match.group(1)) if passed_match else 0
            failed = int(failed_match.group(1)) if failed_match else 0
            errors = int(errors_match.group(1)) if errors_match else 0
            total = passed + failed + errors

            if "no tests ran" in combined.lower() and total == 0:
                return TestResult(
                    raw_output=combined,
                    test_command=command,
                    no_tests_found=True,
                )

            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=errors,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        # Collection errors or other pytest errors
        if "error" in combined.lower():
            return TestResult(
                raw_output=combined,
                test_command=command,
            )

        # Fallback: generic regex
        return self._parse_generic(stdout, stderr, command)

    def _parse_jest(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Parse jest output."""
        combined = f"{stdout}\n{stderr}"

        # "Tests: 2 passed, 1 failed, 3 total"
        test_match = re.search(
            r"Tests:\s*(?:(\d+)\s+passed)?(?:,\s*)?(?:(\d+)\s+failed)?(?:,\s*)?(?:(\d+)\s+total)?",
            combined,
        )
        if test_match:
            passed = int(test_match.group(1) or 0)
            failed = int(test_match.group(2) or 0)
            errors = 0
            total = passed + failed + errors
            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=errors,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        # "Test Suites: 2 passed, 1 failed"
        suite_match = re.search(
            r"Test Suites:\s*(?:(\d+)\s+passed)?(?:,\s*)?(?:(\d+)\s+failed)?",
            combined,
        )
        if suite_match:
            passed = int(suite_match.group(1) or 0)
            failed = int(suite_match.group(2) or 0)
            total = passed + failed
            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=0,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        return self._parse_generic(stdout, stderr, command)

    def _parse_vitest(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Parse vitest output."""
        combined = f"{stdout}\n{stderr}"

        # "Tests  3 passed | 1 failed | 4 total"
        match = re.search(
            r"Tests\s+(?:(\d+)\s+passed)?(?:\s*\|\s*)?(?:(\d+)\s+failed)?",
            combined,
        )
        if match:
            passed = int(match.group(1) or 0)
            failed = int(match.group(2) or 0)
            total = passed + failed
            pass_rate = passed / total if total > 0 else 0.0
            return TestResult(
                passed=passed,
                failed=failed,
                errors=0,
                pass_rate=round(pass_rate, 4),
                raw_output=combined,
                test_command=command,
            )

        return self._parse_generic(stdout, stderr, command)

    def _parse_generic(self, stdout: str, stderr: str, command: List[str]) -> TestResult:
        """Fallback parser — regex for passed / failed / errors."""
        combined = f"{stdout}\n{stderr}"

        passed_match = re.search(r"(\d+)\s+passed", combined, re.IGNORECASE)
        failed_match = re.search(r"(\d+)\s+failed", combined, re.IGNORECASE)
        errors_match = re.search(r"(\d+)\s+errors?", combined, re.IGNORECASE)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0
        total = passed + failed + errors

        pass_rate = passed / total if total > 0 else 0.0

        return TestResult(
            passed=passed,
            failed=failed,
            errors=errors,
            pass_rate=round(pass_rate, 4),
            raw_output=combined,
            test_command=command,
        )
