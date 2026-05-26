"""
StabilityGate — automatically detect whether the test suite is stable enough
to allow ScaffoldEvolver to mutate scaffold code.

Algorithm:
    1. Run pytest `runs` times (default 3)
    2. Record pass count each run
    3. STABLE if:
         - All pass counts >= min_pass  (default 50)
         - Max variance across runs <= variance_tolerance (default 2 tests)
    4. Write result into .env as AWOS_SAFE_TO_RUN_TESTS=true/false
    5. Also update os.environ so the change is live immediately

Called from Orchestrator every `check_every` sessions (default 20).
Can also be run standalone: python3 -m scaffold.agent.stability_gate
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_MIN_PASS_COUNT       = 50
_MAX_VARIANCE         = 2
_DEFAULT_RUNS         = 3
_DEFAULT_CHECK_EVERY  = 20
_TEST_TIMEOUT_SECS    = 360
_ENV_KEY              = "AWOS_SAFE_TO_RUN_TESTS"
_SCAFFOLD_KEY         = "AWOS_SCAFFOLD_EVOLUTION"


class StabilityGate:
    """
    Probes the test suite for flakiness and writes AWOS_SAFE_TO_RUN_TESTS
    into .env automatically.
    """

    def __init__(
        self,
        project_root: str = ".",
        env_file: str = ".env",
        runs: int = _DEFAULT_RUNS,
        min_pass: int = _MIN_PASS_COUNT,
        variance_tolerance: int = _MAX_VARIANCE,
        test_cmd: Optional[List[str]] = None,
        check_every: int = _DEFAULT_CHECK_EVERY,
    ) -> None:
        self._root             = Path(project_root)
        self._env_file         = self._root / env_file
        self._runs             = runs
        self._min_pass         = min_pass
        self._variance         = variance_tolerance
        self._test_cmd         = test_cmd or [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"]
        self.check_every       = check_every
        self._last_checked_at  = 0   # session counter

    # ── Public API ──────────────────────────────────────────────────────────

    def should_check(self, session_count: int) -> bool:
        """True every `check_every` sessions."""
        return session_count > 0 and (session_count % self.check_every == 0)

    def run(self, verbose: bool = True) -> Tuple[bool, str]:
        """
        Run the stability probe and update .env + os.environ.

        Returns:
            (is_stable, reason_message)
        """
        if verbose:
            logger.info("[StabilityGate] probing test suite (%d runs)…", self._runs)

        counts, errors = self._collect_runs(verbose)

        if errors:
            reason = f"Test runner failed: {errors[0]}"
            self._write_gate(stable=False, reason=reason)
            return False, reason

        if not counts:
            reason = "No test results collected"
            self._write_gate(stable=False, reason=reason)
            return False, reason

        min_count = min(counts)
        max_count = max(counts)
        variance  = max_count - min_count

        if min_count < self._min_pass:
            reason = f"Pass count too low: {min_count} < {self._min_pass} required"
            self._write_gate(stable=False, reason=reason)
            return False, reason

        if variance > self._variance:
            reason = (
                f"Flaky tests detected: counts varied by {variance} "
                f"across {self._runs} runs {counts} (tolerance={self._variance})"
            )
            self._write_gate(stable=False, reason=reason)
            return False, reason

        reason = f"Stable: {self._runs} runs all passed {counts}, variance={variance}"
        self._write_gate(stable=True, reason=reason)
        return True, reason

    def current_gate(self) -> bool:
        """Read current AWOS_SAFE_TO_RUN_TESTS value from os.environ."""
        return os.getenv(_ENV_KEY, "").lower() == "true"

    # ── Internal helpers ────────────────────────────────────────────────────

    def _collect_runs(self, verbose: bool) -> Tuple[List[int], List[str]]:
        counts: List[int] = []
        errors: List[str] = []
        for i in range(1, self._runs + 1):
            if verbose:
                logger.info("[StabilityGate] run %d/%d …", i, self._runs)
            count, err = self._run_once()
            if err:
                errors.append(err)
                break
            counts.append(count)
            if verbose:
                logger.info("[StabilityGate] run %d → %d passed", i, count)
        return counts, errors

    def _run_once(self) -> Tuple[int, str]:
        """Run test suite once. Returns (pass_count, error_str)."""
        try:
            result = subprocess.run(
                self._test_cmd,
                capture_output=True,
                text=True,
                timeout=_TEST_TIMEOUT_SECS,
                cwd=str(self._root),
            )
            output = result.stdout + result.stderr
            count = self._parse_pass_count(output)
            if count == 0 and result.returncode not in (0, 1):
                return 0, f"pytest exited {result.returncode}: {output[-300:]}"
            return count, ""
        except subprocess.TimeoutExpired:
            return 0, f"test run timed out after {_TEST_TIMEOUT_SECS}s"
        except Exception as exc:
            return 0, str(exc)

    def _parse_pass_count(self, output: str) -> int:
        m = re.search(r"(\d+) passed", output)
        return int(m.group(1)) if m else 0

    def _write_gate(self, stable: bool, reason: str) -> None:
        """Update AWOS_SAFE_TO_RUN_TESTS in .env and os.environ."""
        value = "true" if stable else "false"

        # Update live process env immediately
        os.environ[_ENV_KEY] = value

        # Read current .env lines (create if absent)
        lines: List[str] = []
        if self._env_file.exists():
            lines = self._env_file.read_text().splitlines()

        # Replace or append the key
        key_found = False
        new_lines: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{_ENV_KEY}=") or stripped.startswith(f"export {_ENV_KEY}="):
                new_lines.append(f"{_ENV_KEY}={value}")
                key_found = True
            else:
                new_lines.append(line)

        if not key_found:
            new_lines.append(f"{_ENV_KEY}={value}")

        try:
            self._env_file.write_text("\n".join(new_lines) + "\n")
            logger.info("[StabilityGate] %s=%s written to %s — %s", _ENV_KEY, value, self._env_file, reason)
        except Exception as exc:
            logger.warning("[StabilityGate] could not write .env: %s", exc)


# ── Standalone entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(__file__).parents[2]
    gate = StabilityGate(project_root=str(root), verbose=True)
    stable, reason = gate.run(verbose=True)
    print(f"\n{'STABLE ✓' if stable else 'UNSTABLE ✗'}  —  {reason}")
    print(f"AWOS_SAFE_TO_RUN_TESTS={'true' if stable else 'false'}")
    sys.exit(0 if stable else 1)
