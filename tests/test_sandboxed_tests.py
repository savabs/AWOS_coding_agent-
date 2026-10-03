"""
Tests run inside the sandbox — run_tests and the orchestrator's verification.

pytest imports project code on collection (conftest.py above all), so code the
model wrote would otherwise execute on the host even though run_command is
sandboxed.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.sandbox import SandboxResult
from scaffold.agent.test_runner import TestRunner


class _FakeSandbox:
    backend = "fake"

    def __init__(self, workspace, result):
        self.workspace = Path(workspace)
        self.result = result
        self.commands = []

    def run(self, command, timeout_sec=120):
        self.commands.append((command, timeout_sec))
        return self.result

    def close(self):
        pass


def _project():
    root = Path(tempfile.mkdtemp())
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _allow_tests(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")


def test_runner_uses_the_sandbox_and_parses_its_output():
    sb = _FakeSandbox(_project(), SandboxResult(1, "3 passed, 1 failed in 0.1s", "", False, 0.1))
    result = TestRunner(project_root=str(sb.workspace), sandbox=sb).run()
    assert (result.passed, result.failed) == (3, 1)
    command, _ = sb.commands[0]
    # The host interpreter path means nothing inside a container.
    assert command.startswith("python -m pytest") and sys.executable not in command


def test_runner_reports_a_sandbox_timeout():
    sb = _FakeSandbox(_project(), SandboxResult(-9, "", "", True, 60.0))
    assert TestRunner(project_root=str(sb.workspace), sandbox=sb).run().timed_out


def test_missing_pytest_in_the_sandbox_is_no_evidence():
    sb = _FakeSandbox(_project(), SandboxResult(1, "", "No module named pytest", False, 0.1))
    assert TestRunner(project_root=str(sb.workspace), sandbox=sb).run().no_tests_found


def test_registry_shares_its_sandbox_with_run_tests():
    from scaffold.agent.agent_loop import build_coding_registry

    root = _project()
    sb = _FakeSandbox(root, SandboxResult(0, "1 passed in 0.1s", "", False, 0.1))
    registry = build_coding_registry(str(root), sandbox=sb)
    assert registry.sandbox is sb
    assert registry.execute("run_tests", {}).success
    assert sb.commands, "run_tests did not go through the sandbox"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
def test_a_planted_conftest_cannot_escape_through_run_tests(monkeypatch):
    # The attack: the model writes a conftest.py, then asks for the tests.
    from scaffold.agent.agent_loop import build_coding_registry

    monkeypatch.setenv("AWOS_SANDBOX", "seatbelt")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-planted-canary")
    outside = Path(tempfile.mkdtemp()) / "escaped.txt"
    root = _project()
    (root / "conftest.py").write_text(
        "import os, pathlib\n"
        f"try:\n    pathlib.Path({str(outside)!r}).write_text('x')\n"
        "except OSError:\n    pass\n"
        "pathlib.Path('leak.txt').write_text(os.environ.get('OPENROUTER_API_KEY', 'none'))\n",
        encoding="utf-8",
    )
    (root / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    registry = build_coding_registry(str(root))
    try:
        assert registry.sandbox is not None and registry.sandbox.backend == "seatbelt"
        result = registry.execute("run_tests", {})
    finally:
        registry.close()

    assert result.success, result.text
    assert not outside.exists(), "conftest.py wrote outside the workspace"
    assert (root / "leak.txt").read_text() == "none", "an API key reached the test process"
