"""
The loop executor with its reliability and cost layers wired in (rea_synthesis
A6 routing, A7 stuck detector, C9 checkpoint, C10 compaction;
docs/specs/loop_reliability_ab.md). Fakes only: scripted clients, a /bin/sh
sandbox. No model call, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent.agent_loop import ModelReply  # noqa: E402
from scaffold.agent.loop_checkpoint import LoopCheckpoint  # noqa: E402
from test_loop_executor import EDIT, FakeSandbox, Scripted, _ws, bash, submit  # noqa: E402

GOAL = "Fix add() in calc.py"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("AWOS_LOOP_STUCK", "AWOS_LOOP_COMPACT", "AWOS_LOOP_CONTEXT_TOKENS",
              "AWOS_LOOP_GRAMMAR", "AWOS_PROVIDER", "AWOS_AGENT_MODEL", "AWOS_LOOP_CHECKPOINT"):
        monkeypatch.delenv(k, raising=False)


def _exec(ws, client, **kw):
    sandbox = kw.pop("sandbox", None) or FakeSandbox(ws)
    ex = lx.LoopExecutor(client, lx.build_loop_registry(sandbox), GOAL, workspace=str(ws), **kw)
    return ex, sandbox


def _texts(messages) -> str:
    return json.dumps(messages)


FAIL = "cat does_not_exist.txt"


# ── stuck detector ───────────────────────────────────────────────────────────


def test_stuck_log_mode_counts_but_leaves_prompt_unchanged(tmp_path):
    ws = _ws(tmp_path)
    client = Scripted([bash(FAIL) for _ in range(5)] + [bash(EDIT), submit()])
    ex, _ = _exec(ws, client, gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.success
    assert run.stuck["mode"] == "log"
    assert run.stuck["triggers"]["repeat_command"] >= 1
    assert "[harness note]" not in _texts(client.seen)
    assert run.component()["stuck"]["triggers"]["repeat_command"] >= 1


def test_stuck_off_mode_runs_no_detector(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_STUCK", "off")
    ws = _ws(tmp_path)
    ex, _ = _exec(ws, Scripted([bash(FAIL), submit()]), gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.stuck is None and "stuck" not in run.component()


def test_stuck_nudge_rides_in_the_tool_result_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_STUCK", "nudge")
    ws = _ws(tmp_path)
    client = Scripted([bash(FAIL) for _ in range(4)] + [bash(EDIT), submit()])
    ex, _ = _exec(ws, client, gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.success and run.stuck["nudges"] >= 1
    last = client.seen[-1]
    noted = [m for m in last if "[harness note]" in json.dumps(m)]
    assert noted, "the nudge should reach the model"
    # The note is a text block inside the tool_result user turn: roles alternate.
    roles = [m["role"] for m in last]
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert noted[0]["role"] == "user" and isinstance(noted[0]["content"], list)


def test_stuck_never_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_STUCK", "nudge")
    ws = _ws(tmp_path)
    ex, _ = _exec(ws, Scripted([bash(FAIL) for _ in range(30)]), max_steps_=30)
    run = ex.run()
    assert run.steps == 30 and run.stop_reason == "max_steps"


# ── routing / escalation ─────────────────────────────────────────────────────


class Ladder:
    """escalate(reason) over a list of clients, recording each call."""

    def __init__(self, *clients):
        self.clients = list(clients)
        self.reasons: list[str] = []

    def __call__(self, reason):
        self.reasons.append(reason)
        return self.clients.pop(0) if self.clients else None


class Strong(Scripted):
    model = "claude-sonnet-5-5"


def test_escalates_when_stuck_and_hands_over(tmp_path):
    ws = _ws(tmp_path)
    cheap = Scripted([bash(FAIL) for _ in range(20)])
    strong = Strong([bash(EDIT), submit()])
    ladder = Ladder(strong)
    ex, _ = _exec(ws, cheap, gate=lambda f, s: {"green": True}, escalate=ladder)
    run = ex.run()
    assert run.success and run.rung == 1
    assert run.escalations[0]["reason"].startswith("stuck")
    assert run.escalations[0]["from"] == "claude-haiku-5-5"
    assert run.escalations[0]["to"] == "claude-sonnet-5-5"
    first = strong.seen[0]
    assert len(first) == 1 and GOAL in first[0]["content"]
    assert "handed it over" in first[0]["content"] and FAIL in first[0]["content"]
    assert "earlier_rungs" in run.stuck
    assert ws.joinpath("calc.py").read_text().count("a + b") == 1


def test_escalates_when_the_rung_allowance_is_spent(tmp_path):
    ws = _ws(tmp_path)
    cheap = Scripted([bash(f"echo {i}") for i in range(10)])
    strong = Strong([bash(EDIT), submit()])
    ex, _ = _exec(ws, cheap, gate=lambda f, s: {"green": True}, escalate=Ladder(strong),
                  rung_steps_=3)
    run = ex.run()
    assert run.success and len(cheap.seen) == 3
    assert "allowance" in run.escalations[0]["reason"] and run.escalations[0]["step"] == 3


def test_escalates_after_a_red_gate_with_the_failures(tmp_path):
    ws = _ws(tmp_path)
    verdicts = iter([{"green": False, "retry": False, "feedback": "FAILED test_add"},
                     {"green": True}])
    cheap = Scripted([submit("done?")])
    strong = Strong([bash(EDIT), submit()])
    ex, _ = _exec(ws, cheap, gate=lambda f, s: next(verdicts), escalate=Ladder(strong))
    run = ex.run()
    assert run.success and run.stop_reason == "submitted" and run.submits == 2
    assert "gate red" in run.escalations[0]["reason"]
    assert "FAILED test_add" in strong.seen[0][0]["content"]


def test_escalates_on_a_model_error(tmp_path):
    class Broken(Scripted):
        def complete(self, system, messages, registry):
            raise ConnectionError("local server down")

    ws = _ws(tmp_path)
    strong = Strong([bash(EDIT), submit()])
    ex, _ = _exec(ws, Broken([]), gate=lambda f, s: {"green": True}, escalate=Ladder(strong))
    run = ex.run()
    assert run.success and "ConnectionError" in run.escalations[0]["reason"]


def test_top_of_the_ladder_keeps_going_and_asks_once(tmp_path):
    ws = _ws(tmp_path)
    ladder = Ladder()  # nothing above
    cheap = Scripted([bash(FAIL) for _ in range(12)] + [bash(EDIT), submit()])
    ex, _ = _exec(ws, cheap, gate=lambda f, s: {"green": True}, escalate=ladder,
                  rung_steps_=2)
    run = ex.run()
    assert run.success and run.rung == 0 and not run.escalations
    assert len(ladder.reasons) == 1


def test_gate_red_without_routing_still_stops(tmp_path):
    ws = _ws(tmp_path)
    ex, _ = _exec(ws, Scripted([submit()]),
                  gate=lambda f, s: {"green": False, "retry": False})
    run = ex.run()
    assert run.stop_reason == "gate_red" and not run.success


# ── checkpoint / resume ──────────────────────────────────────────────────────


class Killed(BaseException):
    """Stands in for SIGKILL: not an Exception, so nothing in the loop catches it."""


class DiesAt(Scripted):
    def __init__(self, replies, die_at):
        super().__init__(replies)
        self.die_at = die_at

    def complete(self, system, messages, registry):
        if len(self.seen) + 1 == self.die_at:
            raise Killed()
        return super().complete(system, messages, registry)


def test_killed_run_resumes_the_same_conversation(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    ck = LoopCheckpoint(path, "k1")
    first = DiesAt([bash("echo one"), bash(EDIT)], die_at=3)
    ex, sandbox = _exec(ws, first, checkpoint=ck, gate=lambda f, s: {"green": True})
    with pytest.raises(Killed):
        ex.run()
    assert len(sandbox.commands) == 2
    saved = LoopCheckpoint(path, "k1").load()
    assert saved is not None and saved[1]["steps"] == 2 and saved[1]["files_changed"] == ["calc.py"]

    second = Scripted([submit()])
    ex2, sandbox2 = _exec(ws, second, checkpoint=LoopCheckpoint(path, "k1"),
                          gate=lambda f, s: {"green": True})
    run = ex2.run()
    assert run.resumed and run.success and run.steps == 3
    assert sandbox2.commands == []  # nothing re-run
    assert second.seen[0] == saved[0]  # byte-identical history
    assert run.files_changed == ["calc.py"]  # edits made before the kill still reported
    assert LoopCheckpoint(path, "k1").load() is None  # finished: not resumable again


def test_another_task_key_starts_fresh(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    with pytest.raises(Killed):
        _exec(ws, DiesAt([bash("echo one")], die_at=2), checkpoint=LoopCheckpoint(path, "a"))[0].run()
    client = Scripted([submit()])
    run = _exec(ws, client, checkpoint=LoopCheckpoint(path, "b"),
                gate=lambda f, s: {"green": True})[0].run()
    assert not run.resumed and run.steps == 1 and len(client.seen[0]) == 1


def test_model_error_leaves_the_checkpoint_resumable(tmp_path):
    class Broken(Scripted):
        def complete(self, system, messages, registry):
            if self.replies:
                return super().complete(system, messages, registry)
            raise ConnectionError("down")

    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    run = _exec(ws, Broken([bash("echo one")]), checkpoint=LoopCheckpoint(path, "k"))[0].run()
    assert run.stop_reason == "model_error"
    assert LoopCheckpoint(path, "k").load() is not None


def test_resume_cost_cap_counts_only_this_attempt(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    with pytest.raises(Killed):
        _exec(ws, DiesAt([bash("echo 1", tin=100_000), bash("echo 2", tin=100_000)], die_at=3),
              checkpoint=LoopCheckpoint(path, "k"))[0].run()
    spent = LoopCheckpoint(path, "k").load()[1]["cost_usd"]
    assert spent > 0
    run = _exec(ws, Scripted([submit()]), checkpoint=LoopCheckpoint(path, "k"),
                cost_cap=spent * 0.9, gate=lambda f, s: {"green": True})[0].run()
    assert run.success and run.cost_usd > spent


def test_resume_after_an_escalation_returns_to_that_rung(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    cheap = Scripted([bash("echo a"), bash("echo b")])
    strong = DiesAt([bash("echo strong")], die_at=2)
    strong.model = "claude-sonnet-5-5"
    with pytest.raises(Killed):
        _exec(ws, cheap, checkpoint=LoopCheckpoint(path, "k"), escalate=Ladder(strong),
              rung_steps_=2)[0].run()
    assert LoopCheckpoint(path, "k").load()[1]["rung"] == 1

    strong2 = Strong([submit()])
    ladder = Ladder(strong2)
    run = _exec(ws, Scripted([]), checkpoint=LoopCheckpoint(path, "k"), escalate=ladder,
                rung_steps_=2, gate=lambda f, s: {"green": True})[0].run()
    assert run.resumed and run.success and ladder.reasons == ["resume"]
    assert "handed it over" in strong2.seen[0][0]["content"]


# ── compaction ───────────────────────────────────────────────────────────────


class Summarising(Scripted):
    """Answers a compaction summary request with a fixed summary."""

    def __init__(self, replies):
        super().__init__(replies)
        self.summaries = 0

    def complete(self, system, messages, registry):
        if len(messages) == 1 and "<transcript>" in str(messages[0].get("content")):
            self.summaries += 1
            return ModelReply(text="SUMMARY: tried echo a few times.", input_tokens=50,
                              output_tokens=10)
        return super().complete(system, messages, registry)


def test_compaction_swaps_in_a_summary_and_keeps_the_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_CONTEXT_TOKENS", "1000")
    ws = _ws(tmp_path)
    replies = [bash(f"echo {i}", tin=100 * (i + 1)) for i in range(12)] + [bash(EDIT), submit()]
    client = Summarising(replies)
    ex, _ = _exec(ws, client, compact=True, gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.success and client.summaries >= 1
    assert run.compaction["summaries"] >= 1
    after = client.seen[-1]
    assert GOAL in after[0]["content"] if isinstance(after[0]["content"], str) \
        else GOAL in json.dumps(after[0]["content"])
    assert "SUMMARY: tried echo" in json.dumps(after[0])
    seen = client.seen
    assert any(len(b) < len(a) for a, b in zip(seen, seen[1:])), "a request should shrink"
    # the summary call is accounted like any other call
    assert run.input_tokens >= sum(100 * (i + 1) for i in range(12)) + 50


def test_compaction_off_by_default(tmp_path):
    ws = _ws(tmp_path)
    ex, _ = _exec(ws, Scripted([bash("echo 1"), submit()]), gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.compaction is None and ex.compactor is None


# ── orchestrator wiring ──────────────────────────────────────────────────────


def test_orchestrator_routes_cheap_first_and_logs_labelled_decisions(tmp_path, monkeypatch):
    from unittest.mock import patch

    from test_loop_executor import GREEN, _run_task

    log = tmp_path / "route.jsonl"
    monkeypatch.setenv("AWOS_ROUTE_LOG", str(log))
    monkeypatch.setenv("AWOS_LOOP_ROUTE", "cheap-x,claude-sonnet-5-5")
    monkeypatch.setenv("AWOS_LOOP_ROUTE_RUNG_STEPS", "2")
    cheap = Scripted([bash("echo look"), bash("echo look again")])
    cheap.model = "cheap-x"
    strong = Strong([bash(EDIT), submit()])
    built = {"cheap-x": cheap, "claude-sonnet-5-5": strong}
    with patch("scaffold.agent.loop_route.build_route_client", side_effect=built.__getitem__):
        r = _run_task(tmp_path, [], [GREEN], monkeypatch)
    assert r.out["success"] is True and r.orch._last_agent_model == "claude-sonnet-5-5"
    comp = r.orch._components["loop"]
    assert comp["rung"] == 1 and comp["escalations"][0]["to"] == "claude-sonnet-5-5"
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert [row.get("start") or row.get("to") for row in rows] == ["cheap-x", "claude-sonnet-5-5"]
    assert all(row["label_solved"] is True for row in rows)


def test_orchestrator_checkpoints_when_the_host_names_a_path(tmp_path, monkeypatch):
    from test_loop_executor import GREEN, _run_task

    path = tmp_path / "job" / "loop_checkpoint.jsonl"
    path.parent.mkdir()
    monkeypatch.setenv("AWOS_LOOP_CHECKPOINT_PATH", str(path))
    r = _run_task(tmp_path, [bash(EDIT), submit()], [GREEN], monkeypatch)
    assert r.out["success"] is True
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines[-1]["t"] == "finish" and lines[-1]["stop_reason"] == "submitted"


def test_orchestrator_without_checkpoint_path_writes_none(tmp_path, monkeypatch):
    from test_loop_executor import GREEN, _run_task

    monkeypatch.delenv("AWOS_LOOP_CHECKPOINT_PATH", raising=False)
    r = _run_task(tmp_path, [bash(EDIT), submit()], [GREEN], monkeypatch)
    assert r.out["success"] is True and not list(tmp_path.rglob("loop_checkpoint*"))
