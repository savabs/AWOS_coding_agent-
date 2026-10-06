"""
Tests for the no-progress stop, the re-read note and the wall-clock budget in
scaffold/agent/agent_loop.py, and that the orchestrator does not resume them.

A real sqlparse attempt ran 130 turns with no edit (re-reading the same
lines, probing, scanning /) until the 30-minute job kill. The model and the
tools are stubbed, so nothing runs and no key is needed.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

import scaffold.agent.agent_loop as agent_loop
from scaffold.agent.agent_loop import (
    DEFAULT_NO_PROGRESS_TURNS,
    DEFAULT_WALL_S,
    AgentLoop,
    ModelReply,
    ToolCall,
)
from scaffold.agent.orchestrator import _agent_resume_reason
from scaffold.agent.tools.base import ToolResult

NUDGE_TEXT = "turns without changing anything"


class RecordingClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.seen: list[tuple[str, str]] = []

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
    def __init__(self, test_results=None):
        self._tests = list(test_results or [])
        self.executed: list[str] = []

    def execute(self, name, args):
        self.executed.append(name)
        if name == "run_tests":
            passed, failed = self._tests.pop(0) if self._tests else (1, 2)
            return ToolResult.ok(f"{passed} passed, {failed} failed.",
                                 {"passed": passed, "failed": failed})
        if name == "edit_file":
            return ToolResult.ok(f"Edited {args['path']}.", {"path": args["path"]})
        if name == "read_file":
            return ToolResult.ok(f"CONTENT of {args['path']}", {})
        return ToolResult.ok(f"ran {args}")


_counter = iter(range(100_000))


def _call(name, **args):
    return ModelReply(tool_calls=[ToolCall(id=f"c{next(_counter)}", name=name, arguments=args)])


def edit(path="a.py"):
    return _call("edit_file", path=path, old_string="x", new_string=f"y{next(_counter)}")


def run_tests():
    return _call("run_tests", n=next(_counter))


def probe():
    # A different read each turn: never trips the repeat or re-read rules.
    return _call("read_file", path=f"f{next(_counter)}.py")


def _run(replies, test_results=None, registry=None, **kwargs):
    client = RecordingClient(replies)
    kwargs.setdefault("post_green_turns", 0)
    kwargs.setdefault("wall_s", 0)
    loop = AgentLoop(registry or FakeRegistry(test_results), client, max_turns=200, **kwargs)
    return loop.run("task"), client


def _nudged_turns(client):
    return [i + 1 for i, (_, text) in enumerate(client.seen) if NUDGE_TEXT in text]


def test_defaults():
    assert DEFAULT_NO_PROGRESS_TURNS == 25
    assert DEFAULT_WALL_S == 1500


def test_stops_after_n_turns_without_progress():
    outcome, _ = _run([probe() for _ in range(50)], no_progress_turns=5)
    assert outcome.success is False
    assert outcome.stop_reason == "no_progress"
    assert outcome.turns == 6  # the 6th idle turn exceeds 5
    assert "6 turns in a row without changing anything" in outcome.final_message


def test_env_default_and_zero_disables(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_NO_PROGRESS_TURNS", "4")
    outcome, _ = _run([probe() for _ in range(20)])
    assert outcome.stop_reason == "no_progress" and outcome.turns == 5

    monkeypatch.setenv("AWOS_AGENT_NO_PROGRESS_TURNS", "0")
    outcome, client = _run([probe() for _ in range(40)])
    assert outcome.stop_reason == "completed" and outcome.turns == 41
    assert _nudged_turns(client) == []


def test_nudge_once_at_sixty_percent():
    outcome, client = _run([probe() for _ in range(50)], no_progress_turns=10)
    assert outcome.stop_reason == "no_progress"
    # One call per turn: the 6th result (60% of 10) carries the nudge, once.
    assert _nudged_turns(client) == [6]
    assert "You have gone 6 turns without changing anything; make the most likely fix" \
        in client.seen[5][1]
    # The transcript keeps the tool's own text.
    assert NUDGE_TEXT not in outcome.transcript[5]["calls"][0]["detail"]


def test_edit_resets_the_counter_and_rearms_the_nudge():
    replies = [probe() for _ in range(4)] + [edit()] + [probe() for _ in range(50)]
    outcome, client = _run(replies, no_progress_turns=5)
    assert outcome.stop_reason == "no_progress"
    # 4 idle, edit at turn 5, then 6 idle turns: stop at turn 11.
    assert outcome.turns == 11
    # Nudge at the 3rd idle turn of each streak (ceil(0.6 * 5) = 3).
    assert _nudged_turns(client) == [3, 8]


def test_test_improvement_counts_as_progress():
    # Failures drop 5 -> 4 -> 3 at turns 4 and 8, with no edits at all.
    replies = ([run_tests()] + [probe(), probe()]
               + [run_tests()] + [probe(), probe(), probe()]
               + [run_tests()] + [probe() for _ in range(50)])
    tests = [(0, 5), (1, 4), (2, 3)]
    outcome, _ = _run(replies, test_results=tests, no_progress_turns=3)
    assert outcome.stop_reason == "no_progress"
    # Turn 1 is a baseline (idle 1), 2-3 idle, 4 improves; 5-7 idle, 8 improves;
    # then 4 idle turns (9-12) exceed 3.
    assert outcome.turns == 12


def test_same_test_counts_are_not_progress():
    outcome, _ = _run([run_tests() for _ in range(20)],
                      test_results=[(1, 2)] * 20, no_progress_turns=3)
    assert outcome.stop_reason == "no_progress" and outcome.turns == 4


def test_reread_note_after_three_reads_of_the_same_lines():
    registry = FakeRegistry()
    replies = [
        _call("read_file", path="sql.py", start_line=440, end_line=480),
        _call("read_file", path="sql.py", start_line=448, end_line=479),
        _call("read_file", path="./sql.py", start_line="448", end_line="479"),
        _call("read_file", path="sql.py", start_line=450, end_line=470),
    ]
    outcome, client = _run(replies, registry=registry, no_progress_turns=0)
    texts = [t for name, t in client.seen if name == "read_file"]
    assert texts[:3] == ["CONTENT of sql.py", "CONTENT of sql.py", "CONTENT of ./sql.py"]
    assert texts[3].startswith("You already read these lines (sql.py 450-470) at turn 3")
    assert registry.executed.count("read_file") == 3  # the 4th was not re-sent
    assert outcome.reread_notes == 1
    assert outcome.stop_reason == "completed"  # non-breaking


def test_reread_note_resets_after_an_edit_and_skips_new_ranges():
    replies = [_call("read_file", path="b.py", start_line=1, end_line=10) for _ in range(3)]
    # Identical args 3x is fine; a 4th identical call is the repeat rule's job,
    # so vary the range: lines 2-9 are covered, lines 5-20 are not.
    replies += [_call("read_file", path="b.py", start_line=5, end_line=20),
                edit("b.py"),
                _call("read_file", path="b.py", start_line=2, end_line=9)]
    outcome, client = _run(replies, no_progress_turns=0)
    texts = [t for name, t in client.seen if name == "read_file"]
    assert all(t == "CONTENT of b.py" for t in texts)
    assert outcome.reread_notes == 0


def test_wall_budget_stop(monkeypatch):
    clock = {"t": 1000.0}

    class FakeTime:
        @staticmethod
        def monotonic():
            clock["t"] += 100.0  # every reading moves the clock on 100 s
            return clock["t"]

    monkeypatch.setattr(agent_loop, "time", FakeTime)
    outcome, _ = _run([probe() for _ in range(50)], wall_s=1500, no_progress_turns=0)
    assert outcome.success is False
    assert outcome.stop_reason == "wall_budget"
    assert "1500s wall-clock budget" in outcome.final_message
    assert 0 < outcome.turns < 50
    assert outcome.to_dict()["stop_reason"] == "wall_budget"


def test_wall_budget_env_zero_disables(monkeypatch):
    clock = {"t": 0.0}

    class FakeTime:
        @staticmethod
        def monotonic():
            clock["t"] += 1000.0
            return clock["t"]

    monkeypatch.setattr(agent_loop, "time", FakeTime)
    monkeypatch.setenv("AWOS_AGENT_WALL_S", "0")
    client = RecordingClient([probe() for _ in range(10)])
    outcome = AgentLoop(FakeRegistry(), client, max_turns=50, no_progress_turns=0,
                        post_green_turns=0).run("task")
    assert outcome.stop_reason == "completed"


def test_orchestrator_does_not_resume_these_stops():
    verdict = {"success": False, "test_result": None}
    for stop in ("no_progress", "wall_budget"):
        outcome = SimpleNamespace(stop_reason=stop, final_message="x")
        assert _agent_resume_reason(outcome, verdict, 0) is None
    # Unchanged: a turn-budget stop is still resumed.
    outcome = SimpleNamespace(stop_reason="max_turns", final_message="")
    assert _agent_resume_reason(outcome, verdict, 0) is not None
