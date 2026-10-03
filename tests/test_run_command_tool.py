"""
Tests for scaffold/agent/tools/run_command.py and its wiring into
build_coding_registry.

A fake Sandbox stands in for scaffold/agent/sandbox.py throughout, so these
tests neither execute anything nor depend on which sandbox backend the machine
has. The registry tests monkeypatch make_sandbox through a stub module.
"""

import os
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.agent_loop import _render_result, build_coding_registry
from scaffold.agent.tools.base import ToolRegistry
from scaffold.agent.tools.run_command import (
    DEFAULT_TIMEOUT_SEC,
    MAX_STREAM_CHARS,
    MAX_TIMEOUT_SEC,
    RunCommandTool,
)

EXISTING_TOOLS = ["read_file", "list_dir", "find_files", "grep", "edit_file", "run_tests"]


@dataclass
class FakeResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float


class FakeSandbox:
    backend = "fake"

    def __init__(self, result=None):
        self.workspace = Path("/tmp/fake-workspace")
        self.result = result or FakeResult(0, "hello\n", "", False, 0.01)
        self.calls = []

    def run(self, command, timeout_sec=120):
        self.calls.append((command, timeout_sec))
        return self.result

    def close(self):
        pass


def _registry_with(tool):
    registry = ToolRegistry()
    registry.register(tool)
    return registry


class TestRunCommandTool(unittest.TestCase):
    def test_success_report(self):
        sandbox = FakeSandbox(FakeResult(0, "rows: 3\n", "warn\n", False, 0.5))
        result = _registry_with(RunCommandTool(sandbox)).execute(
            "run_command", {"command": "python report.py"}
        )
        self.assertTrue(result.success)
        self.assertEqual(
            result.text, "exit_code: 0\n--- stdout ---\nrows: 3\n\n--- stderr ---\nwarn\n"
        )
        self.assertEqual(result.data, {"exit_code": 0, "timed_out": False, "duration_sec": 0.5})
        self.assertEqual(sandbox.calls, [("python report.py", DEFAULT_TIMEOUT_SEC)])

    def test_nonzero_exit_fails_but_keeps_report(self):
        sandbox = FakeSandbox(FakeResult(1, "", "Traceback: boom\n", False, 0.2))
        result = RunCommandTool(sandbox).execute({"command": "python bad.py"})
        self.assertFalse(result.success)
        self.assertTrue(result.text.startswith("exit_code: 1\n"))
        # The model sees the traceback, not a bare failure.
        self.assertIn("Traceback: boom", _render_result(result))

    def test_timeout_fails_and_is_marked(self):
        sandbox = FakeSandbox(FakeResult(0, "partial", "", True, 5.0))
        result = RunCommandTool(sandbox).execute({"command": "sleep 99", "timeout_sec": 5})
        self.assertFalse(result.success)
        self.assertTrue(result.text.startswith("exit_code: 0 (timed out)\n"))
        self.assertTrue(result.data["timed_out"])
        self.assertEqual(sandbox.calls, [("sleep 99", 5)])

    def test_timeout_is_clamped(self):
        sandbox = FakeSandbox()
        tool = RunCommandTool(sandbox)
        tool.execute({"command": "x", "timeout_sec": 10_000})
        tool.execute({"command": "x", "timeout_sec": "30"})
        self.assertEqual([t for _, t in sandbox.calls], [MAX_TIMEOUT_SEC, 30])

    def test_truncation_keeps_the_tail(self):
        stdout = "HEAD" + "x" * (MAX_STREAM_CHARS * 2) + "FINAL SUMMARY"
        stderr = "START" + "y" * (MAX_STREAM_CHARS * 2) + "Error: last line"
        result = RunCommandTool(FakeSandbox(FakeResult(1, stdout, stderr, False, 1.0))).execute(
            {"command": "big"}
        )
        self.assertNotIn("HEAD", result.text)
        self.assertNotIn("START", result.text)
        self.assertIn("FINAL SUMMARY", result.text)
        self.assertIn("earlier characters truncated", result.text)
        # The agent loop's own cap must not cut the stderr tail off.
        self.assertIn("Error: last line", _render_result(result))

    def test_validation(self):
        tool = RunCommandTool(FakeSandbox())
        self.assertTrue(tool.validate({}))
        self.assertTrue(tool.validate({"command": "  "}))
        self.assertTrue(tool.validate({"command": "ls", "timeout_sec": "soon"}))
        self.assertEqual(tool.validate({"command": "ls"}), [])

    def test_schema_in_both_provider_shapes(self):
        registry = _registry_with(RunCommandTool(FakeSandbox()))
        anthropic = registry.anthropic_schemas()[0]
        openai = registry.openai_schemas()[0]["function"]
        self.assertEqual(anthropic["name"], "run_command")
        self.assertEqual(openai["name"], "run_command")
        for schema in (anthropic["input_schema"], openai["parameters"]):
            self.assertEqual(schema["required"], ["command"])
            self.assertEqual(schema["properties"]["command"]["type"], "string")
            self.assertEqual(schema["properties"]["timeout_sec"]["type"], "integer")
        self.assertIn("run_tests", anthropic["description"])


def _stub_sandbox_module(make_sandbox):
    module = types.ModuleType("scaffold.agent.sandbox")
    module.make_sandbox = make_sandbox
    return mock.patch.dict(
        sys.modules, {"scaffold.agent.sandbox": module, "sandbox": module}
    )


class TestRegistryWiring(unittest.TestCase):
    def test_explicit_sandbox_registers_run_command_last(self):
        registry = build_coding_registry(".", sandbox=FakeSandbox())
        self.assertEqual(registry.names(), EXISTING_TOOLS + ["run_command"])

    def test_make_sandbox_result_is_used(self):
        sandbox = FakeSandbox()
        seen = []

        def make_sandbox(workspace, backend=None):
            seen.append(workspace)
            return sandbox

        with _stub_sandbox_module(make_sandbox):
            registry = build_coding_registry("proj")
        self.assertEqual(seen, ["proj"])
        self.assertEqual(registry.names(), EXISTING_TOOLS + ["run_command"])
        self.assertIs(registry.get("run_command").sandbox, sandbox)

    def test_no_sandbox_means_no_run_command(self):
        with _stub_sandbox_module(lambda workspace, backend=None: None):
            self.assertEqual(build_coding_registry(".").names(), EXISTING_TOOLS)

    def test_make_sandbox_raising_means_no_run_command(self):
        def boom(workspace, backend=None):
            raise RuntimeError("docker daemon exploded")

        with _stub_sandbox_module(boom):
            self.assertEqual(build_coding_registry(".").names(), EXISTING_TOOLS)

    def test_shell_keeps_its_place_before_run_command(self):
        registry = build_coding_registry(".", allow_shell=True, sandbox=FakeSandbox())
        self.assertEqual(registry.names(), EXISTING_TOOLS + ["shell", "run_command"])

    def test_registry_close_releases_the_sandbox(self):
        # Callers close the registry, not the sandbox they never saw; a docker
        # backend would otherwise keep its container running.
        sandbox = mock.Mock(wraps=FakeSandbox())
        registry = build_coding_registry(".", allow_shell=True, sandbox=sandbox)
        registry.close()
        registry.close()
        self.assertEqual(sandbox.close.call_count, 2)  # sandboxes' close is idempotent

    def test_registry_close_without_sandbox_is_a_no_op(self):
        with _stub_sandbox_module(lambda workspace, backend=None: None):
            build_coding_registry(".").close()


if __name__ == "__main__":
    unittest.main()
