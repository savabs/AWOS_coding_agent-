"""Emergency stop (stop.py) and takeover semantics (ui_session.TurnLease):
latched, hardware-only hotkey, sentinel, signal; yielded state + quiet period."""
import json
import os
import signal

import pytest

from scaffold.agent.desktop.app_policy import AppPolicy
from scaffold.agent.desktop.enforce import Enforcer, Mode
from scaffold.agent.desktop.fake_backend import FakeBackend
from scaffold.agent.desktop.policy import ActionClass as C, AuditLog, Grant, SinkPolicy
from scaffold.agent.desktop.stop import StopSwitch, owner_reset
from scaffold.agent.desktop.ui_session import DesktopSession, Outcome, TurnLease


def node(role, title="", frame=(0, 0, 10, 10), children=(), **kw):
    return {"role": role, "title": title,
            "frame": {"x": frame[0], "y": frame[1], "w": frame[2], "h": frame[3]},
            "children": list(children), **kw}


def mail(draft="hi"):
    return node("AXApplication", "Mail", (0, 0, 800, 600), [node("AXWindow", "Compose", (0, 0, 800, 600), [
        node("AXButton", "Bold", (10, 10, 40, 20)),
        node("AXTextArea", "", (10, 50, 400, 200), value=draft, focused=True),
        node("AXButton", "Send", (420, 260, 60, 24))])])


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def mk(tmp_path, approver=None, quiet=0.0, clock=None, takeover_hook=True, **kw):
    audit = AuditLog(tmp_path / "audit.jsonl")
    (tmp_path / "sb").mkdir(exist_ok=True)
    enf = Enforcer(SinkPolicy(tmp_path / "sb", [Grant(C.UI_ACTION, "app:com.apple.mail/*")]),
                   Mode.ENFORCE, approver, audit)
    be = FakeBackend(mail(), takeover_hook=takeover_hook)
    stop = StopSwitch(tmp_path / "cu_stop", audit)
    s = DesktopSession(be, enf, send_approver=lambda p: True, quiet_period=quiet,
                       clock=clock or Clock(), stop=stop,
                       app_policy=kw.pop("app_policy", AppPolicy()), **kw)
    return s, be, stop, audit


def sid(s, text):
    return next(e.sid for e in s.latest.elements if e.text == text)


def stop_records(audit):
    return [r for r in audit.read() if r.get("kind") == "cu_stop"]


# -- the switch -------------------------------------------------------------------

def test_sentinel_file_trips_and_latches(tmp_path):
    s, be, stop, audit = mk(tmp_path)
    s.observe()
    (tmp_path / "cu_stop").write_text("")
    r = s.act("click", sid(s, "Bold"))
    assert r.outcome is Outcome.STOPPED and be.ops == []
    (tmp_path / "cu_stop").unlink()                 # removing the file does not un-stop
    assert stop.tripped
    assert s.observe().kind == "stopped"
    assert s.act("click", "e1").outcome is Outcome.STOPPED
    assert s.state == "stopped"
    assert any(r["source"] == "sentinel" for r in stop_records(audit))


def test_sentinel_default_path_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_CU_STOP_FILE", str(tmp_path / "custom_stop"))
    sw = StopSwitch()
    assert sw.sentinel == tmp_path / "custom_stop" and not sw.tripped
    (tmp_path / "custom_stop").write_text("")
    assert sw.tripped


def test_hotkey_rejects_synthetic_events(tmp_path):
    s, be, stop, audit = mk(tmp_path)
    assert be.hotkey_cb is not None                 # the session registered the hook
    assert be.press_stop_hotkey(synthetic=True) is False
    assert not stop.tripped
    for ev in ({"source": "hid", "synthetic": True, "pid": 0},
               {"source": "hid", "synthetic": False, "pid": 4242},     # posted by a process
               {"source": "synthetic", "synthetic": False, "pid": 0},
               {"source": "hid", "pid": 0}):                            # flag missing
        assert stop.on_hotkey(ev) is False
    assert not stop.tripped and stop.rejected_hotkeys == 5
    assert be.press_stop_hotkey() is True and stop.tripped
    assert any(r.get("source") == "hotkey" and r.get("reason") for r in stop_records(audit))


def test_signal_trips(tmp_path):
    sw = StopSwitch(tmp_path / "x")
    prev = sw.install_signal(signal.SIGUSR1)
    try:
        os.kill(os.getpid(), signal.SIGUSR1)
        assert sw.tripped and "signal" in sw.reason
    finally:
        signal.signal(signal.SIGUSR1, prev)


def test_stop_kills_lease_and_rejects_open_sends(tmp_path):
    s, be, stop, audit = mk(tmp_path)
    s.observe()
    p1 = s.prepare_send(sid(s, "Send"), {"body": "hi"}).send
    p2 = s.prepare_send(sid(s, "Send"), {"body": "hi"}, key="second").send
    s.approve_send(p2.id)
    stop.trip("owner said stop")
    assert p1.status == "rejected" and p2.status == "rejected"
    s.observe()
    r = s.commit_send(p2.id)
    assert not r.ok and r.outcome is Outcome.STOPPED and be.ops == []
    assert s.broker.halted
    with pytest.raises(Exception):
        s.broker.prepare("email", "mailto:a@b.test", {"b": 1})
    sess = [r for r in stop_records(audit) if r["source"] == "session"]
    assert sess and set(sess[0]["rejected_sends"]) == {p1.id, p2.id}


def test_stop_between_approval_and_commit_refuses_the_click(tmp_path):
    s, be, stop, audit = mk(tmp_path)
    s.observe()
    p = s.prepare_send(sid(s, "Send"), {"body": "hi"}).send
    s.approve_send(p.id)
    be.on_snapshot = lambda b: stop.trip("mid-commit")      # trips during the fresh read
    r = s.commit_send(p.id)
    assert not r.ok and r.outcome is Outcome.STOPPED
    assert [o for o in be.ops if o.get("payload")] == []


def test_stop_is_passed_to_backend_operations(tmp_path):
    s, be, stop, _ = mk(tmp_path)
    s.observe()
    assert s.act("click", sid(s, "Bold")).ok
    assert be.stop_seen is stop and be.stop_seen() is False


def test_stop_during_perform_reports_stopped(tmp_path):
    s, be, stop, _ = mk(tmp_path)
    s.observe()
    orig = be.perform

    def perform(op, stop=None):
        out = orig(op, stop)
        s.stop.trip("hotkey during drag")
        return out
    be.perform = perform
    r = s.act("click", sid(s, "Bold"))
    assert not r.ok and r.outcome is Outcome.STOPPED


def test_agent_cannot_clear_the_stop(tmp_path):
    s, be, stop, audit = mk(tmp_path)
    stop.trip("x")
    for obj in (stop, s, s.lease, s.broker):
        assert not [m for m in dir(obj) if "reset" in m.lower() or "clear" in m.lower()
                    or "resume" in m.lower()], obj
    (tmp_path / "cu_stop").write_text("")
    with pytest.raises(PermissionError):
        owner_reset(stop)                       # the sentinel must go first
    (tmp_path / "cu_stop").unlink()
    owner_reset(stop, note="owner checked the screen")
    assert not stop.tripped
    # this session stays dead: a reset switch serves a *new* session
    s.observe()
    assert s.act("click", "e1").outcome is Outcome.STOPPED
    assert any(r["source"] == "owner_reset" for r in stop_records(audit))


def test_takeover_hook_saying_stop_escalates(tmp_path):
    s, be, stop, _ = mk(tmp_path)
    s.observe()
    be.took_over = "stop"
    r = s.act("click", sid(s, "Bold"))
    assert r.outcome is Outcome.STOPPED and stop.tripped and "stopped by user" in stop.reason


# -- takeover / turn semantics ---------------------------------------------------------

def test_end_turn():
    l = TurnLease(quiet_period=0)
    l.observed()
    assert l.valid
    l.end_turn()
    assert "turn has ended" in l.check() and l.state == "ended"
    l.begin_turn()
    l.observed()
    assert l.valid and l.state == "active"


def test_session_end_turn_blocks_acting(tmp_path):
    s, be, _, _ = mk(tmp_path)
    s.observe()
    s.end_turn()
    r = s.act("click", sid(s, "Bold"))
    assert r.outcome is Outcome.NEEDS_OBSERVATION and "turn has ended" in r.message


def test_takeover_yields_until_quiet_period_passes(tmp_path):
    clock = Clock()
    s, be, _, _ = mk(tmp_path, quiet=5.0, clock=clock)
    s.observe()
    be.took_over = True
    assert s.act("click", sid(s, "Bold")).outcome is Outcome.NEEDS_OBSERVATION
    assert s.state == "yielded"
    s.observe()                                     # re-read is not enough
    r = s.act("click", sid(s, "Bold"))
    assert r.outcome is Outcome.NEEDS_OBSERVATION and "yielded" in r.message
    clock.t += 3
    be.took_over = True                             # the user is still active: the wait restarts
    s.observe()
    clock.t += 3
    assert "yielded" in s.act("click", sid(s, "Bold")).message
    clock.t += 3
    assert s.state == "active"
    assert s.act("click", sid(s, "Bold")).ok


def test_backend_without_takeover_hook_fails_closed(tmp_path):
    s, be, _, _ = mk(tmp_path, takeover_hook=False)
    s.observe()
    r = s.act("click", sid(s, "Bold"))
    assert r.outcome is Outcome.NOT_ALLOWED and "takeover hook" in r.message and be.ops == []


def test_unattended_session_may_act_without_hook(tmp_path):
    s, be, _, _ = mk(tmp_path, takeover_hook=False, unattended=True)
    s.observe()
    assert s.act("click", sid(s, "Bold")).ok


def test_unattended_declared_in_owner_file(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"unattended": True}))
    s, be, _, _ = mk(tmp_path, takeover_hook=False, app_policy=AppPolicy.load(f))
    s.observe()
    assert s.act("click", sid(s, "Bold")).ok
