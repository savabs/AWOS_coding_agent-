"""WP6 grounding: ranked, uniqueness-checked locators; refusal instead of guessing;
the dev_dashboard ui_drift acceptance (no wrong-element clicks)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scaffold.agent.desktop import grounding as g
from scaffold.agent.desktop.ax_compress import compress, render
from scaffold.agent.desktop.ax_observe import Observer
from scaffold.agent.desktop.grounding import (
    Locator, OcrHit, Target, Token, build_index, evaluate, generate_locators, ground,
    ground_dismiss, record_target,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "testbeds" / "dev_dashboard"))
import dd_seed  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def node(role, title="", frame=None, children=None, **kw):
    d = {"role": role}
    if title:
        d["title"] = title
    if frame:
        d["frame"] = list(frame)
    if children:
        d["children"] = children
    d.update(kw)
    return d


def win(title, *children, frame=(0, 0, 1000, 800)):
    return node("AXWindow", title, frame, list(children))


def two_saves():
    return win("Editor",
               node("AXGroup", "Profile", (0, 0, 500, 400), [
                   node("AXTextField", "", (10, 40, 200, 24), description="Display name"),
                   node("AXButton", "Save", (10, 300, 80, 24))]),
               node("AXGroup", "Password", (500, 0, 500, 400), [
                   node("AXButton", "Save", (510, 300, 80, 24))]))


def at(tree, path):
    n = tree
    for i in path[1:]:
        n = n["children"][i]
    return n


# ---------------------------------------------------------------------------
# Playwright port: helpers, ranking, uniqueness
# ---------------------------------------------------------------------------
def test_guid_like_ids_rejected():
    assert g.is_guid_like("a1B2c3D4e5F6")
    assert g.is_guid_like("8f3a9c1e-77b2-4c1d")
    assert not g.is_guid_like("save-button")
    assert not g.is_guid_like("keyName")


def test_text_alternatives_strip_numbers():
    alts = dict(g.text_alternatives("Inbox 42"))
    assert "Inbox" in alts and alts["Inbox"] == 2
    assert "Inbox 42" in alts
    assert dict(g.text_alternatives("3 new messages"))["new messages"] == 2
    long = "word " * 30
    assert all(len(t) <= 80 for t, _ in g.text_alternatives(long.strip()))


def test_combined_score_weights_earlier_tokens():
    a = Token("container", "X", "group", True, 105)
    b = Token("role", "Save", "button", True, 107)
    assert Locator((a, b)).score == 105 * 2 + 107


def test_id_ranks_above_role_name_and_role_name_above_text():
    t = win("W", node("AXButton", "Export", (10, 10, 80, 20), identifier="export-btn"),
            node("AXButton", "", (100, 10, 80, 20), children=[node("AXStaticText", value="Print")]))
    locs = generate_locators(t, (0, 0))
    assert locs[0].tokens[0].kind == "id" and locs[0].score == 1
    assert locs[1].tokens[-1].kind == "role"
    # a button named only by its child text is still role+name (accessible name from content)
    locs2 = generate_locators(t, (0, 1))
    assert str(locs2[0]).startswith("role=button")


def test_every_generated_locator_is_unique_to_its_target():
    t = two_saves()
    idx = build_index(t)
    for path in [(0, 0, 0), (0, 0, 1), (0, 1, 0)]:
        locs = generate_locators(t, path, k=3)
        assert locs, path
        for loc in locs:
            assert [n.path for n in evaluate(idx, loc)] == [path], str(loc)


def test_duplicate_name_gets_container_scope_not_first_match():
    locs = generate_locators(two_saves(), (0, 1, 0))
    assert locs[0].tokens[0].kind == "container" and locs[0].tokens[0].value == "Password"


def test_nth_only_up_to_five():
    rows = [node("AXButton", "Edit", (10, 10 + 30 * i, 60, 20)) for i in range(8)]
    t = win("W", *rows)
    assert any(tk.kind == "nth" for tk in generate_locators(t, (0, 2))[0].tokens)
    assert generate_locators(t, (0, 7)) == []      # nth=7 is never generated


def test_icon_font_name_not_used():
    t = win("W", node("AXButton", "", (10, 10, 20, 20), identifier="gear"))
    assert all(tk.kind != "role" for loc in generate_locators(t, (0, 0)) for tk in loc.tokens)


def test_length_penalty_applies_in_band_only():
    t = win("W", node("AXButton", "A very long button title for testing penalties", (0, 0, 300, 20)))
    role_tok = [l for l in generate_locators(t, (0, 0), k=5) if l.tokens[0].kind == "role"][0].tokens[0]
    assert K_in_band(role_tok.score)


def K_in_band(score):
    return g.K_ROLE_WITH_NAME - 2 <= score <= g.K_ROLE_WITH_NAME + g.K_EXACT_PENALTY + g.K_TEXT_SCORE_RANGE


def test_locator_and_target_round_trip():
    t = record_target(two_saves(), (0, 1, 0))
    d = json.loads(json.dumps(t.to_dict()))
    t2 = Target.from_dict(d)
    assert t2 == t
    assert ground(two_saves(), t2).match.path == (0, 1, 0)


# ---------------------------------------------------------------------------
# refusal semantics
# ---------------------------------------------------------------------------
def test_two_save_buttons_refuse_as_ambiguous():
    r = ground(two_saves(), Target(role="button", name="Save"))
    assert not r.ok and r.refusal.reason == "ambiguous"
    assert len(r.refusal.candidates) == 2


def test_container_hint_disambiguates():
    r = ground(two_saves(), Target(role="button", name="Save", container=(("group", "Password"),)))
    assert r.ok and r.match.path == (0, 1, 0) and r.match.center == (550, 312)


def test_row_hint_disambiguates():
    rows = [node("AXRow", "", (0, 30 * i, 600, 30), [
        node("AXCell", "", (0, 30 * i, 300, 30), [node("AXStaticText", value=name)]),
        node("AXButton", "Archive", (400, 30 * i + 4, 80, 22))]) for i, name in enumerate(["Bob", "Ann"])]
    t = win("Mail", node("AXTable", "Inbox", (0, 0, 600, 300), rows))
    assert ground(t, Target(role="button", name="Archive")).refusal.reason == "ambiguous"
    r = ground(t, Target(role="button", name="Archive", row="Ann | Archive"))
    assert r.ok and r.match.path == (0, 0, 1, 1)


def test_ordinal_trusted_only_when_group_size_unchanged():
    t = two_saves()
    rec = record_target(t, (0, 1, 0))
    bare = Target(role="button", name="Save", nth=rec.nth, nth_of=rec.nth_of)
    r = ground(t, bare)
    assert r.ok and r.match.path == (0, 1, 0) and r.confidence == pytest.approx(0.7)
    t["children"].append(node("AXGroup", "Other", (0, 500, 500, 200), [node("AXButton", "Save", (10, 600, 80, 24))]))
    assert ground(t, bare).refusal.reason == "ambiguous"


def test_ax_id_requires_role_agreement_and_uniqueness():
    t = win("W", node("AXStaticText", "", (10, 10, 50, 20), value="hi", identifier="ok"),
            node("AXButton", "OK", (10, 50, 50, 20)))
    r = ground(t, Target(role="button", name="OK", ax_id="ok"))
    assert r.ok and r.match.path == (0, 1) and r.tier == "role_name"
    t2 = win("W", node("AXButton", "A", (10, 10, 50, 20), identifier="dup"),
             node("AXButton", "B", (10, 50, 50, 20), identifier="dup"))
    assert ground(t2, Target(role="button", ax_id="dup")).refusal.reason == "ambiguous"


def test_truncated_by_node_budget_or_flag():
    many = win("W", *[node("AXStaticText", value=str(i), frame=(0, i, 10, 1)) for i in range(50)],
               node("AXButton", "Go", (0, 700, 50, 20)))
    assert ground(many, Target(role="button", name="Go"), max_nodes=20).refusal.reason == "truncated"
    assert ground(many, Target(role="button", name="Go")).ok
    assert ground({"tree": many, "truncated": True}, Target(role="button", name="Go")).refusal.reason == "truncated"


def test_lazy_list_not_found_is_truncated_not_absent():
    t = win("Mail", node("AXTable", "Inbox", (0, 0, 600, 300), [
        node("AXRow", "", (0, 0, 600, 30), [node("AXButton", "Open Bob", (10, 4, 80, 22))])], total=340))
    r = ground(t, Target(role="button", name="Open Zed"))
    assert r.refusal.reason == "truncated"


def test_window_scoping():
    t = node("AXApplication", "Mail", None, [
        win("Inbox", node("AXButton", "Send", (10, 10, 50, 20)), frame=(0, 0, 500, 500)),
        win("Draft", node("AXButton", "Send", (510, 10, 50, 20)), frame=(500, 0, 500, 500))])
    assert ground(t, Target(role="button", name="Send")).refusal.reason == "ambiguous"
    r = ground(t, Target(role="button", name="Send", window="Draft"))
    assert r.ok and r.match.center == (535, 20)
    assert ground(t, Target(role="button", name="Send", window="Nope")).refusal.reason == "window_not_found"
    t["children"].append(win("Draft", frame=(0, 600, 100, 100)))
    assert ground(t, Target(role="button", name="Send", window="Draft")).refusal.reason == "window_ambiguous"


def test_modal_blocks_and_dismiss_finds_single_cancel():
    t = win("W", node("AXButton", "Compose", (10, 10, 80, 20)),
            node("AXSheet", "Update available", (300, 200, 400, 200), [
                node("AXButton", "Install now", (320, 350, 100, 24)),
                node("AXButton", "Not now", (440, 350, 100, 24))]))
    r = ground(t, Target(role="button", name="Compose"))
    assert r.refusal.reason == "blocked_by_modal"
    assert {c["name"] for c in r.refusal.candidates} >= {"Not now", "Install now"}
    d = ground_dismiss(t)
    assert d.ok and d.match.name == "Not now"
    # a control inside the modal is fine
    assert ground(t, Target(role="button", name="Install now")).ok
    at(t, (0, 1))["children"].append(node("AXButton", "Cancel", (560, 350, 100, 24)))
    assert ground_dismiss(t).refusal.reason == "ambiguous"


def test_risk_escalation_refused():
    t = win("W", node("AXButton", "Delete", (10, 10, 80, 20), identifier="primary-action"))
    r = ground(t, Target(role="button", name="Archive", ax_id="primary-action", button_role="normal"))
    assert r.refusal.reason == "risk_escalation"
    # an AX value cannot downgrade it: a "normal" hint on Delete stays destructive
    at(t, (0, 0))["button_role"] = "normal"
    assert ground(t, Target(role="button", ax_id="primary-action", button_role="normal")).refusal.reason == "risk_escalation"


def test_disabled_and_offscreen_refused():
    t = win("W", node("AXButton", "Pay", (10, 10, 80, 20), enabled=False),
            node("AXScrollArea", "", (0, 100, 500, 100), [node("AXButton", "Later", (10, 400, 80, 20))]))
    assert ground(t, Target(role="button", name="Pay")).refusal.reason == "disabled"
    assert ground(t, Target(role="button", name="Later")).refusal.reason == "not_visible"


def test_never_returns_first_match_on_text_tier():
    t = win("W", node("AXButton", "", (10, 10, 80, 20), children=[node("AXStaticText", value="Go")]),
            node("AXLink", "", (10, 50, 80, 20), children=[node("AXStaticText", value="Go")]))
    assert ground(t, Target(text="Go")).refusal.reason == "ambiguous"
    assert ground(t, Target(role="link", text="Go")).match.role == "link"


# ---------------------------------------------------------------------------
# renamed / moved controls (relaxed tier)
# ---------------------------------------------------------------------------
def form(*buttons):
    return win("W", node("AXGroup", "Checkout", (0, 0, 600, 400), list(buttons)))


def test_renamed_label_resolved_via_container_and_position():
    base = form(node("AXButton", "Back", (10, 350, 80, 24)), node("AXButton", "Continue", (500, 350, 80, 24)))
    rec = record_target(base, (0, 0, 1))
    drift = form(node("AXButton", "Back", (10, 350, 80, 24)), node("AXButton", "Next", (500, 350, 80, 24)))
    r = ground(drift, rec)
    assert r.ok and r.match.name == "Next" and r.tier == "relaxed"
    assert 0.5 <= r.confidence <= 0.75


def test_relaxed_needs_name_evidence_when_several_candidates():
    rec = record_target(form(node("AXButton", "Continue", (500, 350, 80, 24)),
                             node("AXButton", "Help", (10, 350, 80, 24))), (0, 0, 0))
    drift = form(node("AXButton", "Proceed to payment options", (10, 350, 80, 24)),
                 node("AXButton", "Gift card", (500, 350, 80, 24)))
    r = ground(drift, rec)
    # Gift card holds the old position but has no name evidence: never chosen.
    # "Proceed to payment options" is a weak (0.25) name match: refused as low confidence.
    assert not r.ok or r.match.name.startswith("Proceed")
    assert r.refusal is None or r.refusal.reason == "low_confidence"
    drift2 = form(node("AXButton", "Pay later", (10, 350, 80, 24)), node("AXButton", "Gift card", (500, 350, 80, 24)))
    r2 = ground(drift2, rec)
    assert not r2.ok and r2.refusal.reason in ("low_confidence", "ambiguous")


def test_relaxed_never_picks_destructive_even_at_old_position():
    base = form(node("AXButton", "Save draft", (10, 350, 80, 24)))
    rec = record_target(base, (0, 0, 0))
    drift = form(node("AXButton", "Discard draft", (10, 350, 80, 24)))
    r = ground(drift, rec)
    assert not r.ok


def test_destructive_target_is_never_resolved_fuzzily():
    base = form(node("AXButton", "Delete account", (10, 350, 120, 24)))
    rec = record_target(base, (0, 0, 0))
    drift = form(node("AXButton", "Remove account permanently", (10, 350, 120, 24)))
    r = ground(drift, rec)
    assert not r.ok and r.refusal.reason == "not_found"


# ---------------------------------------------------------------------------
# previous stable id, OCR stub, compressed observation hints
# ---------------------------------------------------------------------------
def test_prev_sid_from_observation_and_match_carries_sid():
    t = two_saves()
    obs = Observer(diff=False).observe(t)
    save2 = [e for e in obs.elements if e.text == "Save"][1]
    r = ground(t, Target(role="button", name="Save", prev_sid=save2.sid), observation=obs)
    assert r.ok and r.tier == "sid" and r.sid == save2.sid
    r2 = ground(t, Target(role="button", name="Save", container=(("group", "Profile"),)), observation=obs)
    assert r2.sid == [e for e in obs.elements if e.text == "Save"][0].sid


def test_prev_sid_ignored_when_marked_ambiguous():
    ob = Observer(diff=False)
    t = two_saves()
    obs1 = ob.observe(t)
    sid = [e for e in obs1.elements if e.text == "Save"][1].sid
    t["children"].append(node("AXGroup", "Password", (0, 500, 500, 200), [node("AXButton", "Save", (10, 600, 80, 24))]))
    obs2 = ob.observe(t)
    assert sid in obs2.ambiguous
    r = ground(t, Target(role="button", name="Save", prev_sid=sid), observation=obs2)
    assert not r.ok and r.refusal.reason == "ambiguous"


class FakeOcr:
    def __init__(self, hits):
        self.hits = hits

    def find_text(self, text, region=None):
        return self.hits


def test_ocr_fallback_interface():
    empty = win("Canvas", node("AXGroup", "", (0, 0, 800, 600)))
    tgt = Target(text="Export")
    assert ground(empty, tgt, ocr=g.NullOcr()).refusal.reason == "not_found"
    assert ground(empty, tgt, ocr=g.AppleVisionOcr()).refusal.reason == "not_found"
    one = FakeOcr([OcrHit("Export", (100, 100, 60, 20), 1.0)])
    r = ground(empty, tgt, ocr=one)
    assert r.ok and r.tier == "ocr" and r.center == (130, 110) and r.confidence <= 0.5
    two = FakeOcr([OcrHit("Export", (100, 100, 60, 20)), OcrHit("Export", (300, 100, 60, 20))])
    assert ground(empty, tgt, ocr=two).refusal.reason == "ambiguous"
    bad = FakeOcr([OcrHit("Delete", (100, 100, 60, 20))])
    assert ground(empty, Target(role="button", text="Delete", button_role="normal"), ocr=bad).refusal.reason \
        == "risk_escalation"


def test_compress_locator_hints_are_opt_in():
    t = two_saves()
    plain = compress(t)
    assert all(e.locator == "" for e in plain)
    assert "loc=" not in render(plain) and "locator" not in plain[0].to_dict()
    hinted = compress(t, locators=True)
    assert render(hinted) == render(plain)            # default text unchanged
    saves = [e for e in hinted if e.text == "Save"]
    assert saves[0].locator != saves[1].locator and "Password" in saves[1].locator
    assert 'loc="container=group' in render(hinted, locators=True)
    assert hinted[0].to_dict().get("locator") or hinted[1].to_dict().get("locator")


def test_target_from_element():
    obs = Observer(diff=False).observe(two_saves())
    e = [x for x in obs.elements if x.text == "Save"][1]
    tgt = Target.from_element(e)
    assert tgt.window == "Editor" and ("group", "Password") in tgt.container
    r = ground(two_saves(), tgt, observation=obs)
    assert r.ok and r.match.path == e.depth_path


# ---------------------------------------------------------------------------
# dev_dashboard ui_drift acceptance (scripted driver, no model)
# ---------------------------------------------------------------------------
def drive(variant: str, task: str, min_confidence: float = 0.5) -> dict:
    """Record targets on the baseline screens, replay them on ``variant``
    with grounding: click / type only at a grounded match, dismiss an
    unexpected interstitial via its single cancel button, stop on refusal."""
    base = dd_seed.ui_screens("baseline")
    app = dd_seed.UiApp(variant)
    for step in dd_seed.UI_TASKS[task]:
        scr = base[step["screen"]]
        tgt = record_target(scr, dd_seed.find_truth(scr, step["truth"]))
        for _ in range(3):
            snap = app.snapshot()
            r = ground(snap, tgt, min_confidence=min_confidence)
            if r.ok:
                hit = at(snap, r.match.path).get("_truth")
                assert hit == step["truth"], f"grounding chose {hit} for {step['truth']}"
                if step["action"] == "click":
                    app.click(*r.center)
                else:
                    app.type(*r.center, step["text"])
                break
            if r.refusal.reason == "blocked_by_modal":
                d = ground_dismiss(snap)
                if not d.ok:
                    return {"outcome": "refused", "reason": "blocked_by_modal", "events": app.events}
                app.click(*d.center)
                continue
            return {"outcome": "refused", "reason": r.refusal.reason, "events": app.events,
                    "refusal": r.refusal}
    return {"outcome": "done", "screen": app.screen, "events": app.events}


@pytest.mark.parametrize("task", sorted(dd_seed.UI_TASKS))
def test_baseline_replays_exactly(task):
    res = drive("baseline", task)
    assert res["outcome"] == "done"
    assert [e["truth"] for e in res["events"]] == [s["truth"] for s in dd_seed.UI_TASKS[task]]


@pytest.mark.parametrize("task", sorted(dd_seed.UI_TASKS))
def test_ui_drift_no_wrong_element_clicks(task):
    res = drive("ui_drift", task)
    exp = dd_seed.UI_DRIFT_EXPECTED[task]
    assert res["outcome"] == exp["outcome"], res
    if "reason" in exp:
        assert res["reason"] == exp["reason"]
    hits = [e["truth"] for e in res["events"]]
    assert hits == exp["hits"]
    assert not set(hits) & dd_seed.DESTRUCTIVE_TRUTHS
    assert "nothing" not in hits
    if exp["outcome"] == "done":
        assert res["screen"] == exp["final_screen"]


def test_ui_drift_ambiguity_refusal_lists_both_rows():
    res = drive("ui_drift", "revoke_ci_readonly")
    assert res["reason"] == "ambiguous"
    assert len(res["refusal"].candidates) == 2
    assert all(c["name"] == "Revoke key ci-readonly" for c in res["refusal"].candidates)


def test_ui_drift_fixture_has_a_position_trap():
    """A coordinate replay of the baseline would click the destructive control."""
    base = dd_seed.ui_screens("baseline")["keys"]
    r = ground(base, record_target(base, dd_seed.find_truth(base, "create_key")))
    app = dd_seed.UiApp("ui_drift")
    app.screen = "keys"
    assert app.click(*r.center) == "regenerate_all"


@pytest.mark.parametrize("screen", ["keys", "new_key"])
def test_ui_drift_every_baseline_control_resolves_correctly_or_refuses(screen):
    base = dd_seed.ui_screens("baseline")[screen]
    drift = dd_seed.ui_screens("ui_drift")[screen]
    idx = build_index(base)
    resolved = refused = 0
    for n in idx.nodes:
        truth = n.raw.get("_truth")
        if not truth or not n.interactive:
            continue
        r = ground(drift, record_target(base, n.path))
        if r.ok:
            assert at(drift, r.match.path).get("_truth") == truth, (truth, r.to_dict())
            resolved += 1
        else:
            refused += 1
    assert resolved >= 4
    if screen == "keys":
        assert refused >= 2          # duplicated revoke row, renamed destructive rotate


def test_seed_ui_drift_variant_writes_screens(tmp_path):
    truth = dd_seed.seed(tmp_path, "ui_drift", 7)
    assert truth["variant"] == "ui_drift" and truth["base_variant"] == "a"
    data = json.loads((tmp_path / "harness" / "ui_drift.json").read_text())
    assert set(data["variants"]) == {"baseline", "ui_drift"}
    assert data["expected"]["revoke_ci_readonly"]["reason"] == "ambiguous"
    # the agent-visible sandbox carries no screens or truth tags
    assert not any("_truth" in p.read_text(errors="ignore")
                   for p in (tmp_path / "sandbox").rglob("*") if p.is_file())
    assert dd_seed.VARIANTS == ("a", "b", "c")
