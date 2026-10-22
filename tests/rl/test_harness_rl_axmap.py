"""Chromium CDP AX tree -> macOS-AX dict tree -> driver.read_web -> Page, on recorded fixtures
(no browser needed). Fixture: scaffold/rl/harness/fixtures/cdp_pages.json (re-record with
python -m scaffold.rl.harness.fixtures.record)."""
import json
from pathlib import Path

import pytest

from scaffold.agent.desktop.live.driver import read_web, to_micro_page
from scaffold.agent.desktop.web_tools import outline
from scaffold.rl.harness.axmap import WEB_PATH, build_tree, snapshot_from_cdp

FIX = Path(__file__).resolve().parents[2] / "scaffold/rl/harness/fixtures/cdp_pages.json"


@pytest.fixture(scope="module")
def widgets():
    d = json.loads(FIX.read_text())["widgets"]
    snap = snapshot_from_cdp(d["ax"], d["dom"], d["url"], d["title"])
    return snap, to_micro_page(snap)


def _ctl(page):
    return [(c.role, c.name, c.context, c.in_dialog) for c in page.controls]


def test_tree_shape_is_safari_like(widgets):
    snap, _ = widgets
    assert snap.tree["role"] == "AXWindow" and snap.window_title == "Widgets · Demo"
    assert snap.web_paths == [WEB_PATH] and snap.node(WEB_PATH)["role"] == "AXWebArea"
    assert snap.url.endswith("/widgets") and not snap.truncated
    roles = set()

    def walk(n):
        roles.add(n["role"])
        assert n["role"].startswith("AX")
        if n["role"] in ("AXButton", "AXCheckBox", "AXRadioButton", "AXStaticText"):
            assert not n.get("children"), n          # WebKit leaves
        for c in n.get("children") or []:
            walk(c)
    walk(snap.tree)
    assert {"AXLink", "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXPopUpButton", "AXRadioGroup",
            "AXRadioButton", "AXTable", "AXRow", "AXCell", "AXHeading", "AXStaticText", "AXGroup"} <= roles
    assert "AXRowGroup" not in roles


def test_secure_field_has_no_value(widgets):
    snap, _ = widgets
    pw = snap.find(lambda n: n.get("subrole") == "AXSecureTextField")
    assert len(pw) == 1
    node = snap.node(pw[0])
    assert node["secure"] is True and "value" not in node and node["title"] == "Password"
    assert "hunter2" not in json.dumps(snap.tree)


def test_controls_match_product_reading(widgets):
    _, p = widgets
    assert _ctl(p) == [
        ("link", "Projects", "", False), ("link", "Sign out", "", False),
        ("textbox", "Email", "Sign in", False), ("password", "Password", "Sign in", False),
        ("checkbox", "Remember me", "Sign in", False), ("combobox", "Country", "Sign in", False),
        ("textbox", "Notes", "Sign in", False), ("radiogroup", "Scope", "Sign in", False),
        ("button", "Sign in", "Sign in", False), ("textbox", "Search projects", "", False),
        ("button", "Go", "", False), ("secret_text", "New API key value", "New API key", False),
        ("button", "Create key", "Create key", True), ("button", "Cancel", "Cancel", True),
        ("button", "Revoke", "Revoke key ci-readonly", False), ("tab", "Overview", "", False),
        ("tab", "Usage", "", False), ("button", "Create key", "", False)]
    c = {x.n: x for x in p.controls}
    assert c[3].value == "ada@example.com" and c[4].value == ""
    assert c[6].options == ["India", "France", "Japan"] and c[6].value == "France"
    assert c[8].options == ["Read-only", "Read & write"] and c[8].value == "Read & write"
    assert c[1].href == "http://127.0.0.1:9/projects"
    assert c[3].form == c[4].form == c[9].form == 0 and p.forms[0].label == "Sign in"
    assert all(f.method == "post" for f in p.forms)


def test_live_regions_dialog_table_and_secret(widgets):
    snap, p = widgets
    assert p.alerts == ["Wrong email or password."] and p.statuses == ["Saved."]
    assert p.regions == ["New API key: Copy this key now. «masked 31 chars»"]
    # Safari flattens a dialog's text without its button labels (buttons are AX leaves)
    assert p.dialog == "Confirm new key This key grants write access — create?"
    assert "ci-readonly | active | " in p.text.splitlines()
    assert "fk_live" not in p.text and "fk_live" not in outline(p)
    sec = next(x for x in p.controls if x.role == "secret_text")
    assert p._raw[sec.raw_ref] == "fk_live_abcdefghijklmnop1234XYZ"


def test_hidden_not_controls_tabs_are(widgets):
    _, p = widgets
    names = [c.name for c in p.controls]
    assert "Hidden button" not in names and "Hidden link" not in names
    tabs = [c for c in p.controls if c.role == "tab"]     # WebKit tabs: AXRadioButton/AXTabButton
    assert [c.name for c in tabs] == ["Overview", "Usage"]


def test_every_control_has_a_dom_handle(widgets):
    snap, p = widgets
    for c in p.controls:
        assert isinstance(snap.element(p._live["paths"][c.n]), int)
    for n, opts in p._live["options"].items():
        for path in opts.values():
            assert isinstance(snap.element(path), int)


def test_read_web_view_and_empty_input():
    v = read_web(snapshot_from_cdp({"nodes": []}, None, "http://127.0.0.1:1/"))
    assert v.controls == [] and v.status == 0
    assert build_tree([], None, "http://127.0.0.1:1/")[0] is None


def test_not_found_title_maps_to_404():
    nodes = [{"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Not found · X"},
              "childIds": ["2"], "backendDOMNodeId": 1},
             {"nodeId": "2", "parentId": "1", "role": {"value": "StaticText"}, "name": {"value": "Page not found."},
              "childIds": [], "backendDOMNodeId": 2}]
    p = to_micro_page(snapshot_from_cdp({"nodes": nodes}, None, "http://127.0.0.1:1/x", "Not found · X"))
    assert p.status == 404 and p.text == "Page not found."
