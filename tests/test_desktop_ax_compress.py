"""AX / DOM tree compression on a synthetic macOS-style tree."""
from scaffold.agent.desktop.ax_compress import (
    compress, compression_ratio, diff, no_progress, norm_role, render,
)


def node(role, title="", frame=(0, 0, 10, 10), children=(), **kw):
    return {"role": role, "title": title,
            "frame": {"x": frame[0], "y": frame[1], "w": frame[2], "h": frame[3]},
            "children": list(children), **kw}


def synthetic_tree(with_sheet=False):
    rows = [node("AXRow", "", (0, 100 + 20 * i, 400, 20),
                 [node("AXCell", "", (0, 100 + 20 * i, 400, 20),
                       [node("AXStaticText", f"file_{i}.txt", (5, 102 + 20 * i, 100, 16))])])
            for i in range(5)]
    toolbar = node("AXToolbar", "", (0, 0, 800, 40), [
        node("AXButton", "Back", (10, 8, 30, 24)),
        node("AXButton", "Forward", (45, 8, 30, 24), enabled=False),
        node("AXSearchField", "Search", (600, 8, 180, 24), focused=True),
        node("AXGroup", "", (0, 0, 800, 40), [node("AXGroup", "", (0, 0, 800, 40))]),  # empty wrappers
    ])
    filler = [node("AXGroup", "", (0, 0, 800, 600), [node("AXGroup", "", (0, 0, 800, 600))])
              for _ in range(20)]
    hidden = [node("AXButton", "Hidden", (0, 0, 10, 10), hidden=True),
              node("AXButton", "ZeroSize", (0, 0, 0, 0)),
              node("AXButton", "Offscreen", (5000, 5000, 10, 10))]
    dup_icons = [node("AXImage", "spacer", (300, 300, 4, 4)) for _ in range(6)]
    kids = [toolbar, node("AXScrollArea", "", (0, 60, 800, 500),
                          [node("AXOutline", "", (0, 60, 800, 500), rows)])] + filler + hidden + \
        [node("AXGroup", "", (290, 290, 40, 40), dup_icons)]
    if with_sheet:
        kids.append(node("AXSheet", "", (200, 150, 400, 200), [
            node("AXStaticText", "Delete 5 items?", (220, 170, 300, 20)),
            node("AXButton", "Cancel", (300, 300, 80, 24)),
            node("AXButton", "Delete", (400, 300, 80, 24)),
        ]))
    return node("AXApplication", "Finder", (0, 0, 800, 600),
                [node("AXWindow", "Documents", (0, 0, 800, 600), kids)])


VIEWPORT = (0, 0, 1440, 900)


def test_role_normalisation():
    assert norm_role("AXButton") == "button"
    assert norm_role("search_field") == "searchfield"


def test_drops_invisible_and_empty():
    out = render(compress(synthetic_tree(), VIEWPORT))
    for gone in ("Hidden", "ZeroSize", "Offscreen", "group"):
        assert gone not in out
    assert '"Back"' in out and '"Search"' in out


def test_dedup_counts_repeats():
    els = compress(synthetic_tree(), VIEWPORT)
    spacer = [e for e in els if e.text == "spacer"]
    assert len(spacer) == 1 and spacer[0].repeat == 6


def test_reading_order_indices_and_centres():
    els = compress(synthetic_tree(), VIEWPORT)
    assert [e.index for e in els] == list(range(len(els)))
    back = next(e for e in els if e.text == "Back")
    assert back.center == (25, 20)
    ys = [e.center[1] for e in els if e.center]
    assert ys == sorted(ys) or all(abs(a - b) < 10 or a <= b for a, b in zip(ys, ys[1:]))
    files = [e.text for e in els if e.text.startswith("file_")]
    assert files == [f"file_{i}.txt" for i in range(5)]


def test_flags_rendered():
    out = render(compress(synthetic_tree(), VIEWPORT))
    assert '"Forward"' in out and "disabled" in out.split('"Forward"')[1].split("\n")[0]
    assert "focused" in out


def test_modal_sheet_first():
    els = compress(synthetic_tree(with_sheet=True), VIEWPORT)
    assert els[0].modal and els[0].role == "sheet"
    assert {e.text for e in els[:4]} >= {"Delete 5 items?", "Cancel", "Delete"}
    assert "MODAL" in render(els[:1])


def test_compression_ratio_under_quarter():
    tree = synthetic_tree()
    ratio = compression_ratio(tree, compress(tree, VIEWPORT))
    assert ratio < 0.25, ratio  # A11y-Compressor reports ~22% of raw tokens


def test_diff_and_no_progress():
    a = compress(synthetic_tree(), VIEWPORT)
    b = compress(synthetic_tree(), VIEWPORT)
    assert no_progress(a, b)
    c = compress(synthetic_tree(with_sheet=True), VIEWPORT)
    d = diff(a, c)
    assert not no_progress(a, c) and any("Delete 5 items?" in s for s in d["added"])


def test_dom_style_input():
    dom = {"tag": "body", "children": [
        {"tag": "a", "name": "Home", "bounds": [0, 0, 50, 20]},
        {"tag": "div", "children": [{"tag": "input", "placeholder": "Email", "bounds": [0, 40, 200, 30]}]},
        {"tag": "div", "visible": False, "children": [{"tag": "a", "name": "Secret"}]},
    ]}
    out = render(compress(dom))
    assert '[0] a "Home"' in out and '"Email"' in out and "Secret" not in out
