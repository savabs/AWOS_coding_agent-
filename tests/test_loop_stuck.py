"""
The loop's behavioural stuck detector (scaffold/agent/loop_stuck.py): each
trigger, the off/log/nudge modes, the cooldown, the escalation flag, and that
odd inputs never raise. Pure: no model, no sandbox, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent import loop_stuck as ls  # noqa: E402
from scaffold.agent.loop_executor import render_observation  # noqa: E402
from scaffold.agent.tools.base import ToolResult  # noqa: E402


def res(code: int = 0, output: str = "", timed_out: bool = False) -> ToolResult:
    """A ToolResult shaped like loop_executor.BashTool's."""
    text = render_observation(code, output, timed_out, 120)
    ok = code == 0 and not timed_out
    return ToolResult(success=ok, text=text, error="" if ok else text,
                      data={"exit_code": code, "timed_out": timed_out})


def ok(output: str = "fine") -> ToolResult:
    return res(0, output)


def bad(output: str = "Error: boom") -> ToolResult:
    return res(1, output)


FORBIDDEN = ("stop", "give up", "submit", "quit", "COMPLETE_TASK")


# ── env ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,want", [("", "log"), ("off", "off"), ("LOG", "log"),
                                      (" nudge ", "nudge"), ("bogus", "log"), ("1", "log")])
def test_stuck_mode_env(monkeypatch, raw, want):
    monkeypatch.setenv("AWOS_LOOP_STUCK", raw)
    assert ls.stuck_mode() == want


def test_stuck_mode_unset_defaults_to_log(monkeypatch):
    monkeypatch.delenv("AWOS_LOOP_STUCK", raising=False)
    assert ls.stuck_mode() == "log"
    assert ls.StuckDetector().mode == "log"


def test_explicit_mode_beats_env_and_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_STUCK", "off")
    assert ls.StuckDetector("nudge").mode == "nudge"
    assert ls.StuckDetector("wat").mode == "off"  # invalid explicit -> env
    monkeypatch.delenv("AWOS_LOOP_STUCK")
    assert ls.StuckDetector("wat").mode == "log"


# ── repeat_command ───────────────────────────────────────────────────────────


def test_repeat_command_consecutive_fires_at_threshold():
    d = ls.StuckDetector("nudge")
    assert d.observe(1, ["pytest  -x"], [bad("E1")]) is None
    assert d.observe(2, ["pytest -x "], [bad("E2 other")]) is None
    text = d.observe(3, ["pytest -x"], [bad("E3 different")])
    assert text and "`pytest -x`" in text and "3 times" in text
    assert d.summary()["triggers"]["repeat_command"] == 1
    assert d.summary()["first_fire_step"]["repeat_command"] == 3


def test_repeat_command_within_one_step():
    d = ls.StuckDetector("nudge")
    text = d.observe(1, ["ls", "ls", "ls"], [ok("a"), ok("b"), ok("c")])
    assert text and "`ls`" in text


def test_repeat_command_same_output_alternating():
    d = ls.StuckDetector("log")
    d.observe(1, ["cat a.py"], [ok("X")])
    d.observe(2, ["cat b.py"], [ok("Y")])
    d.observe(3, ["cat a.py"], [ok("X")])
    d.observe(4, ["cat b.py"], [ok("Y")])
    assert d.summary()["triggers"]["repeat_command"] == 0
    d.observe(5, ["cat a.py"], [ok("X")])
    assert d.summary()["triggers"]["repeat_command"] == 1


def test_repeat_same_command_different_output_not_alternating_does_not_fire():
    d = ls.StuckDetector("log")
    for i in range(3):
        d.observe(2 * i, ["python t.py"], [ok(f"out {'abc'[i]}")])
        d.observe(2 * i + 1, [f"sed -i 's/{i}/x/' f.py"], [ok("")])
    assert d.summary()["triggers"]["repeat_command"] == 0


def test_output_signature_ignores_digits():
    a = ok("ran 3 tests in 0.12s at 0x7ffe12")
    b = ok("ran 3 tests in 0.98s at 0x1234ab")
    assert ls.output_signature(a) == ls.output_signature(b)
    assert ls.output_signature(a) != ls.output_signature(ok("something else"))


# ── failure_rate ─────────────────────────────────────────────────────────────


def test_failure_rate_needs_full_window():
    d = ls.StuckDetector("log", window=20, fail_threshold=15)
    for i in range(15):  # distinct commands and errors: only failure_rate can fire
        d.observe(i, [f"cmd{i}"], [bad(f"err {chr(97 + i)}")])
    assert d.summary()["triggers"]["failure_rate"] == 0
    for i in range(15, 20):
        d.observe(i, [f"cmd{i}"], [ok(chr(97 + i))])
    assert d.summary()["triggers"]["failure_rate"] == 1  # 15/20 once full
    s = d.summary()
    assert s["triggers"]["repeat_command"] == 0 and s["triggers"]["failure_signature"] == 0


def test_failure_rate_below_threshold_no_fire():
    d = ls.StuckDetector("log", window=20, fail_threshold=15)
    for i in range(40):
        r = bad(f"e{chr(97 + i % 26)}{i}x") if i % 2 else ok(str(i))
        d.observe(i, [f"c{i}"], [r])
    assert d.summary()["triggers"]["failure_rate"] == 0


def test_failure_rate_nudge_text():
    d = ls.StuckDetector("nudge", window=4, fail_threshold=3)
    texts = [d.observe(i, [f"c{i}"], [bad(f"err {'abcd'[i]}")]) for i in range(4)]
    assert texts[:3] == [None, None, None]
    assert "4 of your last 4" in texts[3]


# ── failure_signature ────────────────────────────────────────────────────────


def test_failure_signature_consecutive():
    d = ls.StuckDetector("nudge")
    tb = "Traceback...\n  File x.py, line {n}\nValueError: bad value {n}\n"
    assert d.observe(1, ["python a.py"], [bad(tb.format(n=1))]) is None
    assert d.observe(2, ["python b.py"], [bad(tb.format(n=22))]) is None
    text = d.observe(3, ["python c.py"], [bad(tb.format(n=333))])
    assert "ValueError: bad value #" in text
    assert d.summary()["triggers"] == {"repeat_command": 0, "failure_rate": 0,
                                       "failure_signature": 1, "no_progress": 0}


def test_failure_signature_reset_by_success_or_new_error():
    d = ls.StuckDetector("log")
    d.observe(1, ["a"], [bad("E: x")])
    d.observe(2, ["b"], [bad("E: x")])
    d.observe(3, ["c"], [ok()])
    d.observe(4, ["d"], [bad("E: x")])
    d.observe(5, ["e"], [bad("E: y")])
    d.observe(6, ["f"], [bad("E: x")])
    assert d.summary()["triggers"]["failure_signature"] == 0


def test_failure_signature_timeouts_and_empty_output():
    assert ls.failure_signature(res(124, "", timed_out=True)) == "<timed out>"
    assert ls.failure_signature(res(2, "")) == "<exit 2>"
    assert ls.failure_signature(res(1, "real error\n-----\n")) == "real error"


# ── no_progress ──────────────────────────────────────────────────────────────


def test_no_progress_fires_after_k_unchanged_steps():
    d = ls.StuckDetector("nudge", no_progress_steps=3)
    out = [d.observe(i, [f"c{i}"], [ok(chr(97 + i))], progress_key="h1") for i in range(4)]
    assert out[:3] == [None, None, None]
    assert "has not changed in the last 3 steps" in out[3]


def test_no_progress_reset_on_change_and_none_is_ignored():
    d = ls.StuckDetector("log", no_progress_steps=3)
    keys = ["a", "a", None, "a", "b", "b", None, "b", "c"]
    for i, k in enumerate(keys):
        d.observe(i, [f"c{i}"], [ok(chr(97 + i))], progress_key=k)
    assert d.summary()["triggers"]["no_progress"] == 0


def test_no_progress_never_without_keys():
    d = ls.StuckDetector("log", no_progress_steps=2)
    for i in range(30):
        d.observe(i, [f"c{i}"], [ok(chr(65 + i))])
    assert d.summary()["triggers"]["no_progress"] == 0


def test_no_progress_unhashable_key():
    d = ls.StuckDetector("log", no_progress_steps=2)
    for i in range(3):
        d.observe(i, [f"c{i}"], [ok(chr(65 + i))], progress_key={"files": ["a.py"]})
    assert d.summary()["triggers"]["no_progress"] == 1


# ── modes ────────────────────────────────────────────────────────────────────


def _spin(d, steps, start=0):
    return [d.observe(start + i, ["make"], [bad("make: *** [all] Error 2")])
            for i in range(steps)]


def test_off_mode_tracks_nothing():
    d = ls.StuckDetector("off")
    assert _spin(d, 30) == [None] * 30
    s = d.summary()
    assert s["mode"] == "off" and not any(s["triggers"].values())
    assert s["nudges"] == 0 and s["escalate_recommended"] is False


def test_log_mode_counts_but_never_returns_text():
    d = ls.StuckDetector("log")
    assert _spin(d, 30) == [None] * 30
    s = d.summary()
    assert s["triggers"]["repeat_command"] == 1
    assert s["triggers"]["failure_signature"] == 1
    assert s["triggers"]["failure_rate"] == 1
    assert s["nudges"] == 0 and s["would_nudge"] >= 3
    assert s["escalate_recommended"] is True  # log mode still counts escalation


def test_nudge_mode_nudges_once_per_activation_and_combines():
    d = ls.StuckDetector("nudge", escalate_after=100)
    out = _spin(d, 10)
    fired = [t for t in out if t]
    assert len(fired) == 1  # step 3: repeat_command + failure_signature together
    assert out[2] is fired[0]
    assert "make" in fired[0] and "same error" in fired[0]
    assert d.summary()["nudges"] == 2


def test_nudge_texts_never_say_stop_or_submit():
    for name in ls.TRIGGERS:
        text = ls.nudge_text(name, {"command": "x", "count": 3, "failed": 15, "window": 20,
                                    "signature": "E", "steps": 12}).lower()
        assert text and "different" in text
        for word in FORBIDDEN:
            assert word.lower() not in text, (name, word)
    assert ls.nudge_text("unknown", {})


# ── cooldown ─────────────────────────────────────────────────────────────────


def test_cooldown_suppresses_quick_refire_then_nudges_if_still_active():
    # distinct outputs, so only the consecutive-command rule is in play
    d2 = ls.StuckDetector("nudge", window=10, escalate_after=100)  # cooldown 5
    assert d2.cooldown == 5
    outs = []
    seq = ["ls", "ls", "ls", "pwd", "ls", "ls", "ls", "ls", "ls"]
    for i, c in enumerate(seq, 1):
        outs.append(d2.observe(i, [c], [ok(f"o{chr(96 + i)}")]))
    # tick 3 nudge; tick 4 deactivates; tick 7 refires (fires=2) but within cooldown (7-3<5);
    # tick 8 cooldown elapsed and still active -> nudge
    assert outs[2] is not None
    assert outs[6] is None
    assert outs[7] is not None
    assert outs[8] is None
    assert d2.summary()["triggers"]["repeat_command"] == 2
    assert d2.summary()["nudges"] == 2


def test_refire_after_cooldown_nudges_immediately():
    d = ls.StuckDetector("nudge", window=4, fail_threshold=4, escalate_after=100)  # cooldown 2
    outs = []
    seq = ["a", "a", "a", "b", "c", "d", "a", "a", "a"]
    for i, c in enumerate(seq, 1):
        outs.append(d.observe(i, [c], [ok(f"o{chr(96 + i)}")]))
    assert outs[2] and outs[8]
    assert sum(1 for o in outs if o) == 2


# ── escalation ───────────────────────────────────────────────────────────────


def test_escalation_after_persisting_past_nudge():
    d = ls.StuckDetector("nudge", escalate_after=4, no_progress_steps=2)
    flags = []
    for i in range(1, 9):
        d.observe(i, [f"c{i}"], [ok(chr(96 + i))], progress_key="same")
        flags.append(d.escalate_recommended)
    # no_progress active from tick 3 (nudged), escalates at tick 7
    assert flags == [False] * 6 + [True, True]
    s = d.summary()
    assert s["escalate_step"] == 7 and s["escalate_trigger"] == "no_progress"


def test_no_escalation_when_trigger_clears():
    d = ls.StuckDetector("nudge", escalate_after=3, no_progress_steps=2)
    keys = ["k", "k", "k", "k", "new", "new", "new", "new2"]
    for i, k in enumerate(keys, 1):
        d.observe(i, [f"c{i}"], [ok(chr(96 + i))], progress_key=k)
    assert d.escalate_recommended is False


def test_escalation_is_sticky_and_run_continues():
    d = ls.StuckDetector("nudge")
    _spin(d, 10)
    assert d.escalate_recommended is True
    # recovery does not clear it, and observe keeps working
    for i in range(10, 15):
        assert d.observe(i, [f"fix{i}"], [ok(chr(80 + i))]) is None
    assert d.escalate_recommended is True
    assert d.summary()["steps_observed"] == 15


# ── defensive ────────────────────────────────────────────────────────────────


class Explosive:
    @property
    def success(self):
        raise RuntimeError("boom")


@pytest.mark.parametrize("commands,results", [
    (None, None),
    ([], []),
    ("ls", None),
    (["ls", "pwd"], [ok()]),          # fewer results than commands
    ([], [bad(), bad()]),             # results with no commands
    ([None, 3, b"x"], [None, "str", 42]),
    (["ls"], [SimpleNamespace()]),    # no attributes at all
    (["ls"], [SimpleNamespace(success=None, data={"exit_code": 2})]),
    (["ls"], [SimpleNamespace(success="yes", text=None, data="nope")]),
    (["ls"], [SimpleNamespace(data={"timed_out": True})]),
    (["ls"], [Explosive()]),
    (12, 34),
])
def test_weird_inputs_never_raise(commands, results):
    d = ls.StuckDetector("nudge")
    for step in range(5):
        d.observe(step, commands, results, progress_key=object())
    s = d.summary()
    assert set(s) >= {"mode", "triggers", "nudges", "escalate_recommended", "first_fire_step"}


def test_internal_error_swallowed_and_counted(monkeypatch):
    d = ls.StuckDetector("nudge")
    monkeypatch.setattr(d, "_observe", lambda *a, **k: 1 / 0)
    assert d.observe(1, ["ls"], [ok()]) is None
    assert d.summary()["errors"] == 1


def test_result_failed_fallbacks():
    assert ls.result_failed(SimpleNamespace(success=True)) is False
    assert ls.result_failed(SimpleNamespace(data={"exit_code": 0})) is False
    assert ls.result_failed(SimpleNamespace(data={"exit_code": 3})) is True
    assert ls.result_failed(SimpleNamespace(data={"timed_out": True})) is True
    assert ls.result_failed(SimpleNamespace()) is None
    assert ls.result_failed(None) is None
    assert ls.result_failed(ToolResult.fail("Not run")) is True


def test_bad_constructor_args_are_clamped():
    d = ls.StuckDetector("log", window=0, fail_threshold=99, repeat_threshold=0,
                         no_progress_steps="x", escalate_after=-5)
    assert d.window == 1 and d.fail_threshold == 1 and d.repeat_threshold == 2
    assert d.no_progress_steps == 12 and d.escalate_after == 1
    d.observe(1, ["a"], [bad()])
    assert d.summary()["triggers"]["failure_rate"] == 1


def test_summary_shape():
    d = ls.StuckDetector("log")
    s = d.summary()
    assert s["mode"] == "log"
    assert s["triggers"] == {name: 0 for name in ls.TRIGGERS}
    assert s["nudges"] == 0 and s["escalate_recommended"] is False
    assert s["first_fire_step"] == {}
