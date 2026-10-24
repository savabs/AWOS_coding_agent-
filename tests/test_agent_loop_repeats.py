"""
Tests for the repeated-tool-call guard in scaffold/agent/agent_loop.py.

The guard exists to end a stuck loop: the same call, on the same workspace,
giving the same answer. Re-running the tests after each edit is the same call
every time, and the long-task benchmark showed the old guard ending real work
on the third legitimate test run. These tests pin the difference: a repeat only
counts when nothing changed the workspace in between.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.agent_loop import AgentLoop, ModelReply, ToolCall
from scaffold.agent.tools.base import ToolResult


class _FakeRegistry:
    """Executes calls without touching disk; outputs are scripted per tool."""

    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.executed = []

    def execute(self, name, arguments):
        self.executed.append(name)
        text = self.outputs.get(name, f"{name} ok")
        if callable(text):
            text = text()
        data = {"path": arguments.get("path")} if name == "edit_file" else {}
        return ToolResult(success=True, text=text, data=data)


class _ScriptedClient:
    def __init__(self, replies):
        self._replies = list(replies)

    def complete(self, system, messages, registry):
        if not self._replies:
            return ModelReply(text="done")
        return self._replies.pop(0)

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text}

    def format_tool_results(self, calls, results):
        return [{"role": "user", "content": f"{c.name}: {r.success}"} for c, r in zip(calls, results)]


def _reply(name, args):
    return ModelReply(text="", tool_calls=[ToolCall(id="t0", name=name, arguments=args)])


RUN_TESTS = ("run_tests", {})


def _edit(i):
    return ("edit_file", {"path": "calc.py", "old_string": f"v{i}", "new_string": f"v{i + 1}"})


def _run(replies, registry=None, max_repeats=3):
    registry = registry or _FakeRegistry()
    loop = AgentLoop(registry, _ScriptedClient([_reply(*r) for r in replies]), max_turns=50, max_repeats=max_repeats)
    return loop.run("task"), registry


class TestRepeatsAfterChanges(unittest.TestCase):
    def test_run_tests_after_each_edit_is_not_stopped(self):
        replies = []
        for i in range(5):
            replies += [_edit(i), RUN_TESTS]
        outcome, registry = _run(replies)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(registry.executed.count("run_tests"), 5)

    def test_run_command_counts_as_a_change(self):
        replies = []
        for i in range(5):
            replies += [("run_command", {"command": f"sed -i s/{i}/{i + 1}/ calc.py"}), RUN_TESTS]
        outcome, registry = _run(replies)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(registry.executed.count("run_tests"), 5)

    def test_repeated_command_with_changing_output_is_not_stopped(self):
        counter = iter(range(100))
        registry = _FakeRegistry({"run_command": lambda: f"step {next(counter)}"})
        outcome, _ = _run([("run_command", {"command": "make step"})] * 6, registry)
        self.assertEqual(outcome.stop_reason, "completed")


class TestIdenticalRepeats(unittest.TestCase):
    def test_identical_call_with_no_change_stops_at_same_threshold(self):
        outcome, registry = _run([RUN_TESTS] * 10, max_repeats=3)
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        # max_repeats calls run; the next one is refused and ends the run.
        self.assertEqual(registry.executed.count("run_tests"), 3)
        self.assertEqual(outcome.turns, 4)

    def test_count_restarts_after_a_change_then_trips_again(self):
        replies = [RUN_TESTS] * 3 + [_edit(0)] + [RUN_TESTS] * 10
        outcome, registry = _run(replies, max_repeats=3)
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertEqual(registry.executed.count("run_tests"), 6)

    def test_identical_command_with_identical_output_still_stops(self):
        """A successful command that changes nothing must not reset its own count."""
        outcome, registry = _run([("run_command", {"command": "ls"})] * 20, max_repeats=3)
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertLessEqual(registry.executed.count("run_command"), 4)

    def test_refusal_is_reported_in_band(self):
        outcome, _ = _run([RUN_TESTS] * 10, max_repeats=2)
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertFalse(outcome.transcript[-1]["calls"][0]["ok"])



class _DiskRegistry(_FakeRegistry):
    """A real project root; run_command runs a scripted effect on it."""

    def __init__(self, root, effects):
        super().__init__()
        self.project_root = root
        self.effects = effects  # command -> callable(root) -> output text

    def execute(self, name, arguments):
        self.executed.append(name)
        if name == "run_command":
            out = self.effects[arguments["command"]](self.project_root)
            return ToolResult(success=True, text=out, data={})
        return ToolResult(success=True, text=f"{name} ok", data={})


class TestWorkspaceIsTheChangeSignal(unittest.TestCase):
    """What a command changed is read off the workspace, not its output."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()
        with open(os.path.join(self.root, "calc.py"), "w") as fh:
            fh.write("x = 1\n")
        self.clock = iter(range(1000))

    def _run(self, commands, effects):
        registry = _DiskRegistry(self.root, effects)
        loop = AgentLoop(registry, _ScriptedClient([_reply("run_command", {"command": c})
                                                    for c in commands]), max_turns=50)
        return loop.run("task"), registry

    def test_pytest_output_that_differs_only_in_timing_still_trips(self):
        effects = {"pytest": lambda root: f"1 passed in 0.{next(self.clock):02d}s"}
        outcome, registry = self._run(["pytest"] * 20, effects)
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertLessEqual(registry.executed.count("run_command"), 3)

    def test_two_alternating_stable_commands_trip(self):
        effects = {"ls": lambda root: "calc.py", "cat calc.py": lambda root: "x = 1"}
        outcome, registry = self._run(["ls", "cat calc.py"] * 10, effects)
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertLessEqual(registry.executed.count("run_command"), 7)

    def test_a_command_that_writes_a_file_is_a_change_and_is_reported(self):
        def bump(root):
            n = next(self.clock)
            with open(os.path.join(root, "calc.py"), "w") as fh:
                fh.write(f"x = {n}\n")
            return "ok"

        outcome, registry = self._run(["bump"] * 6, {"bump": bump})
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(registry.executed.count("run_command"), 6)
        self.assertEqual(outcome.files_written, [os.path.join(os.path.abspath(self.root), "calc.py")])
        self.assertEqual(outcome.files_touched, [])

    def test_rewriting_identical_content_is_not_a_change(self):
        def regen(root):
            with open(os.path.join(root, "report.csv"), "w") as fh:
                fh.write("a,b\n")
            os.utime(os.path.join(root, "report.csv"), ns=(next(self.clock) * 10**9,) * 2)
            return f"wrote report in 0.{next(self.clock):02d}s"

        outcome, registry = self._run(["regen"] * 20, {"regen": regen})
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        # The first run created it (a change); identical rewrites after do not count.
        self.assertLessEqual(registry.executed.count("run_command"), 4)

    def test_a_write_under_a_cache_dir_is_not_a_change(self):
        def cache(root):
            os.makedirs(os.path.join(root, ".pytest_cache"), exist_ok=True)
            with open(os.path.join(root, ".pytest_cache", "v"), "w") as fh:
                fh.write(str(next(self.clock)))
            return "1 passed"

        outcome, _ = self._run(["pytest"] * 20, {"pytest": cache})
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertEqual(outcome.files_written, [])

    def test_required_edits_are_satisfied_by_a_command_that_wrote_a_file(self):
        def gen(root):
            with open(os.path.join(root, "out.txt"), "w") as fh:
                fh.write("generated")
            return "ok"

        registry = _DiskRegistry(self.root, {"gen": gen})
        loop = AgentLoop(registry, _ScriptedClient([_reply("run_command", {"command": "gen"})]),
                         max_turns=10, require_edits=True)
        outcome = loop.run("task")
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.nudges, 0)


if __name__ == "__main__":
    unittest.main()
