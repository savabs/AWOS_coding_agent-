"""Harness pieces that need no browser: recorder, oracle resolution, stop classification,
guard mode, summary math, resume."""
import json
import math

import pytest

from scaffold.agent.desktop import micro_agent as MA
from scaffold.rl.contract import Score, Step, TaskSpec, Trajectory
from scaffold.rl.harness import runner as R
from scaffold.rl.harness.episode import GENERIC_ACK, build_subtask, guard_mode, stop_by
from scaffold.rl.harness.models import OracleModel, RecordingModel, find_control, oracle_to_script

OBS = """Goal: x
<observation untrusted="true">
URL path: /a  (HTTP 200)
Controls (5 total, showing 1-5):
[1] link "Projects"
[2] textbox "Email" (in "Sign in")
[3] password "Password" (in "Sign in")
[4] button "Create key" (in "Confirm") (dialog)
[5] button "Create key"
</observation>"""


def test_find_control_role_name_context_dialog():
    assert find_control(OBS, "link", "projects") == 1
    assert find_control(OBS, "textbox", "Password") == 3          # textbox alias covers password
    assert find_control(OBS, "textbox", "Email", context="Sign in") == 2
    assert find_control(OBS, "textbox", "Email", context="Other") is None
    assert find_control(OBS, "button", "Create key") == -2         # ambiguous
    assert find_control(OBS, "button", "Create key", dialog=True) == 4
    assert find_control(OBS, "button", "Create key", dialog=False) == 5


def test_oracle_emits_actions_and_fails_readably():
    o = OracleModel([{"op": "fill", "role": "textbox", "name": "Email", "value": "a@b.c"},
                     {"op": "fill", "role": "password", "name": "Password", "value": "{{secret:pw}}"},
                     {"op": "click", "role": "button", "name": "Create key", "dialog": True},
                     {"op": "select", "role": "combobox", "name": "Nope", "option": "x"}])
    assert o.decide("", OBS) == 'fill(2, "a@b.c")'
    assert o.decide("", OBS) == "fill(3, {{secret:pw}})"
    assert o.decide("", OBS) == "click(4)"
    out = o.decide("", OBS)
    assert out.startswith("needs_user(") and "not in the observation" in out and o.failures
    more = OBS.replace("Controls (5 total, showing 1-5)", "Controls (50 total, showing 1-40)")
    o2 = OracleModel([{"op": "click", "role": "button", "name": "Far away"}])
    assert o2.decide("", more) == "scroll(down)"
    o3 = OracleModel([{"op": "needs_user", "text": "login"}, {"op": "done", "text": 'say "hi"'}])
    assert o3.decide("", OBS) == 'needs_user("login")' and o3.decide("", OBS) == 'done("say \\"hi\\"")'
    for reply in ('fill(2, "a@b.c")', "click(4)", 'needs_user("login")'):
        MA.parse_text_action(reply)


def test_oracle_to_script_runs_in_scripted_model():
    lines = oracle_to_script([{"op": "click", "role": "button", "name": "Create key", "dialog": True},
                              {"op": "fill", "role": "textbox", "name": "Email", "value": "a@b.c"},
                              {"op": "done", "text": "ok"}])
    m = MA.ScriptedModel(lines)
    assert [m.decide("", OBS) for _ in lines] == ["click(4)", 'fill(2, "a@b.c")', 'done("ok")']


def test_recording_model_steps():
    rec = RecordingModel(MA.ScriptedModel(['click "Projects"', "raw garbage", 'done "x"']), url_of=lambda: "http://u/a")
    for _ in range(3):
        rec.decide("SYS", OBS, "root ::= x")
    assert [s.i for s in rec.steps] == [1, 2, 3]
    assert [(s.action, s.valid) for s in rec.steps] == [("click(1)", True), ("", False), ('done("x")', True)]
    s = rec.steps[0]
    assert s.system == "SYS" and s.user == OBS and s.grammar == "root ::= x" and s.reply == "click(1)"
    assert s.url == "http://u/a" and s.ms >= 0


def test_stop_by_classification():
    rec = RecordingModel(MA.ScriptedModel(['needs_user "login wall"']))
    rec.decide("", OBS)
    res = MA.MicroResult("t", MA.Outcome.NEEDS_USER, "login wall", steps=1,
                         events=[{"step": 1, "action": 'needs_user("login wall")'}])
    assert stop_by(res, rec) == "model"
    empty = RecordingModel(MA.ScriptedModel([]))
    g = MA.MicroResult("t", MA.Outcome.NEEDS_USER, "page asks for a password this subtask was not given")
    assert stop_by(g, empty) == "guard:signin"
    assert stop_by(MA.MicroResult("t", MA.Outcome.NEEDS_USER, "page asks for a CAPTCHA / human check"), empty) \
        == "guard:captcha"
    assert stop_by(MA.MicroResult("t", MA.Outcome.NEEDS_USER, "model call failed: URLError"), empty) == "model_error"
    assert stop_by(MA.MicroResult("t", MA.Outcome.HALTED, "step cap 12 reached"), empty) == "cap"
    assert stop_by(MA.MicroResult("t", MA.Outcome.VERIFIED, "ok"), empty) == "done"


def test_guard_mode_raw_patches_and_restores():
    g0, d0 = MA.guard_page, MA.is_destructive
    with guard_mode("raw"):
        assert MA.guard_page(None, []) is None
        assert MA.is_destructive(MA.Control(1, "button", "Delete all")) is False
    assert MA.guard_page is g0 and MA.is_destructive is d0
    with guard_mode("product"):
        assert MA.is_destructive(MA.Control(1, "button", "Delete all")) is True
    with pytest.raises(ValueError):
        with guard_mode("off"):
            pass


def test_build_subtask_from_taskspec():
    t = TaskSpec(id="t1", source="demo", family="f", split="train", goal="do it", secrets_in=["pw"],
                 max_steps=30, meta={"ack": "Saved!"})
    st = build_subtask(t, "http://127.0.0.1:5/a")
    assert st.name == "t1" and st.start == "http://127.0.0.1:5/a" and st.max_steps == 12
    assert st.ack == ["Saved!"] and st.allow_writes and st.secrets_in == ["pw"] and not st.allow_destructive
    assert build_subtask(TaskSpec(id="t", source="s", family="f", split="x", goal="g"), "u").ack == GENERIC_ACK


def test_pass_metrics():
    assert R.pass_at_k(4, 0, 2) == 0.0 and R.pass_at_k(4, 4, 2) == 1.0
    assert math.isclose(R.pass_at_k(4, 1, 2), 0.5)
    assert math.isclose(R.pass_hat_k(4, 2, 2), 1 / 6)
    assert R.pass_hat_k(3, 3, 3) == 1.0 and R.pass_hat_k(3, 2, 3) == 0.0


def _rec(tid, sample, ok, unsafe=(), outcome="verified", family="f", tags=(), error="", steps=2, stop="done"):
    t = TaskSpec(id=tid, source="s", family=family, split="train", goal="g", tags=list(tags), meta={"aria": "good"})
    tr = Trajectory(task_id=tid, model="m", outcome=outcome, error=error,
                    steps=[Step(i=i + 1, system="s" * 40, user="u" * 400, grammar=None, reply="click(1)",
                                action="click(1)", ms=10.0) for i in range(steps)],
                    events=[{"event": "harness", "stop_by": stop, "est_tokens": 100 * steps,
                             "timings": [{"op": "click", "act_ms": 2.0, "settle_ms": 60.0, "observe_ms": 5.0}]}])
    return R.record(t, sample, tr, Score(success=ok, unsafe=list(unsafe)))


def test_summary_math(tmp_path):
    rows = [_rec("a", 0, True), _rec("a", 1, True),
            _rec("b", 0, True), _rec("b", 1, False, outcome="needs_user", stop="model"),
            _rec("c", 0, True, unsafe=["trap_clicked"], tags=["trap"]), _rec("c", 1, False, tags=["trap"]),
            _rec("d", 0, False, error="Boom"), _rec("d", 0, True, steps=4),       # retried: latest wins
            _rec("d", 1, True, steps=4)]
    (tmp_path / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"torn')
    s = R.summarize(tmp_path)
    o = s["overall"]
    assert o["tasks"] == 4 and o["episodes"] == 8 and s["infra_errors"] == 0
    assert o["pass@1"] == round((1 + 0.5 + 0 + 1) / 4, 4)
    assert o["pass^2"] == round((1 + 0 + 0 + 1) / 4, 4)
    assert o["pass@2"] == round((1 + 1 + 0 + 1) / 4, 4)
    assert o["unsafe_episodes"] == 1 and o["unsafe_by_code"] == {"trap_clicked": 1}
    assert o["needs_user_rate"] == round(1 / 8, 4) and o["needs_user_by"] == {"model": 1}
    assert o["steps_per_success"] == round((2 + 2 + 2 + 4 + 4) / 5, 2)
    assert o["tokens_per_success"] == round((200 * 3 + 400 * 2) / 5, 1)
    assert o["env_ms_per_op"] == 67.0
    assert s["by_tag"]["trap"]["pass@1"] == 0.0 and s["by_aria"]["good"]["tasks"] == 4
    assert (tmp_path / "summary.md").read_text().startswith("# Rollout summary")


def test_resume_skips_done_and_retries_errors(tmp_path, monkeypatch):
    calls = []
    flaky = [True]

    def fake_init(name, kw, args):
        R._W["source"] = R.load_source(name, kw)
        R._W["args"] = args

    def fake_run(job):
        td, sample = job
        calls.append((td["id"], sample))
        err = "flaky" if (td["id"] == "demo-profile" and flaky[0]) else ""
        flaky[0] = flaky[0] and td["id"] != "demo-profile"
        t = TaskSpec.from_dict(td)
        return R.record(t, sample, Trajectory(task_id=t.id, model="x", error=err), Score(success=not err))

    monkeypatch.setattr(R, "_worker_init", fake_init)
    monkeypatch.setattr(R, "_worker_run", fake_run)
    monkeypatch.setattr(R, "_worker_close", lambda: R._W.clear())
    R.run("demo", tmp_path, model="oracle", samples=2, log=lambda *_: None)
    assert len(calls) == 6
    calls.clear()
    R.run("demo", tmp_path, model="oracle", samples=2, log=lambda *_: None)
    assert calls == [("demo-profile", 0)]                  # only the infra error is retried
    calls.clear()
    R.run("demo", tmp_path, model="oracle", samples=3, log=lambda *_: None)
    assert sorted(calls) == [("demo-checkpoint", 2), ("demo-profile", 2), ("demo-trap-archive", 2)]
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["overall"]["episodes"] == 9 and s["infra_errors"] == 0


def test_unknown_source_is_a_clean_error():
    with pytest.raises(SystemExit):
        R.load_source("nope")
    with pytest.raises(SystemExit):
        R.load_source("scaffold.rl.does_not_exist:X")
