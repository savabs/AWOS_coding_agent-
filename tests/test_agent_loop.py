"""
Tests for scaffold/agent/agent_loop.py and scaffold/agent/tools/code_edit.py.

The model is stubbed throughout, so the whole file runs with no API key and no
network. Each class covers one concern:

  TestToolSchemas        — JSON Schema emission and required-parameter derivation
  TestEditFileTool       — edits land, bad edits are refused, file left intact
  TestRunTestsTool       — safety guard and result reporting
  TestLoopTermination    — every stop condition in the spec
  TestLoopBehaviour      — a real read -> edit -> run_tests sequence
  TestClientAdapters     — Anthropic and OpenAI message shapes
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.agent_loop import (
    AgentLoop,
    AnthropicToolClient,
    ModelReply,
    OpenAIToolClient,
    ToolCall,
    build_coding_registry,
    _render_result,
)
from scaffold.agent.tools.base import Tool, ToolResult
from scaffold.agent.tools.code_edit import EditFileTool, RunTestsTool


# ── Helpers ───────────────────────────────────────────────────────────────────


class ScriptedClient:
    """Replays a fixed list of ModelReply objects, one per turn."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls_received = 0
        self.last_messages = None

    def complete(self, system, messages, registry):
        self.calls_received += 1
        self.last_messages = list(messages)
        if not self._replies:
            return ModelReply(text="done")
        return self._replies.pop(0)

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text}

    def format_tool_results(self, calls, results):
        return [
            {"role": "user", "content": f"{c.name}: {r.success}"}
            for c, r in zip(calls, results)
        ]


class ExplodingClient:
    """Raises on the first call, to exercise the model_error path."""

    def complete(self, system, messages, registry):
        raise RuntimeError("connection reset")

    def format_assistant_turn(self, reply):
        return {}

    def format_tool_results(self, calls, results):
        return []


def _reply(*calls, text=""):
    return ModelReply(
        text=text,
        tool_calls=[
            ToolCall(id=f"t{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        input_tokens=10,
        output_tokens=5,
    )


class _StubLedger:
    def __init__(self, allowed=True, reason=""):
        self.allowed, self.reason = allowed, reason

    def check_budget(self, estimated_cost, monthly_budget=20.0):
        return self.allowed, self.reason


def _temp_project():
    d = Path(tempfile.mkdtemp())
    (d / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return d


# ── Schemas ───────────────────────────────────────────────────────────────────


class TestToolSchemas(unittest.TestCase):
    def test_schema_has_json_schema_shape(self):
        schema = EditFileTool().input_schema
        self.assertEqual(schema["type"], "object")
        self.assertIn("path", schema["properties"])
        self.assertEqual(schema["properties"]["path"]["type"], "string")

    def test_optional_params_are_not_required(self):
        """read_file documents start_line/end_line but only needs path."""
        from scaffold.agent.tools.filesystem import ReadFileTool

        required = ReadFileTool().input_schema["required"]
        self.assertIn("path", required)
        self.assertNotIn("start_line", required)
        self.assertNotIn("end_line", required)

    def test_required_params_are_required(self):
        self.assertEqual(
            sorted(EditFileTool().input_schema["required"]),
            ["new_string", "old_string", "path"],
        )

    def test_anthropic_and_openai_shapes(self):
        registry = build_coding_registry(".")
        anthropic = registry.anthropic_schemas()
        openai = registry.openai_schemas()
        self.assertEqual(len(anthropic), len(openai))
        self.assertIn("input_schema", anthropic[0])
        self.assertEqual(openai[0]["type"], "function")
        self.assertIn("parameters", openai[0]["function"])

    def test_registry_excludes_write_file(self):
        """edit_file is validated; write_file overwrites wholesale."""
        self.assertNotIn("write_file", build_coding_registry(".").names())

    def test_shell_is_opt_in(self):
        self.assertNotIn("shell", build_coding_registry(".").names())
        self.assertIn("shell", build_coding_registry(".", allow_shell=True).names())

    def test_every_tool_resolves_paths_against_the_same_root(self):
        """
        Regression: read_file resolved against the process cwd while edit_file
        resolved against project_root, so the same relative path referred to two
        different files and the model's first read always failed.
        """
        project = _temp_project()
        registry = build_coding_registry(str(project))

        read = registry.execute("read_file", {"path": "calc.py"})
        self.assertTrue(read.success, f"read_file could not resolve calc.py: {read.error}")
        self.assertIn("def add", read.text)

        listed = registry.execute("list_dir", {"path": "."})
        self.assertIn("calc.py", listed.text)

        found = registry.execute("find_files", {"pattern": "*.py"})
        self.assertIn("calc.py", found.text)

        grepped = registry.execute("grep", {"pattern": "def add"})
        self.assertTrue(grepped.success)
        self.assertIn("calc.py", grepped.text)

    def test_absolute_paths_still_work(self):
        project = _temp_project()
        registry = build_coding_registry(str(project))
        result = registry.execute("read_file", {"path": str(project / "calc.py")})
        self.assertTrue(result.success)

    def test_rooted_tools_default_to_cwd(self):
        """Existing callers that construct tools with no root keep working."""
        from scaffold.agent.tools.filesystem import ReadFileTool

        self.assertTrue(ReadFileTool()(path="requirements.txt").success)

    def test_schema_derivation_survives_a_strict_validate(self):
        """A validate() that rejects empty input must not break schema building."""

        class Strict(Tool):
            name = "strict"
            description = "d"

            @property
            def parameters(self):
                return {"a": "an arg"}

            def validate(self, args):
                raise KeyError("boom")

            def execute(self, args):
                return ToolResult.ok("")

        # Falls back to treating every documented parameter as required.
        self.assertEqual(Strict().input_schema["required"], ["a"])


# ── edit_file ─────────────────────────────────────────────────────────────────


class TestEditFileTool(unittest.TestCase):
    def setUp(self):
        self.project = _temp_project()
        self.tool = EditFileTool(project_root=str(self.project))

    def test_applies_a_valid_edit(self):
        result = self.tool(path="calc.py", old_string="a + b", new_string="a - b")
        self.assertTrue(result.success)
        self.assertIn("a - b", (self.project / "calc.py").read_text())

    def test_rejects_edit_that_breaks_syntax(self):
        original = (self.project / "calc.py").read_text()
        result = self.tool(path="calc.py", old_string="return a + b", new_string="return a +")
        self.assertFalse(result.success)
        self.assertIn("Syntax error", result.error)
        # The file must be untouched when an edit is refused.
        self.assertEqual((self.project / "calc.py").read_text(), original)

    def test_rejects_ambiguous_edit(self):
        (self.project / "dup.py").write_text("x = 1\nx = 1\n")
        result = self.tool(path="dup.py", old_string="x = 1", new_string="x = 2")
        self.assertFalse(result.success)
        self.assertIn("2 times", result.error)

    def test_missing_file_is_a_readable_error(self):
        result = self.tool(path="nope.py", old_string="a", new_string="b")
        self.assertFalse(result.success)
        self.assertIn("File not found", result.error)

    def test_creates_file_with_empty_old_string(self):
        result = self.tool(path="pkg/new.py", old_string="", new_string="VALUE = 1\n")
        self.assertTrue(result.success)
        self.assertEqual((self.project / "pkg/new.py").read_text(), "VALUE = 1\n")

    def test_refuses_to_clobber_existing_file_via_create(self):
        result = self.tool(path="calc.py", old_string="", new_string="wiped")
        self.assertFalse(result.success)
        self.assertIn("already exists", result.error)
        self.assertIn("def add", (self.project / "calc.py").read_text())

    def test_validation_reports_missing_arguments(self):
        self.assertTrue(self.tool.validate({}))
        self.assertFalse(self.tool.validate({"path": "a", "old_string": "", "new_string": ""}))


class TestRunTestsTool(unittest.TestCase):
    def setUp(self):
        self.project = _temp_project()
        self.tool = RunTestsTool(project_root=str(self.project))
        self._saved = os.environ.get(RunTestsTool.SAFETY_ENV_VAR)
        os.environ.pop(RunTestsTool.SAFETY_ENV_VAR, None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(RunTestsTool.SAFETY_ENV_VAR, None)
        else:
            os.environ[RunTestsTool.SAFETY_ENV_VAR] = self._saved

    def test_blocked_without_the_safety_flag(self):
        result = self.tool()
        self.assertFalse(result.success)
        self.assertIn(RunTestsTool.SAFETY_ENV_VAR, result.error)

    def test_reuses_test_runners_own_flag(self):
        """One switch for running tests, not a competing second one."""
        from scaffold.agent import test_runner

        self.assertEqual(RunTestsTool.SAFETY_ENV_VAR, test_runner._SAFETY_ENV_VAR)

    def test_all_arguments_optional(self):
        self.assertEqual(self.tool.validate({}), [])


# ── Termination ───────────────────────────────────────────────────────────────


class TestLoopTermination(unittest.TestCase):
    """Every stop condition in the spec must actually stop the loop."""

    def setUp(self):
        self.project = _temp_project()
        self.registry = build_coding_registry(str(self.project))

    def test_stops_when_model_makes_no_tool_calls(self):
        loop = AgentLoop(self.registry, ScriptedClient([ModelReply(text="all done")]))
        outcome = loop.run("task")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.final_message, "all done")

    def test_stops_at_max_turns(self):
        forever = [_reply(("read_file", {"path": str(self.project / "calc.py")})) for _ in range(20)]
        loop = AgentLoop(self.registry, ScriptedClient(forever), max_turns=3)
        outcome = loop.run("task")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stop_reason, "max_turns")
        self.assertEqual(outcome.turns, 3)

    def test_stops_when_budget_blocks(self):
        loop = AgentLoop(
            self.registry,
            ScriptedClient([ModelReply(text="hi")]),
            ledger=_StubLedger(allowed=False, reason="BUDGET EXHAUSTED"),
        )
        outcome = loop.run("task")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stop_reason, "budget_blocked")
        self.assertIn("BUDGET", outcome.final_message)

    def test_budget_is_checked_before_any_model_call(self):
        client = ScriptedClient([ModelReply(text="hi")])
        AgentLoop(self.registry, client, ledger=_StubLedger(False, "no")).run("task")
        self.assertEqual(client.calls_received, 0)

    def test_stops_on_repeated_identical_tool_call(self):
        same = ("read_file", {"path": "/nonexistent/x.py"})
        loop = AgentLoop(
            self.registry,
            ScriptedClient([_reply(same) for _ in range(10)]),
            max_turns=10,
            max_repeats=2,
        )
        outcome = loop.run("task")
        self.assertEqual(outcome.stop_reason, "repeated_tool_call")
        self.assertLess(outcome.turns, 10)

    def test_model_error_stops_cleanly(self):
        outcome = AgentLoop(self.registry, ExplodingClient()).run("task")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stop_reason, "model_error")
        self.assertIn("connection reset", outcome.final_message)

    def test_budget_check_failure_does_not_halt_the_loop(self):
        class AngryLedger:
            def check_budget(self, *a, **k):
                raise RuntimeError("ledger unavailable")

        loop = AgentLoop(
            self.registry, ScriptedClient([ModelReply(text="ok")]), ledger=AngryLedger()
        )
        self.assertEqual(loop.run("task").stop_reason, "completed")


# ── Behaviour ─────────────────────────────────────────────────────────────────


class TestLoopBehaviour(unittest.TestCase):
    def setUp(self):
        self.project = _temp_project()
        self.registry = build_coding_registry(str(self.project))

    def test_full_read_edit_verify_sequence(self):
        """The sequence a single-shot worker structurally cannot perform."""
        client = ScriptedClient(
            [
                _reply(("read_file", {"path": str(self.project / "calc.py")}), text="looking"),
                _reply(
                    ("edit_file", {"path": "calc.py", "old_string": "a + b", "new_string": "a * b"}),
                    text="editing",
                ),
                _reply(("run_tests", {}), text="verifying"),
                ModelReply(text="Changed add to multiply."),
            ]
        )
        outcome = AgentLoop(self.registry, client).run("make add multiply")

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.turns, 4)
        self.assertEqual(outcome.tool_calls, 3)
        self.assertIn("a * b", (self.project / "calc.py").read_text())
        self.assertEqual(len(outcome.files_touched), 1)
        self.assertTrue(outcome.files_touched[0].endswith("calc.py"))

    def test_model_can_recover_from_a_rejected_edit(self):
        """A refused edit is reported in-band so the next turn can correct it."""
        client = ScriptedClient(
            [
                _reply(
                    ("edit_file", {"path": "calc.py", "old_string": "return a + b", "new_string": "return a +"}),
                ),
                _reply(
                    ("edit_file", {"path": "calc.py", "old_string": "return a + b", "new_string": "return a * b"}),
                ),
                ModelReply(text="fixed"),
            ]
        )
        outcome = AgentLoop(self.registry, client).run("task")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.failed_tool_calls, 1)
        self.assertIn("a * b", (self.project / "calc.py").read_text())

    def test_unknown_tool_is_reported_not_raised(self):
        client = ScriptedClient([_reply(("no_such_tool", {})), ModelReply(text="ok")])
        outcome = AgentLoop(self.registry, client).run("task")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.failed_tool_calls, 1)

    def test_token_usage_accumulates(self):
        client = ScriptedClient([_reply(("read_file", {"path": "x"})), ModelReply(text="x")])
        outcome = AgentLoop(self.registry, client).run("task")
        self.assertEqual(outcome.input_tokens, 10)
        self.assertEqual(outcome.output_tokens, 5)

    def test_events_are_emitted(self):
        seen = []
        client = ScriptedClient([_reply(("read_file", {"path": "x"})), ModelReply(text="x")])
        AgentLoop(self.registry, client, on_event=lambda k, p: seen.append(k)).run("task")
        self.assertIn("turn", seen)
        self.assertIn("tool", seen)
        self.assertIn("done", seen)

    def test_a_broken_event_handler_cannot_break_the_run(self):
        def explode(kind, payload):
            raise ValueError("handler bug")

        outcome = AgentLoop(
            self.registry, ScriptedClient([ModelReply(text="ok")]), on_event=explode
        ).run("task")
        self.assertTrue(outcome.success)

    def test_long_tool_output_is_truncated(self):
        rendered = _render_result(ToolResult.ok("x" * 20000))
        self.assertLess(len(rendered), 20000)
        self.assertIn("truncated", rendered)

    def test_outcome_serialises(self):
        outcome = AgentLoop(self.registry, ScriptedClient([ModelReply(text="ok")])).run("t")
        self.assertEqual(json.loads(json.dumps(outcome.to_dict()))["stop_reason"], "completed")


# ── Adapters ──────────────────────────────────────────────────────────────────


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestClientAdapters(unittest.TestCase):
    def test_anthropic_parses_text_and_tool_use(self):
        class FakeMessages:
            def create(self, **kw):
                return _Block(
                    content=[
                        _Block(type="text", text="thinking"),
                        _Block(type="tool_use", id="tu1", name="read_file", input={"path": "a.py"}),
                    ],
                    usage=_Block(input_tokens=7, output_tokens=3),
                )

        client = AnthropicToolClient(_Block(messages=FakeMessages()), "claude-x")
        reply = client.complete("sys", [], build_coding_registry("."))
        self.assertEqual(reply.text, "thinking")
        self.assertEqual(reply.tool_calls[0].name, "read_file")
        self.assertEqual(reply.tool_calls[0].arguments, {"path": "a.py"})
        self.assertEqual(reply.input_tokens, 7)

    def test_anthropic_tool_result_shape(self):
        client = AnthropicToolClient(_Block(), "m")
        blocks = client.format_tool_results(
            [ToolCall("tu1", "read_file", {})], [ToolResult.fail("nope")]
        )
        block = blocks[0]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "tu1")
        self.assertTrue(block["is_error"])

    def test_openai_parses_tool_calls(self):
        class FakeCompletions:
            def create(self, **kw):
                return _Block(
                    choices=[
                        _Block(
                            message=_Block(
                                content="hi",
                                tool_calls=[
                                    _Block(
                                        id="c1",
                                        function=_Block(
                                            name="grep", arguments='{"pattern": "def"}'
                                        ),
                                    )
                                ],
                            )
                        )
                    ],
                    usage=_Block(prompt_tokens=4, completion_tokens=2),
                )

        client = OpenAIToolClient(
            _Block(chat=_Block(completions=FakeCompletions())), "deepseek-chat"
        )
        reply = client.complete("sys", [], build_coding_registry("."))
        self.assertEqual(reply.tool_calls[0].arguments, {"pattern": "def"})
        self.assertEqual(reply.output_tokens, 2)

    def test_openai_malformed_arguments_do_not_crash(self):
        class FakeCompletions:
            def create(self, **kw):
                return _Block(
                    choices=[
                        _Block(
                            message=_Block(
                                content="",
                                tool_calls=[
                                    _Block(id="c1", function=_Block(name="grep", arguments="{not json"))
                                ],
                            )
                        )
                    ],
                    usage=None,
                )

        client = OpenAIToolClient(
            _Block(chat=_Block(completions=FakeCompletions())), "deepseek-chat"
        )
        reply = client.complete("sys", [], build_coding_registry("."))
        self.assertIn("__malformed__", reply.tool_calls[0].arguments)

    def test_openai_assistant_turn_serialises_arguments(self):
        client = OpenAIToolClient(_Block(), "m")
        turn = client.format_assistant_turn(
            ModelReply(text="t", tool_calls=[ToolCall("c1", "grep", {"pattern": "x"})])
        )
        self.assertEqual(
            json.loads(turn["tool_calls"][0]["function"]["arguments"]), {"pattern": "x"}
        )


if __name__ == "__main__":
    unittest.main()
