"""
Tests for TestRunner — detection, parsing, execution, safety guard.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import from scaffold
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
from test_runner import TestRunner, TestConfig, TestResult, _SAFETY_ENV_VAR


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project(tmp_path):
    """Return a temp project directory."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def clear_safety_env():
    """Clear AWOS_SAFE_TO_RUN_TESTS after each test."""
    yield
    if _SAFETY_ENV_VAR in os.environ:
        del os.environ[_SAFETY_ENV_VAR]


# ── Detection Tests ──────────────────────────────────────────────────────

class TestDetection:
    def test_detect_pytest_ini(self, tmp_project):
        Path(tmp_project, "pytest.ini").write_text("[pytest]\n")
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "pytest"
        assert cfg.command == ["pytest", "--tb=short", "-q"]

    def test_detect_pyproject_pytest(self, tmp_project):
        Path(tmp_project, "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "pytest"

    def test_detect_setup_cfg_pytest(self, tmp_project):
        Path(tmp_project, "setup.cfg").write_text("[tool:pytest]\n")
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "pytest"

    def test_detect_jest(self, tmp_project):
        Path(tmp_project, "package.json").write_text(
            json.dumps({"devDependencies": {"jest": "^29.0.0"}})
        )
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "jest"
        assert cfg.command == ["npx", "jest", "--passWithNoTests"]

    def test_detect_vitest(self, tmp_project):
        Path(tmp_project, "package.json").write_text(
            json.dumps({"devDependencies": {"vitest": "^1.0.0"}})
        )
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "vitest"
        assert cfg.command == ["npx", "vitest", "run"]

    def test_detect_go(self, tmp_project):
        Path(tmp_project, "go.mod").write_text("module example\n")
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "go"
        assert cfg.command == ["go", "test", "./..."]

    def test_detect_cargo(self, tmp_project):
        Path(tmp_project, "Cargo.toml").write_text("[package]\nname = \"test\"\n")
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "cargo"
        assert cfg.command == ["cargo", "test"]

    def test_detect_make(self, tmp_project):
        Path(tmp_project, "Makefile").write_text("test:\n\t@echo ok\n")
        runner = TestRunner(tmp_project)
        cfg = runner.detect()
        assert cfg is not None
        assert cfg.runner == "make"
        assert cfg.command == ["make", "test"]

    def test_detect_unknown(self, tmp_project):
        runner = TestRunner(tmp_project)
        assert runner.detect() is None


# ── Parser Tests ─────────────────────────────────────────────────────────

class TestParsePytest:
    def test_all_pass(self):
        runner = TestRunner(".")
        stdout = "test_foo.py .\ntest_bar.py .\n\n2 passed in 0.12s\n"
        result = runner._parse_pytest(stdout, "", ["pytest"])
        assert result.passed == 2
        assert result.failed == 0
        assert result.errors == 0
        assert result.pass_rate == 1.0

    def test_one_fail(self):
        runner = TestRunner(".")
        stdout = "test_foo.py .\ntest_bar.py F\n\n1 failed, 1 passed in 0.12s\n"
        result = runner._parse_pytest(stdout, "", ["pytest"])
        assert result.passed == 1
        assert result.failed == 1
        assert result.pass_rate == 0.5

    def test_errors(self):
        runner = TestRunner(".")
        stdout = "test_foo.py E\n\n0 passed, 0 failed, 2 errors in 0.12s\n"
        result = runner._parse_pytest(stdout, "", ["pytest"])
        assert result.passed == 0
        assert result.failed == 0
        assert result.errors == 2
        assert result.pass_rate == 0.0

    def test_pass_fail_errors(self):
        runner = TestRunner(".")
        stdout = "\n3 passed, 1 failed, 2 errors in 0.45s\n"
        result = runner._parse_pytest(stdout, "", ["pytest"])
        assert result.passed == 3
        assert result.failed == 1
        assert result.errors == 2
        assert result.pass_rate == 0.5  # 3 / 6

    def test_no_tests_ran(self):
        runner = TestRunner(".")
        stdout = "\nno tests ran in 0.01s\n"
        result = runner._parse_pytest(stdout, "", ["pytest"])
        assert result.pass_rate == 0.0
        assert result.no_tests_found is True

    def test_collection_error(self):
        runner = TestRunner(".")
        stdout = ""
        stderr = "ERROR: could not load test_foo.py\n"
        result = runner._parse_pytest(stdout, stderr, ["pytest"])
        assert result.pass_rate == 0.0


class TestParseJest:
    def test_passed(self):
        runner = TestRunner(".")
        stdout = "Tests: 3 passed, 3 total\n"
        result = runner._parse_jest(stdout, "", ["jest"])
        assert result.passed == 3
        assert result.failed == 0
        assert result.pass_rate == 1.0

    def test_failed(self):
        runner = TestRunner(".")
        stdout = "Tests: 2 passed, 1 failed, 3 total\n"
        result = runner._parse_jest(stdout, "", ["jest"])
        assert result.passed == 2
        assert result.failed == 1
        assert result.pass_rate == pytest.approx(0.6667, 0.001)

    def test_suites(self):
        runner = TestRunner(".")
        stdout = "Test Suites: 2 passed, 1 failed\n"
        result = runner._parse_jest(stdout, "", ["jest"])
        assert result.passed == 2
        assert result.failed == 1
        assert result.pass_rate == pytest.approx(0.6667, 0.001)


class TestParseVitest:
    def test_passed(self):
        runner = TestRunner(".")
        stdout = "Tests  3 passed | 0 failed | 3 total\n"
        result = runner._parse_vitest(stdout, "", ["vitest"])
        assert result.passed == 3
        assert result.failed == 0
        assert result.pass_rate == 1.0

    def test_mixed(self):
        runner = TestRunner(".")
        stdout = "Tests  2 passed | 1 failed | 3 total\n"
        result = runner._parse_vitest(stdout, "", ["vitest"])
        assert result.passed == 2
        assert result.failed == 1
        assert result.pass_rate == pytest.approx(0.6667, 0.001)


class TestParseGeneric:
    def test_passed_failed(self):
        runner = TestRunner(".")
        stdout = "5 passed, 2 failed, 1 error\n"
        result = runner._parse_generic(stdout, "", ["make", "test"])
        assert result.passed == 5
        assert result.failed == 2
        assert result.errors == 1
        assert result.pass_rate == pytest.approx(5 / 8, 0.001)

    def test_no_numbers(self):
        runner = TestRunner(".")
        stdout = "some output\n"
        result = runner._parse_generic(stdout, "", ["cmd"])
        assert result.passed == 0
        assert result.failed == 0
        assert result.pass_rate == 0.0


# ── Run / Safety Tests ───────────────────────────────────────────────────

class TestRun:
    def test_run_success(self, tmp_project):
        os.environ[_SAFETY_ENV_VAR] = "1"
        Path(tmp_project, "pytest.ini").write_text("[pytest]\n")
        runner = TestRunner(tmp_project)

        mock_result = MagicMock()
        mock_result.stdout = "2 passed in 0.12s\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = runner.run()

        assert result.passed == 2
        assert result.pass_rate == 1.0
        mock_run.assert_called_once()
        assert mock_run.call_args[1]["cwd"] == tmp_project

    def test_run_timeout(self, tmp_project):
        os.environ[_SAFETY_ENV_VAR] = "1"
        Path(tmp_project, "pytest.ini").write_text("[pytest]\n")
        runner = TestRunner(tmp_project, timeout_sec=1)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 1)):
            result = runner.run()

        assert result.timed_out is True
        assert result.pass_rate == 0.0

    def test_run_no_tests_detected(self, tmp_project):
        os.environ[_SAFETY_ENV_VAR] = "1"
        runner = TestRunner(tmp_project)
        result = runner.run()
        assert result.no_tests_found is True
        assert result.pass_rate == 0.0


class TestSafetyGuard:
    def test_guard_blocks_without_env(self, tmp_project):
        if _SAFETY_ENV_VAR in os.environ:
            del os.environ[_SAFETY_ENV_VAR]
        Path(tmp_project, "pytest.ini").write_text("[pytest]\n")
        runner = TestRunner(tmp_project)

        with patch("subprocess.run") as mock_run:
            result = runner.run()

        assert result.pass_rate == 0.0
        assert _SAFETY_ENV_VAR in result.raw_output
        mock_run.assert_not_called()

    def test_guard_allows_with_env(self, tmp_project):
        os.environ[_SAFETY_ENV_VAR] = "1"
        Path(tmp_project, "pytest.ini").write_text("[pytest]\n")
        runner = TestRunner(tmp_project)

        mock_result = MagicMock()
        mock_result.stdout = "1 passed in 0.01s\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = runner.run()

        assert result.passed == 1
        mock_run.assert_called_once()


# ── Integration ──────────────────────────────────────────────────────────

class TestIntegration:
    def test_full_pipeline(self, tmp_project):
        os.environ[_SAFETY_ENV_VAR] = "1"
        Path(tmp_project, "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        runner = TestRunner(tmp_project)

        mock_result = MagicMock()
        mock_result.stdout = "3 passed, 1 failed in 0.12s\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = runner.run()

        assert result.pass_rate == 0.75
        assert result.raw_output == "3 passed, 1 failed in 0.12s\n\n"


class TestComputeRewardFloat:
    """Verify compute_reward accepts float pass_rate (added 2026-05-19)."""

    def test_float_pass_rate_interpolates(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
        from reward_store import compute_reward

        # pass_rate = 0.5 should be exactly between success and failure reward
        r_success = compute_reward(True, 1, 0.001)
        r_failure = compute_reward(False, 1, 0.001)
        r_half = compute_reward(0.5, 1, 0.001)
        expected = round(r_success * 0.5 + r_failure * 0.5, 4)
        assert r_half == expected

    def test_full_pass_rate_equals_success(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
        from reward_store import compute_reward

        assert compute_reward(1.0, 1, 0.001) == compute_reward(True, 1, 0.001)

    def test_zero_pass_rate_equals_failure(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
        from reward_store import compute_reward

        assert compute_reward(0.0, 1, 0.001) == compute_reward(False, 1, 0.001)

    def test_backward_compat_bool(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
        from reward_store import compute_reward

        # Ensure bool still works exactly as before
        assert compute_reward(True, 1, 0.001) == 0.999
        assert compute_reward(False, 1, 0.001) == pytest.approx(-0.0539, 0.0001)
