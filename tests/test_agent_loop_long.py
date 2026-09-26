"""
Tests for the long-task behaviour of scaffold/agent/agent_loop.py.

  TestTurnBudget   — AWOS_AGENT_MAX_TURNS, default, garbage, explicit override
  TestNudge        — an empty finish is pushed back when edits are required
  TestCondensing   — old tool output is stubbed; pairing and short runs intact
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent import agent_loop as al
from scaffold.agent.agent_loop import (
    DEFAULT_MAX_TURNS,
    NUDGE_MESSAGE,
    AgentLoop,
    AnthropicToolClient,
    ModelReply,
    OpenAIToolClient,
    ToolCall,
    build_coding_registry,
)


def _env(**values):
    """Patch the loop's env vars; None removes one."""
    base = {k: v for k, v in os.environ.items()
            if k not in ("AWOS_AGENT_MAX_TURNS", "AWOS_AGENT_KEEP_TURNS")}
    base.update({k: v for k, v in values.items() if v is not None})
    return mock.patch.dict(os.environ, base, clear=True)


class _Scripted:
    """Scripted replies; snapshots every payload the loop sends."""

    def _init_script(self, replies):
        self._replies = list(replies)
        self.sent = []

    def complete(self, system, messages, registry):
        self.sent.append(copy.deepcopy(messages))
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


class ScriptedAnthropic(_Scripted, AnthropicToolClient):
    def __init__(self, replies):
        AnthropicToolClient.__init__(self, client=None, model="replay")
        self._init_script(replies)


class ScriptedOpenAI(_Scripted, OpenAIToolClient):
    def __init__(self, replies):
        OpenAIToolClient.__init__(self, client=None, model="replay")
        self._init_script(replies)


_ids = iter(range(10**6))


def _call(name, args):
    return ModelReply(tool_calls=[ToolCall(id=f"c{next(_ids)}", name=name, arguments=args)])


def _project():
    d = Path(tempfile.mkdtemp())
    (d / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n" + "# padding line\n" * 40, encoding="utf-8"
    )
    return d


def _edit():
    return _call("edit_file", {"path": "calc.py", "old_string": "a + b", "new_string": "a * b"})


def _reads(n):
    # Distinct arguments so the repeated-call guard never trips.
    return [_call("read_file", {"path": "calc.py", "start_line": i + 1}) for i in range(n)]


class TestTurnBudget(unittest.TestCase):
    def setUp(self):
        self.registry = build_coding_registry(str(_project()))

    def _loop(self, **kw):
        return AgentLoop(self.registry, ScriptedAnthropic([]), **kw)

    def test_default_is_60(self):
        self.assertEqual(DEFAULT_MAX_TURNS, 60)
        with _env():
            self.assertEqual(self._loop().max_turns, 60)

    def test_env_sets_budget(self):
        with _env(AWOS_AGENT_MAX_TURNS="25"):
            self.assertEqual(self._loop().max_turns, 25)

    def test_garbage_env_falls_back(self):
        with _env(AWOS_AGENT_MAX_TURNS="lots"):
            self.assertEqual(self._loop().max_turns, DEFAULT_MAX_TURNS)

    def test_explicit_wins_over_env(self):
        with _env(AWOS_AGENT_MAX_TURNS="25"):
            self.assertEqual(self._loop(max_turns=7).max_turns, 7)

    def test_env_budget_is_enforced(self):
        with _env(AWOS_AGENT_MAX_TURNS="3"):
            loop = AgentLoop(self.registry, ScriptedAnthropic(_reads(10)))
            outcome = loop.run("read forever")
        self.assertEqual(outcome.stop_reason, "max_turns")
        self.assertEqual(outcome.turns, 3)


class TestNudge(unittest.TestCase):
    def setUp(self):
        self.root = _project()
        self.registry = build_coding_registry(str(self.root))

    def test_nudge_then_edit_succeeds(self):
        events = []
        client = ScriptedAnthropic(
            [ModelReply(text="Looks fine to me."), _edit(), ModelReply(text="changed")]
        )
        loop = AgentLoop(
            self.registry, client, require_edits=True,
            on_event=lambda kind, payload: events.append(kind),
        )
        outcome = loop.run("make add multiply")

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.nudges, 1)
        self.assertEqual(outcome.to_dict()["nudges"], 1)
        self.assertEqual(len(outcome.files_touched), 1)
        self.assertTrue(outcome.files_touched[0].endswith("calc.py"))
        self.assertIn("a * b", (self.root / "calc.py").read_text())
        self.assertIn("nudge", events)
        # Second payload: task, the assistant's text, then the nudge.
        second = client.sent[1]
        self.assertEqual(second[-2]["role"], "assistant")
        self.assertEqual(second[-1], {"role": "user", "content": NUDGE_MESSAGE})

    def test_empty_text_finish_not_appended_as_assistant(self):
        client = ScriptedAnthropic([ModelReply(text=""), _edit(), ModelReply(text="ok")])
        outcome = AgentLoop(self.registry, client, require_edits=True).run("t")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.nudges, 1)
        self.assertEqual([m["role"] for m in client.sent[1]], ["user", "user"])

    def test_no_nudge_without_require_edits(self):
        client = ScriptedAnthropic([ModelReply(text="nothing to do")])
        outcome = AgentLoop(self.registry, client).run("t")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.nudges, 0)
        self.assertEqual(len(client.sent), 1)

    def test_no_nudge_once_edited(self):
        client = ScriptedAnthropic([_edit(), ModelReply(text="done")])
        outcome = AgentLoop(self.registry, client, require_edits=True).run("t")
        self.assertEqual(outcome.nudges, 0)
        self.assertEqual(outcome.stop_reason, "completed")

    def test_at_most_two_nudges(self):
        client = ScriptedAnthropic([ModelReply(text=f"no {i}") for i in range(5)])
        outcome = AgentLoop(self.registry, client, require_edits=True).run("t")
        self.assertEqual(outcome.nudges, 2)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(len(client.sent), 3)
        self.assertEqual(outcome.final_message, "no 2")

    def test_nudge_openai_dialect(self):
        client = ScriptedOpenAI([ModelReply(text="fine"), _edit(), ModelReply(text="ok")])
        outcome = AgentLoop(self.registry, client, require_edits=True).run("t")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.nudges, 1)
        self.assertEqual(client.sent[1][-1], {"role": "user", "content": NUDGE_MESSAGE})


def _check_anthropic_pairing(messages):
    for i, m in enumerate(messages):
        if m["role"] == "assistant":
            ids = [b["id"] for b in m["content"] if b["type"] == "tool_use"]
            if ids:
                nxt = messages[i + 1]
                got = [b["tool_use_id"] for b in nxt["content"] if b["type"] == "tool_result"]
                assert got == ids, (ids, got)


def _check_openai_pairing(messages):
    for i, m in enumerate(messages):
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = [c["id"] for c in m["tool_calls"]]
            got = [x["tool_call_id"] for x in messages[i + 1 : i + 1 + len(ids)]]
            assert got == ids, (ids, got)
            assert all(x["role"] == "tool" for x in messages[i + 1 : i + 1 + len(ids)])


def _result_contents(messages):
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append(m["content"])
        elif m["role"] == "user" and isinstance(m["content"], list):
            out += [b["content"] for b in m["content"] if b.get("type") == "tool_result"]
    return out


class TestCondensing(unittest.TestCase):
    def setUp(self):
        self.registry = build_coding_registry(str(_project()))

    def _run(self, client_cls, n_reads, keep="3"):
        client = client_cls(_reads(n_reads) + [ModelReply(text="done")])
        with _env(AWOS_AGENT_KEEP_TURNS=keep):
            outcome = AgentLoop(self.registry, client, max_turns=50).run("read a lot")
        return client, outcome

    def _assert_condensed(self, client, outcome, keep, check_pairing):
        final = client.sent[-1]
        check_pairing(final)
        contents = _result_contents(final)
        # The newest `keep` results are verbatim; everything older is a stub.
        old, recent = contents[:-keep], contents[-keep:]
        self.assertTrue(old)
        self.assertTrue(all(c.startswith("[earlier tool output elided — ") for c in old))
        self.assertTrue(all("re-read the file if you need it]" in c for c in old))
        self.assertTrue(all(not c.startswith("[earlier") for c in recent))
        self.assertEqual(outcome.elided_results, len(old))
        self.assertEqual(outcome.to_dict()["elided_results"], len(old))
        # The task prompt and every assistant message are untouched.
        self.assertEqual(final[0], client.sent[0][0])
        self.assertTrue(outcome.success)

    def test_anthropic_condenses_old_results(self):
        client, outcome = self._run(ScriptedAnthropic, 8)
        self._assert_condensed(client, outcome, 3, _check_anthropic_pairing)

    def test_openai_condenses_old_results(self):
        client, outcome = self._run(ScriptedOpenAI, 8)
        self._assert_condensed(client, outcome, 3, _check_openai_pairing)

    def test_assistant_messages_untouched(self):
        client, _ = self._run(ScriptedOpenAI, 8)
        final = client.sent[-1]
        n = len(final)
        # Any message that also appeared in an earlier payload and is an
        # assistant message must be identical to its first appearance.
        for i in range(n):
            if final[i]["role"] == "assistant":
                first = next(p for p in client.sent if len(p) > i)
                self.assertEqual(final[i], first[i])

    def test_short_conversation_byte_identical(self):
        client, outcome = self._run(ScriptedAnthropic, 3, keep="3")
        self.assertEqual(outcome.elided_results, 0)
        # Each payload is a strict, unmodified extension of the previous one.
        for prev, nxt in zip(client.sent, client.sent[1:]):
            self.assertEqual(
                json.dumps(prev, sort_keys=True), json.dumps(nxt[: len(prev)], sort_keys=True)
            )

    def test_default_keep_leaves_twelve_turn_runs_short_enough(self):
        # 8 assistant turns at the default keep of 8 are not touched.
        client, outcome = self._run(ScriptedAnthropic, 8, keep=None)
        self.assertEqual(outcome.elided_results, 0)

    def test_idempotent(self):
        client, _ = self._run(ScriptedAnthropic, 8)
        messages = copy.deepcopy(client.sent[-1])
        before = json.dumps(messages, sort_keys=True)
        self.assertEqual(al._condense_history(messages, 3), 0)
        self.assertEqual(json.dumps(messages, sort_keys=True), before)

    def test_garbage_keep_env_uses_default(self):
        with _env(AWOS_AGENT_KEEP_TURNS="many"):
            loop = AgentLoop(self.registry, ScriptedAnthropic([]))
        self.assertEqual(loop.keep_turns, al.DEFAULT_KEEP_TURNS)


if __name__ == "__main__":
    unittest.main()
