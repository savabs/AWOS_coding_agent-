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
    # without compaction request i (0-based) holds 2*i + 1 messages
    assert any(len(req) < 2 * i + 1 for i, req in enumerate(seen)), "history should shrink"
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


# ── per-rung step budget ─────────────────────────────────────────────────────


def test_final_rung_gets_full_max_steps_from_its_own_start(tmp_path):
    ws = _ws(tmp_path)
    cheap = Scripted([bash(f"echo c{i}") for i in range(10)])
    strong = Strong([bash(f"echo s{i}") for i in range(20)])
    ex, _ = _exec(ws, cheap, escalate=Ladder(strong), rung_steps_=3, max_steps_=5)
    run = ex.run()
    assert run.stop_reason == "max_steps" and run.rung == 1
    assert len(cheap.seen) == 3 and len(strong.seen) == 5 and run.steps == 8


def test_non_final_rung_out_of_max_steps_hands_over(tmp_path):
    ws = _ws(tmp_path)
    cheap = Scripted([bash(f"echo c{i}") for i in range(10)])
    strong = Strong([bash(EDIT), submit()])
    ex, _ = _exec(ws, cheap, escalate=Ladder(strong), max_steps_=3,
                  gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.success and len(cheap.seen) == 3
    assert "step budget" in run.escalations[0]["reason"]


def test_cost_cap_stays_per_task_across_rungs(tmp_path):
    ws = _ws(tmp_path)
    cheap = Scripted([bash(f"echo c{i}", tin=200_000) for i in range(10)])
    strong = Strong([bash(f"echo s{i}", tin=200_000) for i in range(10)])
    ex, _ = _exec(ws, cheap, escalate=Ladder(strong), rung_steps_=2, max_steps_=50,
                  cost_cap=0.5)
    run = ex.run()
    assert run.stop_reason == "cost_cap" and run.rung == 1 and run.steps < 10


def test_escalation_event_carries_rung_steps_cost_and_stuck(tmp_path):
    ws = _ws(tmp_path)
    cheap = Scripted([bash(f"echo c{i}", tin=1000) for i in range(5)])
    strong = Strong([bash(EDIT), submit()])
    ex, _ = _exec(ws, cheap, gate=lambda f, s: {"green": True}, escalate=Ladder(strong),
                  rung_steps_=3)
    run = ex.run()
    ev = run.escalations[0]
    assert ev["rung_steps"] == 3 and ev["rung_cost_usd"] > 0
    assert ev["stuck"]["mode"] == "log" and "triggers" in ev["stuck"]
    assert json.dumps(ev)  # goes to the labelled route log as is


# ── model errors ─────────────────────────────────────────────────────────────


class AuthError(Exception):
    status_code = 401


class RateLimitError(Exception):
    status_code = 429


class FailsAfter(Scripted):
    """Replies normally, then raises `exc` once the replies run out."""

    def __init__(self, replies, exc):
        super().__init__(replies)
        self.exc = exc

    def complete(self, system, messages, registry):
        if self.replies:
            return super().complete(system, messages, registry)
        self.seen.append(list(messages))
        raise self.exc


def test_auth_error_at_the_top_stops_and_finishes_the_checkpoint(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    run = _exec(ws, FailsAfter([bash("echo one")], AuthError("invalid x-api-key")),
                checkpoint=LoopCheckpoint(path, "k"))[0].run()
    assert run.stop_reason == "model_error" and run.error_kind == "auth"
    assert run.component()["error_kind"] == "auth"
    assert LoopCheckpoint(path, "k").load() is None  # a retry will not fix a bad key


def test_transient_error_leaves_the_checkpoint_resumable(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    run = _exec(ws, FailsAfter([bash("echo one")], RateLimitError("slow down")),
                checkpoint=LoopCheckpoint(path, "k"))[0].run()
    assert run.stop_reason == "model_error" and run.error_kind == "rate_limit"
    assert LoopCheckpoint(path, "k").load() is not None


def test_auth_error_on_a_lower_rung_still_escalates(tmp_path):
    ws = _ws(tmp_path)
    strong = Strong([bash(EDIT), submit()])
    run = _exec(ws, FailsAfter([], AuthError("no key")), gate=lambda f, s: {"green": True},
                escalate=Ladder(strong))[0].run()
    assert run.success and "auth" in run.escalations[0]["reason"]
    assert run.error_kind is None and run.model_errors == ["auth"]


# ── context overflow ─────────────────────────────────────────────────────────


class Overflow(Exception):
    status_code = 400

    def __init__(self):
        super().__init__("prompt is too long: 250000 tokens > 200000 maximum")


class OverflowsWhenLong(Summarising):
    """Raises a context overflow while the request holds > `limit` messages."""

    def __init__(self, replies, limit, summary_fails=False):
        super().__init__(replies)
        self.limit = limit
        self.overflows = 0
        self.summary_fails = summary_fails

    def complete(self, system, messages, registry):
        is_summary = len(messages) == 1 and "<transcript>" in str(messages[0].get("content"))
        if is_summary and self.summary_fails:
            raise RuntimeError("summary model down")
        if not is_summary and len(messages) > self.limit:
            self.overflows += 1
            raise Overflow()
        return super().complete(system, messages, registry)


@pytest.mark.parametrize("compact", [False, True])
def test_context_overflow_compacts_once_and_retries(tmp_path, compact):
    ws = _ws(tmp_path)
    replies = [bash(f"echo {i}") for i in range(6)] + [bash(EDIT), submit()]
    client = OverflowsWhenLong(replies, limit=11)
    ex, _ = _exec(ws, client, compact=compact, gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.success and run.stop_reason == "submitted"
    assert client.overflows >= 1 and run.forced_compactions >= 1 and client.summaries >= 1
    assert run.error_kind is None and "context_overflow" in run.model_errors
    assert run.compaction["forced"] >= 1


def test_context_overflow_with_a_failing_summary_uses_the_fallback(tmp_path):
    ws = _ws(tmp_path)
    replies = [bash(f"echo {i}") for i in range(6)] + [bash(EDIT), submit()]
    client = OverflowsWhenLong(replies, limit=11, summary_fails=True)
    run = _exec(ws, client, gate=lambda f, s: {"green": True})[0].run()
    assert run.success and run.compaction["fallbacks"] >= 1


def test_context_overflow_that_compaction_cannot_fix_stops_without_looping(tmp_path):
    ws = _ws(tmp_path)
    client = OverflowsWhenLong([bash(f"echo {i}") for i in range(6)], limit=0)
    path = tmp_path / "ckpt.jsonl"
    run = _exec(ws, client, checkpoint=LoopCheckpoint(path, "k"))[0].run()
    assert run.stop_reason == "context_overflow" and run.error_kind == "context_overflow"
    assert client.overflows == 1 and run.steps == 1  # nothing to compact: stop at once
    assert LoopCheckpoint(path, "k").load() is None


def test_context_overflow_after_a_forced_compaction_escalates(tmp_path):
    ws = _ws(tmp_path)
    strong = Strong([bash(EDIT), submit()])

    class AlwaysOverflowAfter(OverflowsWhenLong):
        """Once it has overflowed, every loop request overflows (summaries work)."""

        def complete(self, system, messages, registry):
            if self.overflows and not (len(messages) == 1 and "<transcript>"
                                       in str(messages[0].get("content"))):
                self.overflows += 1
                raise Overflow()
            return super().complete(system, messages, registry)

    cheap = AlwaysOverflowAfter([bash(f"echo {i}") for i in range(8)], limit=9)
    run = _exec(ws, cheap, gate=lambda f, s: {"green": True}, escalate=Ladder(strong))[0].run()
    assert run.success and run.rung == 1
    assert run.escalations[0]["reason"].startswith("context overflow")
    assert run.forced_compactions == 1  # one per overflow streak, then the next rung


# ── resume safety ────────────────────────────────────────────────────────────


def test_resume_names_files_changed_after_the_last_saved_step(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    first = DiesAt([bash("echo one"), bash(EDIT)], die_at=3)
    with pytest.raises(Killed):
        _exec(ws, first, checkpoint=LoopCheckpoint(path, "k1"))[0].run()
    saved = LoopCheckpoint(path, "k1").load()
    assert "workspace" in saved[1]
    # the kill landed mid-command: a write happened after the last save
    import time
    time.sleep(0.01)
    (ws / "half_written.py").write_text("x = (\n", encoding="utf-8")
    second = Scripted([submit()])
    run = _exec(ws, second, checkpoint=LoopCheckpoint(path, "k1"),
                gate=lambda f, s: {"green": True})[0].run()
    assert run.resumed and run.success
    assert run.resume_changed == ["half_written.py"]
    sent = json.dumps(second.seen[0][-1])
    assert "[harness note]" in sent and "half_written.py" in sent and "resumed" in sent
    assert second.seen[0][:-1] == saved[0][:-1]  # only the last turn got the note
    assert run.component()["resume_changed"] == ["half_written.py"]
    assert set(run.files_changed) == {"calc.py", "half_written.py"}


def test_resume_keeps_escalations(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    cheap = Scripted([bash("echo a"), bash("echo b")])
    strong = DiesAt([bash("echo strong")], die_at=2)
    strong.model = "claude-sonnet-5-5"
    with pytest.raises(Killed):
        _exec(ws, cheap, checkpoint=LoopCheckpoint(path, "k"), escalate=Ladder(strong),
              rung_steps_=2)[0].run()
    run = _exec(ws, Scripted([]), checkpoint=LoopCheckpoint(path, "k"),
                escalate=Ladder(Strong([submit()])), rung_steps_=2,
                gate=lambda f, s: {"green": True})[0].run()
    assert run.resumed and len(run.escalations) == 1
    assert run.escalations[0]["to"] == "claude-sonnet-5-5"


def test_resume_keeps_the_compaction_count(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_CONTEXT_TOKENS", "1000")
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    class DiesWhenDone(Summarising):
        """Answers summaries; killed on the first loop call past its replies."""

        def complete(self, system, messages, registry):
            summary = len(messages) == 1 and "<transcript>" in str(messages[0].get("content"))
            if not summary and not self.replies:
                raise Killed()
            return super().complete(system, messages, registry)

    client = DiesWhenDone([bash(f"echo {i}", tin=100 * (i + 1)) for i in range(10)])
    with pytest.raises(Killed):
        _exec(ws, client, compact=True, checkpoint=LoopCheckpoint(path, "k"))[0].run()
    saved = LoopCheckpoint(path, "k").load()[1]
    assert saved["compaction_summaries"] >= 1
    ex, _ = _exec(ws, Summarising([submit()]), compact=True,
                  checkpoint=LoopCheckpoint(path, "k"), gate=lambda f, s: {"green": True})
    run = ex.run()
    assert run.resumed and run.compaction["summaries"] == saved["compaction_summaries"]


def test_checkpoint_failures_reach_telemetry(tmp_path):
    class Broken:
        def load(self):
            return None

        def save(self, messages, counters):
            return False

        def finish(self, reason):
            pass

    ws = _ws(tmp_path)
    run = _exec(ws, Scripted([bash("echo 1"), bash("echo 2"), submit()]), checkpoint=Broken(),
                gate=lambda f, s: {"green": True})[0].run()
    assert run.checkpoint_failures == 2 and run.component()["checkpoint_failures"] == 2
    plain = _exec(ws, Scripted([submit()]), gate=lambda f, s: {"green": True})[0].run()
    assert "checkpoint_failures" not in plain.component()


# ── hand-over redaction ──────────────────────────────────────────────────────


def test_handover_note_is_redacted(tmp_path, monkeypatch):
    secret = "s3cr3t-value-0123456789abcdef"
    key = "sk-ant-api03-" + "Q7wErTy9UiOp2AsDfGh5JkL3ZxCvBn8M"
    monkeypatch.setenv("MY_SERVICE_TOKEN", secret)
    ws = _ws(tmp_path)
    verdicts = iter([{"green": False, "retry": False, "feedback": f"FAILED: auth {key}"},
                     {"green": True}])
    cheap = Scripted([bash(f"curl -H 'x: {secret}' localhost"), submit("done?")])
    strong = Strong([submit()])
    run = _exec(ws, cheap, gate=lambda f, s: next(verdicts), escalate=Ladder(strong))[0].run()
    assert run.success
    first = strong.seen[0][0]["content"]
    assert secret not in first and key not in first
    assert "[REDACTED:MY_SERVICE_TOKEN]" in first and "FAILED: auth [REDACTED" in first


# ── stuck precision inside the loop ──────────────────────────────────────────


def test_grep_without_a_match_is_not_a_failure_in_the_loop(tmp_path):
    ws = _ws(tmp_path)
    from scaffold.agent.loop_stuck import StuckDetector

    greps = [bash(f"grep -rn needle{i} .") for i in range(6)]
    ex, _ = _exec(ws, Scripted(greps + [bash(EDIT), submit()]),
                  gate=lambda f, s: {"green": True},
                  stuck=StuckDetector("log", window=4, fail_threshold=4))
    run = ex.run()
    assert run.success and run.stuck["neutral_results"] == 6
    assert run.stuck["triggers"]["failure_rate"] == 0


def test_auto_continue_note_reaches_a_resumed_conversation_once(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    with pytest.raises(Killed):
        _exec(ws, DiesAt([bash("echo one")], die_at=2),
              checkpoint=LoopCheckpoint(path, "k"))[0].run()
    (tmp_path / "ckpt.jsonl.note").write_text("NOTE: the last attempt hit the time limit.")
    client = Scripted([submit()])
    run = _exec(ws, client, checkpoint=LoopCheckpoint(path, "k"),
                gate=lambda f, s: {"green": True})[0].run()
    assert run.resumed and run.success
    text = json.dumps(client.seen[0])
    assert text.count("hit the time limit") == 1


def test_auto_continue_note_ignored_on_a_fresh_start(tmp_path):
    ws = _ws(tmp_path)
    path = tmp_path / "ckpt.jsonl"
    (tmp_path / "ckpt.jsonl.note").write_text("NOTE: should not be added twice")
    client = Scripted([submit()])
    run = _exec(ws, client, checkpoint=LoopCheckpoint(path, "k"),
                gate=lambda f, s: {"green": True})[0].run()
    assert not run.resumed and "should not be added" not in json.dumps(client.seen[0])
