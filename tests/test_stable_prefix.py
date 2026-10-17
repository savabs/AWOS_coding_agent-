"""
T7b — AWOS_STABLE_PREFIX (scaffold/agent/stable_prefix.py): byte-stable
prompt prefixes. No network: model calls are captured, projects are tmp dirs.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent import acceptance, one_shot, providers, stable_prefix
from scaffold.agent.agent_loop import (
    AgentLoop, ModelReply, ToolCall, _condense_history, build_coding_registry,
)
from scaffold.agent.orchestrator import Orchestrator

GOAL = "add() returns a - b; it must return a + b"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("from .calc import add\n")
    (tmp_path / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n")
    (tmp_path / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from pkg import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    big = "\n".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(200))
    (tmp_path / "pkg" / "big.py").write_text(big)
    return tmp_path


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv(stable_prefix.ENV, raising=False)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(stable_prefix.ENV, "1")


def _capture_one_shot(monkeypatch) -> list:
    seen = []

    def fake_call(tap, model, messages, **kw):
        seen.append(messages)
        return one_shot._Reply(text="no blocks here")
    monkeypatch.setattr(one_shot, "_call", fake_call)
    return seen


def _capture_acceptance(monkeypatch) -> list:
    seen = []

    def fake_chat(tap, component, model, messages, **kw):
        seen.append(messages)
        return "```python\ndef test_x():\n    assert True\n```", {"cost_usd": 0.0}
    monkeypatch.setattr(providers, "utility_chat", fake_chat)
    return seen


def _user(messages) -> str:
    return messages[-1]["content"]


# ── flag off: byte-identical ────────────────────────────────────────────────

def test_flag_off_one_shot_identical(project, off, monkeypatch):
    seen = _capture_one_shot(monkeypatch)
    task = "Goal (verbatim):\n" + GOAL + "\n\nFiles to change: pkg/calc.py"
    one_shot.run_one_shot(object(), "m", str(project), task, None)
    expected = one_shot.build_messages(task, one_shot.build_context(str(project), task, None))
    assert seen[0] == expected
    assert "(shown below)" in _user(seen[0])  # the old map, marks and all


def test_flag_off_acceptance_identical(project, off, monkeypatch):
    seen = _capture_acceptance(monkeypatch)
    acceptance.generate_acceptance_tests(GOAL, str(project), None, client=object(), model="m")
    ctx = one_shot.build_context(str(project), GOAL, None,
                                 budget=acceptance.CONTEXT_BUDGET_TOKENS)
    assert seen[0] == acceptance.build_messages(GOAL, ctx.text)


def test_flag_off_feedback_concat_identical(off):
    assert stable_prefix.append_feedback("base", "fb") == "base\n\nfb"


def test_flag_off_agent_loop_uses_old_condense(project, off, monkeypatch):
    called = []
    import scaffold.agent.agent_loop as al
    real = al._condense_history
    monkeypatch.setattr(al, "_condense_history",
                        lambda m, k: called.append(1) or real(m, k))
    client = _ReadingClient(3)
    AgentLoop(registry=build_coding_registry(str(project)), client=client,
              no_progress_turns=0, post_green_turns=0).run("task")
    assert called
    assert client.requests[0][0] == {"role": "user", "content": "task"}


# ── ordering ────────────────────────────────────────────────────────────────

def test_one_shot_order_repo_before_task(project, on, monkeypatch):
    seen = _capture_one_shot(monkeypatch)
    one_shot.run_one_shot(object(), "m", str(project), "TASK-TEXT " + GOAL, None, goal=GOAL)
    msgs = seen[0]
    assert msgs[0] == {"role": "system", "content": stable_prefix.SHARED_SYSTEM}
    user = _user(msgs)
    assert user.startswith("# Repository\n\n## Repository map\n")
    assert user.index("# Repository") < user.index("# Instructions") < user.index("# Task")
    assert "(shown below)" not in user


def test_agent_prompt_stable_order():
    from types import SimpleNamespace
    ctx = {"session": SimpleNamespace(goal=GOAL),
           "exploration": {"exploration_summary": "EXPLORED"},
           "experience": "EXPERIENCE"}
    task = {"action": "fix add", "files": ["pkg/calc.py"], "prev_task_context": "PREV"}
    text = Orchestrator._agent_loop_prompt_stable(task, ctx)
    assert text.index("EXPLORED") < text.index(GOAL) < text.index("Files to change") \
        < text.index("PREV") < text.index("EXPERIENCE")
    # The same parts as the old prompt, only reordered.
    old = Orchestrator._agent_loop_prompt(task, ctx)
    assert sorted(old.replace("# Task\n\n", "").split("\n\n")) == \
        sorted(text.replace("# Task\n\n", "").split("\n\n"))


# ── one shared repository section ───────────────────────────────────────────

def test_shared_repository_section(project, on, monkeypatch):
    acc_seen = _capture_acceptance(monkeypatch)
    shot_seen = _capture_one_shot(monkeypatch)
    acceptance.generate_acceptance_tests(GOAL, str(project), None, client=object(), model="m")
    task = "Goal (verbatim):\n" + GOAL + "\n\nPlanner's note:\nfix calc"
    one_shot.run_one_shot(object(), "m", str(project), task, None, goal=GOAL)
    a, s = acc_seen[0], shot_seen[0]
    assert a[0] == s[0]                       # same system message
    repo = stable_prefix.repository_context(str(project), GOAL).text
    block = stable_prefix.repository_block(repo)
    assert _user(a).startswith(block) and _user(s).startswith(block)
    sa = stable_prefix.serialize(a)
    ss = stable_prefix.serialize(s)
    assert stable_prefix.common_prefix_fraction(sa, ss) > 0.5


def test_repository_section_deterministic(project):
    one = stable_prefix.repository_context(str(project), GOAL).text
    two = stable_prefix.repository_context(str(project), GOAL).text
    assert one == two


# ── feedback appended as new messages ───────────────────────────────────────

def test_agent_loop_list_prompt_appends_messages(project, on):
    client = _ReadingClient(1)
    prompt = stable_prefix.append_feedback(["BASE"], "NOTE")
    prompt = stable_prefix.append_feedback(prompt, "RESUME")
    AgentLoop(registry=build_coding_registry(str(project)), client=client,
              no_progress_turns=0, post_green_turns=0).run(prompt)
    first = client.requests[0]
    assert first[:3] == [{"role": "user", "content": "BASE"},
                         {"role": "user", "content": "NOTE"},
                         {"role": "user", "content": "RESUME"}]


def test_one_shot_repair_continues_conversation(project, on, monkeypatch):
    seen = []
    replies = iter([
        "pkg/calc.py\n<<<<<<< SEARCH\n    totally_absent_line_xyz(q, r, s)\n=======\n    return a + b\n"
        ">>>>>>> REPLACE\n",
        "",
    ])

    def fake_call(tap, model, messages, **kw):
        seen.append(messages)
        return one_shot._Reply(text=next(replies))
    monkeypatch.setattr(one_shot, "_call", fake_call)
    one_shot.run_one_shot(object(), "m", str(project), GOAL, None, goal=GOAL)
    assert len(seen) == 2
    first, repair = seen
    assert repair[:len(first)] == first            # whole first request is the prefix
    assert repair[len(first)]["role"] == "assistant"
    assert "Blocks that failed to apply" in repair[-1]["content"]


# ── block masking ───────────────────────────────────────────────────────────

class _ReadingClient:
    """OpenAI-shaped: every turn reads a different 5-line window of big.py."""

    def __init__(self, turns: int, extra=None):
        self.turns = turns
        self.n = 0
        self.requests = []
        self.extra = extra or {}

    def complete(self, system, messages, registry, tool_choice=None):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        self.n += 1
        if self.n > self.turns:
            return ModelReply(text="done")
        if self.n in self.extra:
            name, args = self.extra[self.n]
        else:
            name, args = "read_file", {"path": "pkg/big.py", "start_line": self.n * 5,
                                       "end_line": self.n * 5 + 4}
        return ModelReply(text="", tool_calls=[ToolCall(id=f"c{self.n}", name=name,
                                                        arguments=args)])

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text or None,
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.name,
                                             "arguments": json.dumps(c.arguments)}}
                               for c in reply.tool_calls]}

    def format_tool_results(self, calls, results):
        from scaffold.agent.agent_loop import _render_result
        return [{"role": "tool", "tool_call_id": c.id, "content": _render_result(r)}
                for c, r in zip(calls, results)]


def _run(project, turns, extra=None):
    client = _ReadingClient(turns, extra)
    AgentLoop(registry=build_coding_registry(str(project)), client=client, keep_turns=8,
              max_turns=turns + 2, no_progress_turns=0, post_green_turns=0,
              max_repeats=99).run("TASK")
    return client.requests


def _extends(prev: list, cur: list) -> bool:
    return cur[:len(prev)] == prev


def test_block_masking_stable_between_points(project, on, monkeypatch):
    monkeypatch.setenv(stable_prefix.BLOCK_ENV, "4")
    reqs = _run(project, 24)
    breaks = [k for k in range(1, len(reqs)) if not _extends(reqs[k - 1], reqs[k])]
    # Calls are 1-based; call k+1 sees k assistant turns. Masking happens when
    # (turns - 8) reaches 4, 8, 12 -> at calls 13, 17, 21, 25 only.
    assert [k + 1 for k in breaks] == [13, 17, 21, 25]


def test_old_condense_rewrites_every_turn(project, off):
    reqs = _run(project, 24)
    breaks = [k for k in range(1, len(reqs)) if not _extends(reqs[k - 1], reqs[k])]
    assert len(breaks) >= 15     # every call from turn 10 on


def test_pinned_ids():
    events = [("r1", "read", "/a", False), ("t1", "test", None, True),
              ("r2", "read", "/a", False), ("e1", "edit", "/a", False),
              ("r3", "read", "/a", False), ("r4", "read", "/b", False),
              ("t2", "test", None, False)]
    assert stable_prefix.pinned_ids(events) == {"r2", "t1"}


def test_pinned_results_never_masked():
    msgs = [{"role": "user", "content": "TASK"}]
    for i in range(20):
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "x", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 500})
    stable_prefix.condense_blocks(msgs, 8, 4, pins={"c0", "c3"})
    tools = {m["tool_call_id"]: m["content"] for m in msgs if m.get("role") == "tool"}
    assert tools["c0"] == "y" * 500 and tools["c3"] == "y" * 500
    assert tools["c1"].startswith("[earlier tool output elided")
    # 20 turns, keep 8, block 4: 12 masked (c0..c11), c12.. kept.
    assert tools["c11"].startswith("[earlier") and tools["c12"] == "y" * 500
    # Anthropic dialect too.
    amsgs = [{"role": "user", "content": "TASK"}]
    for i in range(13):
        amsgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"u{i}"}]})
        amsgs.append({"role": "user", "content": [{"type": "tool_result",
                                                   "tool_use_id": f"u{i}",
                                                   "content": "z" * 500}]})
    stable_prefix.condense_blocks(amsgs, 8, 4, pins={"u1"})
    blocks = {m["content"][0]["tool_use_id"]: m["content"][0]["content"]
              for m in amsgs[1:] if m["role"] == "user"}
    assert blocks["u1"] == "z" * 500 and blocks["u0"].startswith("[earlier")
    assert blocks["u4"] == "z" * 500


def test_loop_pins_last_read_of_edited_file(project, on, monkeypatch):
    monkeypatch.setenv(stable_prefix.BLOCK_ENV, "4")
    extra = {1: ("read_file", {"path": "pkg/calc.py"}),
             2: ("edit_file", {"path": "pkg/calc.py", "old_string": "return a - b",
                               "new_string": "return a + b"})}
    reqs = _run(project, 20, extra)
    last = reqs[-1]
    first_read = [m for m in last if m.get("tool_call_id") == "c1"][0]
    assert not first_read["content"].startswith("[earlier")   # pinned
    other = [m for m in last if m.get("tool_call_id") == "c3"][0]
    assert other["content"].startswith("[earlier")


# ── measurement helper ──────────────────────────────────────────────────────

def test_common_prefix_fraction():
    assert stable_prefix.common_prefix_fraction("abcX", "abcY") == 0.75
    assert stable_prefix.common_prefix_fraction("", "") == 1.0
