"""repair/ review fixes: (1) an optional step / handler guard whose control is legitimately absent keeps the
strict 'missing' (the runner skips it) and never asks the model; (2) a fingerprint label never stores text
typed into a field (usernames, member numbers). No model, no network."""
from types import SimpleNamespace

from scaffold.agent.desktop.live.observe import Snapshot
from scaffold.agent.desktop.repair import Fingerprint, Pick
from scaffold.agent.desktop.repair.core import Repairer, SkillRepair, absence_tolerant, page_candidates
from testbeds.teach_once.skill_runner import Grounding

ANC = ("AXWebArea", "AXGroup[AXLandmarkBanner]")


def header_cands():
    names = ["Help", "Account", "Search"]
    return [Fingerprint(role="button", name=n, context="header", ancestors=ANC, index=i, count=3,
                        siblings=tuple(x for x in names if x != n), ref={"role": "button", "name": n})
            for i, n in enumerate(names)]


def close_target():
    fp = Fingerprint(role="button", name="Close what's new", context="header", ancestors=ANC, index=1, count=4,
                     siblings=("Help", "Account", "Search"))
    return {"role": "AXButton", "name": "Close what's new", "fingerprint": fp.to_dict()}


def missing(page, target, *, exclude=(), fuzzy=False):
    return Grounding("missing", rung="none")


def program_with(target, *, optional=True, guard=None):
    op = {"id": "s1", "app": "web", "action": "click", "target": target, "optional": optional}
    return {"setup": [op], "loop": None, "finally": [],
            "handlers": [{"id": "h1", "app": "web", "guard": {"text_all": [], "control": guard}, "do": []}]
            if guard else []}


class Boom:
    calls = 0

    def __call__(self, fp, items):
        Boom.calls += 1
        return Pick(0, "first", calls=1)


def test_precondition_rule_tier_is_unsure_here():
    t = close_target()
    res = Repairer().refind(Fingerprint.from_dict(t["fingerprint"]), header_cands())
    assert res.status == "ambiguous"                     # the reviewer's 0.57-between-floor-and-accept case


def test_optional_step_stays_missing_and_skips_the_model():
    t = close_target()
    Boom.calls = 0
    hook = SkillRepair(picker=Boom(), candidates=lambda page: header_cands())
    hook.begin(program_with(t))
    g = hook.wrap(missing)(object(), t)
    assert g.status == "missing" and g.element is None   # SkillRunner._resolve skips an optional 'missing'
    assert Boom.calls == 0 and hook.model_calls == 0
    assert hook.events[-1]["tolerant"] is True and not hook.pending


def test_required_step_still_reaches_the_model():
    t = close_target()
    Boom.calls = 0
    hook = SkillRepair(picker=Boom(), candidates=lambda page: header_cands())
    hook.begin(program_with(t, optional=False))
    g = hook.wrap(missing)(object(), t)
    assert g.status == "ok" and Boom.calls == 1          # non-optional behaviour unchanged


def test_without_a_picker_a_required_step_is_still_ambiguous():
    t = close_target()
    hook = SkillRepair(candidates=lambda page: header_cands())
    hook.begin(program_with(t, optional=False))
    assert hook.wrap(missing)(object(), t).status == "ambiguous"


def test_handler_guard_control_is_absence_tolerant():
    guard = close_target()
    other = {"role": "AXButton", "name": "Next"}
    prog = program_with(other, optional=False, guard=guard)
    assert set(absence_tolerant(prog)) == {id(guard)}
    Boom.calls = 0
    hook = SkillRepair(picker=Boom(), candidates=lambda page: header_cands())
    hook.begin(prog)
    assert hook.wrap(missing)(object(), guard).status == "missing" and Boom.calls == 0


def test_optional_step_still_gets_a_confident_rule_repair():
    t = close_target()
    cands = header_cands() + [Fingerprint(role="button", name="Close what is new", context="header", ancestors=ANC,
                                          index=1, count=4, siblings=("Help", "Account", "Search"),
                                          ref={"role": "button", "name": "Close what is new"})]
    hook = SkillRepair(candidates=lambda page: cands)
    hook.begin(program_with(t))
    g = hook.wrap(missing)(object(), t)
    assert g.status == "ok" and g.element["name"] == "Close what is new" and g.rung == "repair_rule"


# --------------------------------------------------------- label privacy --

def login_page():
    tree = {"role": "AXWindow", "children": [{"role": "AXWebArea", "children": [{"role": "AXGroup", "children": [
        {"role": "AXStaticText", "value": "Sign in to your account"},
        {"role": "AXTextField", "title": "Member number", "value": "4417-2290-1133"},
        {"role": "AXSecureTextField", "title": "Password", "value": "hunter2"},
        {"role": "AXComboBox", "description": "Account", "value": "Checking 99887766"},
        {"role": "AXButton", "title": "Sign in"}]}]}]}
    snap = Snapshot(tree=tree, truncated=False, app={})
    els = [{"role": "textbox", "name": "Member number", "ax_path": [0, 0, 0, 1]},
           {"role": "textbox", "name": "Password", "ax_path": [0, 0, 0, 2]},
           {"role": "combobox", "name": "Account", "ax_path": [0, 0, 0, 3]},
           {"role": "button", "name": "Sign in", "ax_path": [0, 0, 0, 4]}]
    return SimpleNamespace(elements=els, _snap=snap)


def test_label_never_holds_typed_values():
    fps = {c.name: c for c in page_candidates(login_page())}
    lab = fps["Sign in"].label
    assert "4417" not in lab and "hunter2" not in lab and "99887766" not in lab
    assert "Member number" in lab and "Sign in to your account" in lab      # labels / static text are kept
    dumped = repr([c.to_dict() for c in fps.values()])
    assert "4417-2290-1133" not in dumped and "hunter2" not in dumped and "99887766" not in dumped


def test_enrolled_fingerprint_written_to_the_skill_has_no_typed_value():
    page = login_page()
    btn = page.elements[3]
    t = {"role": "AXButton", "name": "Sign in"}
    prog = {"id": "x", "version": 1, "setup": [{"id": "s1", "app": "web", "action": "click", "target": t}],
            "loop": None, "finally": []}
    hook = SkillRepair()
    hook.begin(prog)
    g = hook.wrap(lambda p, tg, exclude=(), fuzzy=False: Grounding("ok", btn, "exact"))(page, t)
    assert g.status == "ok"
    saved = {}
    out = hook.finish(prog, ok=True, save=lambda new, root: saved.setdefault("p", new) and "skill.json")
    assert out["written"] and "4417" not in repr(saved["p"]) and "Member number" in repr(saved["p"])
