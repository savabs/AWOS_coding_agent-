"""
Tests for the post-green stop rule in scaffold/agent/agent_loop.py.

Once run_tests passes after an edit, the loop tells the model to check once
each requirement the tests don't cover and then finish, and
if the model keeps going without changing anything it stops the run itself
after AWOS_AGENT_POST_GREEN_TURNS turns, as completed. The model and the tools
are stubbed, so nothing runs and no key is needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.agent_loop import (
    DEFAULT_POST_GREEN_TURNS,
    GREEN_MESSAGE,
    SYSTEM_PROMPT,
    AgentLoop,
    ModelReply,
    ToolCall,
)
from scaffold.agent.tools.base import ToolResult


class RecordingClient:
    """Replays scripted replies; records the tool-result text the model saw."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.seen: list[tuple[str, str]] = []  # (tool name, rendered text)

    def complete(self, system, messages, registry):
        if not self._replies:
            return ModelReply(text="done")
        return self._replies.pop(0)

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text}

    def format_tool_results(self, calls, results):
        out = []
        for c, r in zip(calls, results):
            self.seen.append((c.name, r.text))
            out.append({"role": "user", "content": r.text})
        return out


class FakeRegistry:
    """run_tests returns the next scripted (passed, failed); edits succeed."""

    def __init__(self, test_results=None):
        self._tests = list(test_results or [])

    def execute(self, name, args):
        if name == "run_tests":
            passed, failed = self._tests.pop(0) if self._tests else (3, 0)
            return ToolResult.ok(f"{passed} passed, {failed} failed.",
                                 {"passed": passed, "failed": failed})
        if name == "edit_file":
            return ToolResult.ok(f"Edited {args['path']}.", {"path": args["path"]})
        return ToolResult.ok(f"read {args.get('path', '')}")


_counter = iter(range(10_000))


def _call(name, **args):
    return ModelReply(tool_calls=[ToolCall(id=f"c{next(_counter)}", name=name, arguments=args)])


def edit(path="a.py"):
    return _call("edit_file", path=path, old_string="x", new_string=f"y{next(_counter)}")


def run_tests():
    return _call("run_tests", changed_files=f"a.py{' ' * (next(_counter) % 2)}")


def read(n):
    return _call("read_file", path=f"f{n}.py")


def _run(replies, test_results=None, **kwargs):
    client = RecordingClient(replies)
    loop = AgentLoop(FakeRegistry(test_results), client, max_turns=40, **kwargs)
    return loop.run("task"), client


def _test_texts(client):
    return [text for name, text in client.seen if name == "run_tests"]


# ── (a) the green reminder ────────────────────────────────────────────────────


def test_reminder_after_edit_then_passing_tests():
    outcome, client = _run([edit(), run_tests()], post_green_turns=6)
    assert outcome.success and outcome.stop_reason == "completed"
    assert _test_texts(client) == [f"3 passed, 0 failed.\n\n{GREEN_MESSAGE}"]
    # The trace keeps the tool's own text.
    assert "All tests pass" not in outcome.transcript[1]["calls"][0]["detail"]


def test_no_reminder_without_an_edit():
    _, client = _run([run_tests()], post_green_turns=6)
    assert _test_texts(client) == ["3 passed, 0 failed."]


def test_no_reminder_on_failing_tests():
    _, client = _run([edit(), run_tests()], test_results=[(2, 1)], post_green_turns=6)
    assert GREEN_MESSAGE not in _test_texts(client)[0]


def test_reminder_only_once_per_edit():
    _, client = _run([edit(), run_tests(), run_tests()], post_green_turns=6)
    texts = _test_texts(client)
    assert GREEN_MESSAGE in texts[0] and GREEN_MESSAGE not in texts[1]


# ── (b) the post-green cap ────────────────────────────────────────────────────


def test_cap_stops_as_completed_after_idle_turns():
    replies = [edit(), run_tests()] + [read(i) for i in range(20)]
    outcome, _ = _run(replies, post_green_turns=3)
    assert outcome.success is True
    assert outcome.stop_reason == "completed"
    assert outcome.post_green_stop is True
    assert outcome.to_dict()["post_green_stop"] is True
    # Green at turn 2, then idle turns 3, 4, 5; the 4th idle turn (6) stops.
    assert outcome.turns == 6
    assert outcome.final_message.startswith("Stopped: tests passed 4 turns ago")


def test_no_cap_without_green():
    replies = [edit()] + [read(i) for i in range(10)]
    outcome, _ = _run(replies, post_green_turns=3)
    assert outcome.post_green_stop is False
    assert outcome.turns == 12  # ran to the model's own finish


# ── (c) an edit after green resets it ─────────────────────────────────────────


def test_edit_after_green_resets_counter():
    replies = ([edit(), run_tests(), read(0), read(1), edit(), read(2), read(3), read(4)]
               + [ModelReply(text="finished")])
    outcome, _ = _run(replies, post_green_turns=3)
    # Without the reset, turns 3-6 would be 4 idle turns and stop at turn 6.
    assert outcome.post_green_stop is False
    assert outcome.final_message == "finished"


def test_edit_then_new_green_restarts_count():
    replies = [edit(), run_tests(), read(0), read(1), edit(), run_tests()] + [read(i) for i in range(2, 20)]
    outcome, _ = _run(replies, post_green_turns=3)
    assert outcome.post_green_stop is True
    assert outcome.turns == 10  # green again at 6, idle 7-10


# ── (d) disabled ──────────────────────────────────────────────────────────────


def test_env_zero_disables(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_POST_GREEN_TURNS", "0")
    replies = [edit(), run_tests()] + [read(i) for i in range(15)]
    outcome, _ = _run(replies)
    assert outcome.post_green_stop is False
    assert outcome.final_message == "done"


def test_env_sets_the_cap(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_POST_GREEN_TURNS", "2")
    replies = [edit(), run_tests()] + [read(i) for i in range(15)]
    outcome, _ = _run(replies)
    assert outcome.post_green_stop is True and outcome.turns == 5


# ── (e) failing tests after green clear it ────────────────────────────────────


def test_failing_tests_after_green_clear_it():
    replies = ([edit(), run_tests(), read(0), run_tests(), read(1), read(2), read(3), read(4)]
               + [ModelReply(text="finished")])
    outcome, _ = _run(replies, test_results=[(3, 0), (2, 1)], post_green_turns=3)
    assert outcome.post_green_stop is False
    assert outcome.final_message == "finished"


def test_passing_tests_without_edit_do_not_reset_count():
    replies = [edit(), run_tests(), read(0), run_tests(), read(1)] + [read(i) for i in range(2, 20)]
    outcome, _ = _run(replies, post_green_turns=3)
    assert outcome.post_green_stop is True
    assert outcome.turns == 6


# ── (f) the wording: one check of untested requirements, then finish ────────


def _flat(text):
    return " ".join(text.split())


def test_green_message_asks_for_one_check_of_untested_requirements():
    msg = _flat(GREEN_MESSAGE)
    assert msg.startswith("All tests pass.")
    assert "Before finishing, check once each requirement" in msg
    assert "the tests do not cover" in msg
    assert "failure paths" in msg and "messages" in msg and "formats" in msg
    assert "short summary and no tool calls" in msg
    assert "finish now" not in msg


def test_system_prompt_step3_checks_untested_requirements_once():
    prompt = _flat(SYSTEM_PROMPT)
    assert "Verify with run_tests" in prompt
    assert "For each requirement the tests do not exercise" in prompt
    assert "check it once" in prompt
    assert "fix it if it is wrong" in prompt
    assert "do not re-run the same smoke command" in prompt
    assert "Do not repeat checks already done" in prompt
    # The old blanket ban is gone.
    assert "at most one quick manual check" not in prompt
    assert "Do not write ad-hoc scripts" not in prompt


def test_default_cap_leaves_room_for_a_check_pass():
    # A typical check pass (2-4 idle turns of reads/commands) must not be cut.
    assert DEFAULT_POST_GREEN_TURNS >= 5
    replies = [edit(), run_tests(), read(0), read(1), read(2), read(3),
               ModelReply(text="checked; finished")]
    outcome, _ = _run(replies)
    assert outcome.post_green_stop is False
    assert outcome.final_message == "checked; finished"


def test_default_cap_still_stops_repeated_smoke_checks():
    replies = [edit(), run_tests()] + [read(i) for i in range(20)]
    outcome, _ = _run(replies)
    assert outcome.post_green_stop is True
    assert outcome.turns == 2 + DEFAULT_POST_GREEN_TURNS + 1
