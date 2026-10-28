"""A project's addopts naming a missing plugin (e.g. --cov) must not zero the test result."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.test_runner import TestRunner  # noqa: E402


def _project(tmp_path, addopts: str):
    (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = {addopts}\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_a():\n    assert True\n\n"
                                                 "def test_b():\n    assert 1 + 1 == 2\n")
    return tmp_path


def test_missing_plugin_addopts_is_retried_without_them(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    proj = _project(tmp_path, "--cov=nonexistent_pkg_xyz --cov-report=term")
    runner = TestRunner(str(proj))
    result = runner.run()
    assert result.passed == 2 and result.failed == 0
    assert runner._drop_addopts is True
    assert "addopts=" in " ".join(runner.pytest_base())


def test_valid_addopts_are_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    proj = _project(tmp_path, "--strict-markers")
    runner = TestRunner(str(proj))
    result = runner.run()
    assert result.passed == 2
    assert runner._drop_addopts is False
    assert "addopts=" not in " ".join(runner.pytest_base())


def test_real_test_failure_is_not_mistaken_for_usage_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    proj = _project(tmp_path, "--strict-markers")
    (proj / "tests" / "test_bad.py").write_text("def test_c():\n    assert False\n")
    runner = TestRunner(str(proj))
    result = runner.run()
    assert result.failed == 1
    assert runner._drop_addopts is False
