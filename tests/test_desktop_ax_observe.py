"""D16 perception: stable IDs, diff-only-when-shorter, no_change, range notes,
button roles (scaffold/agent/desktop/ax_observe.py, ax_compress.py)."""
import copy

import pytest

from scaffold.agent.desktop.ax_compress import button_role, collect, compress, render
from scaffold.agent.desktop.ax_observe import Observer

VIEWPORT = (0, 0, 1440, 900)


def node(role, title="", frame=(0, 0, 10, 10), children=(), **kw):
    return {"role": role, "title": title,
            "frame": {"x": frame[0], "y": frame[1], "w": frame[2], "h": frame[3]},
            "children": list(children), **kw}


def mail_tree(n_msgs=8, draft="", sheet=False, extra_button=False, wrap=False):
    msgs = [node("AXRow", "", (0, 100 + 30 * i, 400, 30),
                 [node("AXStaticText", f"Message {i}", (5, 105 + 30 * i, 200, 20))])
            for i in range(n_msgs)]
    toolbar = [node("AXButton", "New", (10, 8, 40, 24)),
               node("AXButton", "Delete", (60, 8, 50, 24))]
    if extra_button:
        toolbar.insert(0, node("AXButton", "Archive", (120, 8, 60, 24)))
    compose = node("AXGroup", "", (450, 60, 500, 400), [
        node("AXTextField", "To", (460, 70, 300, 24), value="bob@x.test"),
        node("AXTextArea", "", (460, 110, 480, 200), value=draft, focused=True),
        node("AXButton", "Send", (860, 330, 60, 24)),
    ])
    if wrap:
        compose = node("AXGroup", "", (450, 60, 500, 400), [compose])
    kids = [node("AXToolbar", "", (0, 0, 800, 40), toolbar),
            node("AXScrollArea", "", (0, 60, 420, 600), [node("AXTable", "Inbox", (0, 60, 420, 30 * n_msgs + 60), msgs)]),
            compose]
    if sheet:
        kids.append(node("AXSheet", "", (300, 200, 400, 150), [
            node("AXStaticText", "Delete this message?", (320, 220, 300, 20)),
            node("AXButton", "Cancel", (420, 300, 80, 24)),
            node("AXButton", "Delete", (520, 300, 80, 24), default=True),
        ]))
    return node("AXApplication", "Mail", (0, 0, 1200, 800),
                [node("AXWindow", "Inbox", (0, 0, 1200, 800), kids)])


def sid_of(obs, text, role=None):
    return next(e.sid for e in obs.elements if e.text == text and (role is None or e.role == role))


# -- stable ids ---------------------------------------------------------------

def test_first_observation_is_full_with_stable_ids():
    o = Observer(VIEWPORT)
    m = o.observe(mail_tree())
    assert m.kind == "full" and m.seq == 1
    assert m.text.startswith('obs 1: full') and 'app "Mail"' in m.text
    assert all(e.sid.startswith("e") for e in m.elements)
    assert len({e.sid for e in m.elements}) == len(m.elements)
    assert f'[{sid_of(m, "Send")}] button "Send"' in m.text


def test_ids_survive_insertions_elsewhere():
    o = Observer(VIEWPORT)
    a = o.observe(mail_tree())
    b = o.observe(mail_tree(extra_button=True))
    for t in ("New", "Delete", "Send", "Message 3", "To"):
        assert sid_of(a, t) == sid_of(b, t), t
    assert sid_of(b, "Archive") not in {e.sid for e in a.elements}


def test_typing_into_a_field_keeps_its_id():
    o = Observer(VIEWPORT)
    a = o.observe(mail_tree(draft=""))
    b = o.observe(mail_tree(draft="Hello Bob"))
    ta = next(e for e in a.elements if e.role == "textarea")
    tb = next(e for e in b.elements if e.role == "textarea")
    assert ta.sid == tb.sid
    assert b.kind == "diff" and b.changed == [tb.sid] and not b.added and not b.removed


def test_unlabelled_wrapper_does_not_renumber():
    o = Observer(VIEWPORT)
    a = o.observe(mail_tree())
    b = o.observe(mail_tree(wrap=True))
    assert sid_of(a, "Send") == sid_of(b, "Send")
    assert b.kind == "no_change"


def test_ax_identifier_wins_over_label():
    o = Observer()
    a = o.observe(node("AXWindow", "W", children=[node("AXButton", "Play", identifier="playpause")]))
    b = o.observe(node("AXWindow", "W", children=[node("AXButton", "Pause", identifier="playpause")]))
    assert a.elements[-1].sid == b.elements[-1].sid
    assert b.changed == [b.elements[-1].sid]


def test_ids_are_never_reused():
    o = Observer(VIEWPORT)
    a = o.observe(mail_tree(extra_button=True))
    archived = sid_of(a, "Archive")
    o.observe(mail_tree())
    c = o.observe(node("AXApplication", "Mail", children=[node("AXButton", "Totally new")]))
    assert archived not in {e.sid for e in c.elements}


def test_same_label_siblings_get_distinct_ids():
    # (identical siblings under one parent collapse into one "x2" element, so
    # the two buttons live in different unlabelled groups)
    tree = node("AXWindow", "W", children=[
        node("AXGroup", "", children=[node("AXButton", "Edit", (0, 0, 10, 10))]),
        node("AXGroup", "", children=[node("AXButton", "Edit", (0, 50, 10, 10))])])
    m = Observer().observe(tree)
    assert len({e.sid for e in m.elements if e.text == "Edit"}) == 2


# -- diff / full / no_change ----------------------------------------------------

def test_no_change_message():
    o = Observer(VIEWPORT)
    o.observe(mail_tree())
    m = o.observe(mail_tree())
    assert m.kind == "no_change"
    assert m.text == f"obs 2: no_change since obs 1 ({len(m.elements)} elements)"
    assert m.full_text.startswith("obs 2: full")


def test_small_change_sends_a_diff_shorter_than_full():
    o = Observer(VIEWPORT)
    o.observe(mail_tree())
    m = o.observe(mail_tree(sheet=True))
    assert m.kind == "diff"
    assert len(m.text) < len(m.full_text)
    assert m.text.startswith("obs 2: diff vs obs 1 (+")
    assert any(l.startswith("+ ") and "Delete this message?" in l for l in m.text.splitlines())


def test_removed_elements_are_listed_by_id():
    o = Observer(VIEWPORT)
    a = o.observe(mail_tree(sheet=True))
    m = o.observe(mail_tree())
    gone = {e.sid for e in a.elements} - {e.sid for e in m.elements}
    assert set(m.removed) == gone and gone
    for s in gone:
        assert any(l.startswith(f"- [{s}]") for l in m.text.splitlines())


def test_big_change_falls_back_to_full():
    o = Observer(VIEWPORT)
    o.observe(mail_tree())
    other = node("AXApplication", "Mail", (0, 0, 1200, 800), [node("AXWindow", "Prefs", (0, 0, 600, 400), [
        node("AXButton", f"Option {i}", (10, 10 + 30 * i, 100, 24)) for i in range(5)])])
    m = o.observe(other)
    assert m.kind == "full" and m.text == m.full_text


def test_app_switch_is_always_full():
    o = Observer(VIEWPORT)
    o.observe(mail_tree())
    t = mail_tree()
    t["title"] = "Notes"
    m = o.observe(t)
    assert m.kind == "full" and 'app "Notes"' in m.text


def test_order_only_change_is_full_not_empty_diff():
    a = node("AXWindow", "W", children=[node("AXButton", "A", (0, 0, 10, 10)),
                                        node("AXButton", "B", (50, 0, 10, 10))])
    b = node("AXWindow", "W", children=[node("AXButton", "A", (50, 0, 10, 10)),
                                        node("AXButton", "B", (0, 0, 10, 10))])
    o = Observer()
    o.observe(a)
    m = o.observe(b)
    # centres moved too, so this is a diff of two changed lines, never no_change
    assert m.kind in ("diff", "full") and m.kind != "no_change"


# -- visible range notes ----------------------------------------------------------

def test_scroll_area_clips_rows_and_reports_range():
    o = Observer(VIEWPORT)
    m = o.observe(mail_tree(n_msgs=40))       # scroll area shows y 60..660
    texts = [e.text for e in m.elements]
    assert "Message 0" in texts and "Message 39" not in texts
    assert any("table \"Inbox\": showing 1–19 of 40" in n for n in m.notes), m.notes
    assert 'note: table "Inbox": showing 1–19 of 40' in m.text


def test_lazy_table_total_and_offset():
    rows = [node("AXRow", f"r{i}", (0, 20 * i, 100, 20)) for i in range(10)]
    tree = node("AXTable", "Contacts", (0, 0, 100, 200), rows, total=500, offset=100)
    _, notes = collect(tree)
    assert notes == ['table "Contacts": showing 101–110 of 500']


def test_no_note_when_all_rows_visible():
    _, notes = collect(mail_tree(n_msgs=3), VIEWPORT)
    assert notes == []


def test_element_cap_note():
    tree = node("AXWindow", "W", (0, 0, 1000, 1000),
                [node("AXButton", f"B{i}", (0, 10 * i, 50, 8)) for i in range(20)])
    m = Observer(max_elements=5).observe(tree)
    assert len(m.elements) == 5
    assert m.notes[0] == "showing 1–5 of 20 elements"
    assert m.text.splitlines()[1] == "note: showing 1–5 of 20 elements"


def test_range_note_change_appears_in_diff():
    o = Observer(VIEWPORT)
    o.observe(mail_tree(n_msgs=40))
    t = mail_tree(n_msgs=40)
    table = t["children"][0]["children"][1]["children"][0]
    for r in table["children"]:                          # scroll by 3 rows
        r["frame"]["y"] -= 90
        for c in r["children"]:
            c["frame"]["y"] -= 90
    m = o.observe(t)
    assert m.notes == ['table "Inbox": showing 2–22 of 40']
    assert m.kind == "diff"
    assert 'note: table "Inbox": showing 2–22 of 40' in m.text


# -- button roles -------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Delete", "Remove", "Erase", "Discard", "Don't Save", "Don’t Save", "Empty Trash",
    "Format", "Sign Out", "Log Out", "Send", "Move to Trash", "Delete Account…",
    "Permanently Delete", "Uninstall", "Replace", "Cancel Subscription", "Submit", "Post",
    "Buy Now", "Erase Disk...",
])
def test_destructive_titles(title):
    assert button_role({"role": "AXButton", "title": title}) == "destructive"


@pytest.mark.parametrize("title, role", [
    ("Cancel", "cancel"), ("Close", "cancel"), ("Not Now", "cancel"),
    ("OK", "primary"), ("Save", "primary"), ("Continue", "primary"),
    ("Bold", "normal"), ("Back", "cancel"), ("Send to Back", "normal"),
])
def test_other_roles(title, role):
    assert button_role({"role": "AXButton", "title": title}) == role


def test_subrole_and_flags():
    assert button_role({"role": "AXButton", "title": "Go", "subrole": "AXDefaultButton"}) == "primary"
    assert button_role({"role": "AXButton", "title": "Go", "default": True}) == "primary"
    assert button_role({"role": "AXButton", "title": "x", "subrole": "AXCloseButton"}) == "cancel"
    assert button_role({"role": "AXButton", "title": "Go", "button_role": "destructive"}) == "destructive"


def test_explicit_role_cannot_downgrade_heuristic():
    assert button_role({"role": "AXButton", "title": "Delete All", "button_role": "normal"}) == "destructive"


def test_icon_button_named_by_description():
    assert button_role({"role": "AXButton", "description": "Delete"}) == "destructive"


def test_dom_submit_button():
    assert button_role({"tag": "button", "name": "Go", "type": "submit"}) == "destructive"
    assert button_role({"tag": "input", "type": "submit"}) == "destructive"


def test_non_clickables_and_menu_titles():
    assert button_role({"role": "AXStaticText", "value": "Delete"}) is None
    assert button_role({"role": "AXTextField", "title": "Delete reason"}) is None
    assert button_role({"role": "AXMenuButton", "title": "Format"}) == "normal"
    assert button_role({"role": "AXMenuItem", "title": "Delete"}) == "destructive"


def test_roles_rendered_and_default_flag():
    els = compress(mail_tree(sheet=True), VIEWPORT)
    out = render(els)
    assert '"Delete" @(560,312) MODAL destructive' in out
    assert '"Cancel" @(460,312) MODAL cancel' in out
    d = next(e for e in els if e.text == "Delete" and e.modal)
    assert d.is_default and d.button_role == "destructive"
    assert Observer(VIEWPORT).observe(mail_tree(sheet=True)).default_button.text == "Delete"
