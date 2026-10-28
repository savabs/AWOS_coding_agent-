"""D16 policy: UI actions -> classes; destructive buttons always ask; unknown
risk never defaults to allow (scaffold/agent/desktop/policy.py)."""
from types import SimpleNamespace as NS

import pytest

from scaffold.agent.desktop.enforce import Enforcer, Mode
from scaffold.agent.desktop.policy import (
    ALWAYS_ASK, IRREVERSIBLE, Action, ActionClass as C, Grant, Provenance as P, SinkPolicy,
    Verdict, classify_ui_action, exact_grant,
)


def btn(label, role="normal", r="button", **kw):
    return NS(role=r, label=label, text=label, button_role=role, **kw)


@pytest.fixture
def policy(tmp_path):
    return SinkPolicy(tmp_path)


# -- classification -------------------------------------------------------------

def test_destructive_click_is_destructive_ui():
    cls, target, why = classify_ui_action("click", btn("Delete", "destructive"), app="Mail")
    assert cls is C.DESTRUCTIVE_UI and target == "app:Mail/button:Delete"


@pytest.mark.parametrize("label", ["Send", "Post", "Submit", "Publish", "Tweet", "Upload", "Send Now"])
def test_send_like_click_is_send(label):
    assert classify_ui_action("click", btn(label, "destructive"), app="Mail")[0] is C.SEND


def test_send_to_back_is_not_a_send():
    assert classify_ui_action("click", btn("Send to Back"), app="Keynote")[0] is C.UI_ACTION


def test_normal_click_and_typing_are_ui_action():
    assert classify_ui_action("click", btn("Bold"), app="TextEdit")[0] is C.UI_ACTION
    assert classify_ui_action("type", btn("Body", None, r="textarea"), app="TextEdit")[0] is C.UI_ACTION


def test_reads():
    for k in ("observe", "scroll", "hover", "screenshot"):
        assert classify_ui_action(k, None)[0] is C.READ


@pytest.mark.parametrize("el, why", [
    (btn("", "normal"), "no label"),
    (btn("Go", None), "no button role"),
])
def test_unknown_risk_clickable_is_opaque(el, why):
    cls, _, reason = classify_ui_action("click", el)
    assert cls is C.OPAQUE and why in reason


def test_unknown_kind_and_missing_element_are_opaque():
    assert classify_ui_action("drag_and_drop_everything", btn("x"))[0] is C.OPAQUE
    assert classify_ui_action("click", None)[0] is C.OPAQUE
    assert classify_ui_action("", None)[0] is C.OPAQUE


def test_return_follows_default_button():
    field = btn("Name", None, r="textfield")
    cls, _, why = classify_ui_action("key", field, key="Return",
                                     default_button=btn("Delete", "destructive"))
    assert cls is C.DESTRUCTIVE_UI and "default button" in why
    assert classify_ui_action("key", field, key="cmd+Enter",
                              default_button=btn("Send", "destructive"))[0] is C.SEND
    assert classify_ui_action("key", field, key="Return", default_button=btn("OK", "primary"))[0] \
        is C.UI_ACTION


def test_return_in_text_input_sends_only_next_to_a_send_control():
    area = btn("", None, r="textarea")
    assert classify_ui_action("key", None, key="Return", focused=area, send_visible=True)[0] is C.SEND
    assert classify_ui_action("key", None, key="Return", focused=area)[0] is C.UI_ACTION   # Notes
    assert classify_ui_action("key", None, key="Tab", focused=area, send_visible=True)[0] \
        is C.UI_ACTION
    chat = btn("Message", None, r="textfield")
    assert classify_ui_action("key", chat, key="Enter", send_visible=True)[0] is C.SEND
    search = btn("Search", None, r="searchfield")
    assert classify_ui_action("key", search, key="Return", send_visible=True)[0] is C.UI_ACTION


def test_typed_newline_next_to_send_control_is_send():
    area = btn("", None, r="textarea")
    assert classify_ui_action("type", area, text="hi\n", send_visible=True)[0] is C.SEND
    assert classify_ui_action("type", area, text="hi", send_visible=True)[0] is C.UI_ACTION
    assert classify_ui_action("type", area, text="line1\nline2")[0] is C.UI_ACTION


def test_is_send_control():
    from scaffold.agent.desktop.policy import is_send_control
    assert is_send_control(btn("Send", "destructive"))
    assert not is_send_control(btn("Send", None, r="statictext"))     # text, not a control
    assert not is_send_control(btn("Bold"))


# -- decisions --------------------------------------------------------------------

def test_destructive_ui_is_irreversible_and_always_asks():
    assert C.DESTRUCTIVE_UI in IRREVERSIBLE and C.DESTRUCTIVE_UI in ALWAYS_ASK


def test_destructive_click_asks_even_with_a_grant(tmp_path):
    pol = SinkPolicy(tmp_path, [Grant(C.DESTRUCTIVE_UI, "*"), Grant(C.UI_ACTION, "*")])
    d = pol.decide(Action(C.DESTRUCTIVE_UI, "app:Finder/button:Empty Trash", P.OWNER, [P.OWNER]))
    assert d.verdict is Verdict.ASK and d.rule == "R4-critical-point"


def test_destructive_click_from_screen_text_is_denied(policy):
    d = policy.decide(Action(C.DESTRUCTIVE_UI, "app:Mail/button:Delete", P.UNTRUSTED))
    assert d.verdict is Verdict.DENY and d.rule == "R3-untrusted-trigger"


def test_ui_action_asks_without_grant_allows_with_app_grant(tmp_path):
    d = SinkPolicy(tmp_path).decide(Action(C.UI_ACTION, "app:TextEdit/button:Bold"))
    assert d.verdict is Verdict.ASK and d.rule == "R6-ui-ungranted"
    pol = SinkPolicy(tmp_path, [Grant(C.UI_ACTION, "app:TextEdit/*")])
    assert pol.decide(Action(C.UI_ACTION, "app:TextEdit/button:Bold")).verdict is Verdict.ALLOW
    assert pol.decide(Action(C.UI_ACTION, "app:Mail/button:Bold")).verdict is Verdict.ASK


def test_opaque_ui_click_asks(policy):
    assert policy.decide(Action(C.OPAQUE, "app:X/button:")).verdict is Verdict.ASK


def test_enforcer_routes_destructive_to_the_existing_approver(tmp_path):
    asked = []
    enf = Enforcer(SinkPolicy(tmp_path), Mode.ENFORCE, approver=lambda d: asked.append(d) or True)
    res = enf.check_action(Action(C.DESTRUCTIVE_UI, "app:Mail/button:Delete"))
    assert res.allowed and res.outcome == "approved" and asked[0].rule == "R4-critical-point"
    no = Enforcer(SinkPolicy(tmp_path), Mode.ENFORCE, approver=None)
    assert not no.check_action(Action(C.DESTRUCTIVE_UI, "app:Mail/button:Delete")).allowed


def test_no_class_defaults_to_allow(policy):
    """Missing risk never means execute: every class without an explicit
    allow rule is ask or deny with no grant (REA: Apple's planner defaulted
    missing risk to execute)."""
    outside = str(policy.sandbox.parent / "elsewhere")
    for cls in C:
        if cls in (C.READ, C.WRITE_SANDBOX):
            continue
        d = policy.decide(Action(cls, outside, P.AGENT, [P.AGENT]))
        assert d.verdict is not Verdict.ALLOW, cls


def test_exact_grant_matches_only_its_target(tmp_path):
    g = exact_grant(C.SEND, "app:Mail/button:Send->a*b?[c]")
    assert g.matches(Action(C.SEND, "app:Mail/button:Send->a*b?[c]"))
    assert not g.matches(Action(C.SEND, "app:Mail/button:Send->aXXbY[c]"))
    assert not g.matches(Action(C.SEND, "app:Mail/button:Send->abc"))
