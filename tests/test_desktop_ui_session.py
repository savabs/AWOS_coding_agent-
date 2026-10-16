"""D16 session: turn-scoped lease, user takeover, guarded actions, two-phase
UI sends — all on a fake backend (scaffold/agent/desktop/ui_session.py)."""
import copy

import pytest

from scaffold.agent.desktop.enforce import Enforcer, Mode
from scaffold.agent.desktop.policy import (
    ActionClass as C, AuditLog, Grant, Provenance as P, SinkPolicy,
)
from scaffold.agent.desktop.sends import SendApproval
from scaffold.agent.desktop.ui_session import DesktopSession, TurnLease


def node(role, title="", frame=(0, 0, 10, 10), children=(), **kw):
    return {"role": role, "title": title,
            "frame": {"x": frame[0], "y": frame[1], "w": frame[2], "h": frame[3]},
            "children": list(children), **kw}


def mail(sheet=False, draft=""):
    kids = [node("AXButton", "Bold", (10, 10, 40, 20)),
            node("AXButton", "Delete", (60, 10, 50, 20)),
            node("AXTextArea", "", (10, 50, 400, 200), value=draft, focused=True),
            node("AXButton", "Send", (420, 260, 60, 24)),
            node("AXButton", "", (500, 10, 20, 20))]                 # unlabelled icon
    if sheet:
        kids.append(node("AXSheet", "", (100, 100, 300, 100), [
            node("AXButton", "Cancel", (120, 160, 80, 24)),
            node("AXButton", "Don't Save", (220, 160, 80, 24), default=True)]))
    return node("AXApplication", "Mail", (0, 0, 800, 600),
                [node("AXWindow", "Compose", (0, 0, 800, 600), kids)])


class FakeBackend:
    def __init__(self, tree):
        self.tree = tree
        self.ops = []
        self.took_over = False

    def snapshot(self):
        return copy.deepcopy(self.tree)

    def perform(self, op):
        self.ops.append(op)
        return "done"

    def user_took_over(self):
        t, self.took_over = self.took_over, False
        return t


def session(tmp_path, tree=None, approver=None, send_approver=lambda p: True, grants=None):
    grants = [Grant(C.UI_ACTION, "app:Mail/*")] if grants is None else grants
    audit = AuditLog(tmp_path / "audit.jsonl")
    enf = Enforcer(SinkPolicy(tmp_path / "sb", grants), Mode.ENFORCE, approver, audit)
    (tmp_path / "sb").mkdir(exist_ok=True)
    be = FakeBackend(tree or mail())
    return DesktopSession(be, enf, send_approver=send_approver), be, audit


def sid(s, text):
    return next(e.sid for e in s.latest.elements if e.text == text)


# -- lease ----------------------------------------------------------------------

def test_lease_unit():
    l = TurnLease()
    assert "no observation" in l.check()
    l.observed()
    assert l.valid
    l.acted()
    assert "observe before acting again" in l.check()
    l.observed()
    l.user_takeover()
    assert "user took over" in l.check()
    l.observed()
    l.begin_turn()
    assert "in this turn" in l.check()
    l.observed()
    assert l.valid


def test_act_before_observe_is_blocked(tmp_path):
    s, be, _ = session(tmp_path)
    r = s.act("click", "e1")
    assert not r.ok and r.by == "lease" and be.ops == []


def test_one_action_per_observation(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    bold = sid(s, "Bold")
    assert s.act("click", bold).ok
    r = s.act("click", bold)
    assert not r.ok and r.by == "lease"
    s.observe()
    assert s.act("click", bold).ok
    assert len(be.ops) == 2


def test_user_takeover_requires_reread(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    s.user_takeover()
    r = s.act("click", sid(s, "Bold"))
    assert not r.ok and "user took over" in r.message
    s.observe()
    assert s.act("click", sid(s, "Bold")).ok


def test_takeover_detected_through_backend_hook(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    be.took_over = True
    r = s.act("click", sid(s, "Bold"))
    assert not r.ok and r.by == "lease" and be.ops == []


def test_broken_takeover_hook_counts_as_takeover(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    be.user_took_over = lambda: 1 / 0
    assert s.act("click", sid(s, "Bold")).by == "lease"


def test_new_turn_requires_reread(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    s.begin_turn()
    assert s.act("click", sid(s, "Bold")).by == "lease"


def test_blocked_action_does_not_cost_the_lease(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    assert not s.act("click", sid(s, "Delete")).ok           # no approver: blocked
    assert s.act("click", sid(s, "Bold")).ok                 # lease still valid


def test_stale_element_id(tmp_path):
    s, be, _ = session(tmp_path, tree=mail(sheet=True))
    s.observe()
    cancel = sid(s, "Cancel")
    be.tree = mail()
    s.observe()
    r = s.act("click", cancel)
    assert not r.ok and r.by == "stale"


# -- destructive / unknown risk ----------------------------------------------------

def test_destructive_click_needs_approval(tmp_path):
    s, be, audit = session(tmp_path)
    s.observe()
    r = s.act("click", sid(s, "Delete"))
    assert not r.ok and r.action_class is C.DESTRUCTIVE_UI and r.by == "policy"
    assert be.ops == []
    rec = [x for x in audit.read() if x.get("kind") == "enforce"][-1]
    assert rec["action_class"] == "destructive_ui" and rec["outcome"] == "blocked"


def test_destructive_click_runs_when_owner_approves(tmp_path):
    asked = []
    s, be, _ = session(tmp_path, approver=lambda d: asked.append(d.action.target) or True)
    s.observe()
    r = s.act("click", sid(s, "Delete"))
    assert r.ok and r.enforcement.outcome == "approved"
    assert asked == ["app:Mail/button:Delete"]


def test_ui_grant_does_not_cover_destructive(tmp_path):
    s, be, _ = session(tmp_path, grants=[Grant(C.UI_ACTION, "*"), Grant(C.DESTRUCTIVE_UI, "*")])
    s.observe()
    assert not s.act("click", sid(s, "Delete")).ok


def test_return_on_destructive_default_button_needs_approval(tmp_path):
    s, be, _ = session(tmp_path, tree=mail(sheet=True))
    s.observe()
    r = s.act("key", key="Return")
    assert not r.ok and r.action_class is C.DESTRUCTIVE_UI


def test_unlabelled_button_is_unknown_risk(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    icon = next(e.sid for e in s.latest.elements if e.role == "button" and not e.text)
    r = s.act("click", icon)
    assert not r.ok and r.action_class is C.OPAQUE


def test_ungranted_app_asks(tmp_path):
    s, be, _ = session(tmp_path, grants=[])
    s.observe()
    r = s.act("click", sid(s, "Bold"))
    assert not r.ok and r.enforcement.decision.rule == "R6-ui-ungranted"


def test_typed_newline_in_compose_goes_to_send_path(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    area = next(e.sid for e in s.latest.elements if e.role == "textarea")
    assert s.act("type", area, text="hi\n").by == "send-path"


def test_return_in_editor_without_send_control_is_plain_input(tmp_path):
    tree = node("AXApplication", "Notes", (0, 0, 800, 600), [node("AXWindow", "Note", (0, 0, 800, 600), [
        node("AXTextArea", "", (10, 50, 400, 200), focused=True)])])
    s, be, _ = session(tmp_path, tree=tree, grants=[Grant(C.UI_ACTION, "app:Notes/*")])
    s.observe()
    assert s.act("key", key="Return").ok


def test_typing_is_allowed_with_app_grant(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    area = next(e.sid for e in s.latest.elements if e.role == "textarea")
    r = s.act("type", area, text="Hello")
    assert r.ok and be.ops[-1]["text"] == "Hello"


# -- two-phase UI sends ----------------------------------------------------------

def test_send_button_cannot_be_clicked_directly(tmp_path):
    s, be, _ = session(tmp_path, grants=[Grant(C.UI_ACTION, "*"), Grant(C.SEND, "*")])
    s.observe()
    r = s.act("click", sid(s, "Send"))
    assert not r.ok and r.by == "send-path" and be.ops == []


def test_return_in_compose_area_is_routed_to_send_path(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    r = s.act("key", key="Return")
    assert not r.ok and r.by == "send-path"


def test_full_send_flow_with_edit_and_single_commit(tmp_path):
    s, be, audit = session(tmp_path, send_approver=lambda p: SendApproval(True, {"body": "Edited"}))
    s.observe()
    send = sid(s, "Send")
    r = s.prepare_send(send, {"body": "Draft"}, recipient="bob@x.test")
    assert r.ok and r.send.target == "app:Mail/button:Send->bob@x.test"
    assert s.approve_send(r.send.id).ok
    c = s.commit_send(r.send.id)
    assert c.ok
    assert be.ops == [{"op": "click", "sid": send, "center": be.ops[0]["center"],
                       "payload": {"body": "Edited"}, "idempotency_key": r.send.key}]
    s.observe()
    again = s.commit_send(r.send.id)
    assert not again.ok and again.by == "broker" and len(be.ops) == 1


def test_commit_needs_fresh_read_after_takeover(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    p = s.prepare_send(sid(s, "Send"), {"body": "x"}).send
    s.approve_send(p.id)
    s.user_takeover()
    r = s.commit_send(p.id)
    assert not r.ok and r.by == "lease" and be.ops == []
    s.observe()
    assert s.commit_send(p.id).ok


def test_commit_refused_when_send_button_gone(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    p = s.prepare_send(sid(s, "Send"), {"body": "x"}).send
    s.approve_send(p.id)
    t = mail()
    t["children"][0]["children"] = [c for c in t["children"][0]["children"] if c["title"] != "Send"]
    be.tree = t
    s.observe()
    r = s.commit_send(p.id)
    assert not r.ok and r.by == "stale" and be.ops == []


def test_commit_without_approval(tmp_path):
    s, be, _ = session(tmp_path, send_approver=lambda p: False)
    s.observe()
    p = s.prepare_send(sid(s, "Send"), {"body": "x"}).send
    assert not s.approve_send(p.id).ok
    assert not s.commit_send(p.id).ok and be.ops == []


def test_prepare_send_on_non_send_control(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    r = s.prepare_send(sid(s, "Bold"), {"body": "x"})
    assert not r.ok and r.by == "send-path"


def test_send_started_by_screen_text_is_refused(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    r = s.prepare_send(sid(s, "Send"), {"body": "x"}, initiator=P.UNTRUSTED)
    assert not r.ok and r.by == "broker" and "R3" in r.message


def test_backend_failure_mid_send_is_not_retried(tmp_path):
    s, be, _ = session(tmp_path)
    s.observe()
    p = s.prepare_send(sid(s, "Send"), {"body": "x"}).send
    s.approve_send(p.id)

    def boom(op):
        raise TimeoutError("no ack")
    be.perform = boom
    r = s.commit_send(p.id)
    assert not r.ok and r.by == "backend" and p.status == "failed"
    s.observe()
    assert s.commit_send(p.id).by == "broker"


def test_observation_messages_flow_through_session(tmp_path):
    s, be, _ = session(tmp_path)
    assert s.observe().kind == "full"
    assert s.observe().kind == "no_change"
    be.tree = mail(draft="Hello")
    m = s.observe()
    assert m.kind == "diff" and len(m.changed) == 1
