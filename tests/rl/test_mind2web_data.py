"""mind2web_data: Mind2Web-shaped trajectories -> SFT rows (docs/specs/web_data.md).

A hand-made fixture of 2 tasks x 3 steps with local HTML in Mind2Web's dump format
(backend_node_id / bounding_box_rect on every element, data_pw_testid_buckeye on the target, aria_*
attributes, <a> without href, self-closing empty elements, an unrendered dialog). No network: the
Chromium tests need a cached Chromium build and are skipped without one.
"""
import json

import pytest

from scaffold.rl.train import mind2web_data as M
from scaffold.rl.train.chat import render_completion, render_prompt
from scaffold.rl.train.dataset import EvalSplitError, ExtraRows, build as build_dataset, validate_row

_ids = iter(range(1000, 100000))


def el(tag, inner="", rect="10,10,100,20", **attrs):
    """One element in the dump's format; ``rect=None`` drops bounding_box_rect."""
    a = {"backend_node_id": str(next(_ids))}
    if rect is not None:
        a["bounding_box_rect"] = rect
    a.update({k.rstrip("_"): v for k, v in attrs.items()})
    s = " ".join(f'{k}="{v}"' for k, v in a.items())
    return f"<{tag} {s}>{inner}</{tag}>"


def page(title, *body):
    return (f'<html backend_node_id="1" bounding_box_rect="0,0,1280,1080"><head><title>{title}</title></head>'
            f'<body backend_node_id="2" bounding_box_rect="0,0,1280,1080">{"".join(body)}</body></html>')


def action(uid, html, op="CLICK", value="", original=None, cand=None):
    return {"action_uid": uid, "raw_html": html, "cleaned_html": "",
            "operation": {"op": op, "value": value, "original_op": original or op},
            "pos_candidates": [{"tag": "x", "backend_node_id": cand, "is_original_target": True,
                                "is_top_level_target": True, "attributes": "{}"}] if cand else [],
            "neg_candidates": []}


def shop_page(uid, typed=""):
    return page(
        "Towel Shop",
        el("h1", "Towel Shop"),
        el("form", el("input", type="text", aria_label="Search products", input_value=typed,
                      data_pw_testid_buckeye=uid).replace("></input>", "/>")
           + el("button", "Search", type="submit")),
        el("a", "Deals"),
        el("div", el("button", "Close sign-in"), rect="-1,-1,-1,-1", role="dialog", aria_label="Sign in"),
        el("img", src="https://example.com/logo.png").replace("></img>", "/>"),
        '<link rel="stylesheet" href="https://example.com/site.css">')


def results_page(uid, semantic=True):
    target = (el("a", el("span", "Blue Towel Set", data_pw_testid_buckeye=uid)) if semantic
              else el("div", "Blue Towel Set", data_pw_testid_buckeye=uid, class_="tile"))
    return page(
        "Results",
        el("h1", "2 results"),
        target,
        '<span backend_node_id="77" bounding_box_rect="0,0,8,8" class="icon"/>',  # self-closing, non-void
        el("a", "Green Towel"),
        el("div", el("button", "Accept cookies"), rect="0,1000,1280,80", role="alertdialog",
           aria_label="Cookies"))


H = "-1,-1,-1,-1"                     # Mind2Web's rect for an element that was not rendered


def travel_page(uid, which, cabin="", dest=""):
    def mark(name):
        return {"data_pw_testid_buckeye": uid} if name == which else {}
    opts = "".join(el("option", o, rect="-1,-1,-1,-1", **({"selected": "selected"} if o == cabin else {}))
                   for o in ("Business", "Economy"))
    return page(
        "Fly Away",
        el("nav", el("a", "Flights", **mark("flights"))
           + el("ul", el("li", el("a", "Deals", rect=H), rect=H), rect=H)),    # a closed menu
        el("form", el("select", opts, aria_label="Cabin", **mark("cabin"))
           + el("input", type="text", aria_label="Destination", input_value=dest, **mark("dest"))
           + el("button", "Search flights")))


def fixture_tasks(semantic=True):
    return [
        {"website": "towelshop", "domain": "Shopping", "subdomain": "Home", "annotation_id": "task-a",
         "confirmed_task": "Search for blue towels and open the first result.",
         "action_reprs": ["[textbox] Search -> TYPE: blue towels", "[textbox] Search -> ENTER",
                          "[span] Blue Towel Set -> CLICK"],
         "actions": [action("a1", shop_page("a1"), "TYPE", "blue towels"),
                     action("a2", shop_page("a2", "blue towels"), "CLICK", original="ENTER"),
                     action("a3", results_page("a3", semantic))]},
        {"website": "flyaway", "domain": "Travel", "subdomain": "Airlines", "annotation_id": "task-b",
         "confirmed_task": "Set the cabin to Economy and the destination to Paris.",
         "action_reprs": ["[link] Flights -> HOVER", "[combobox] Cabin -> SELECT: Economy",
                          "[textbox] Destination -> TYPE: Paris"],
         "actions": [action("b1", travel_page("b1", "flights"), "CLICK", original="HOVER"),
                     action("b2", travel_page("b2", "cabin"), "SELECT", "Economy"),
                     action("b3", travel_page("b3", "dest", cabin="Economy"), "TYPE", "Paris")]},
    ]


def write_tasks(path, tasks):
    path.write_text(json.dumps(tasks))
    return path


BENCH = [{"task_id": "om1", "confirmed_task": "Find the cheapest flight from Boston to Denver on Ryanair.",
          "website": "https://www.ryanair.com/", "reference_length": 6, "level": "medium"},
         {"task_id": "om2", "confirmed_task": "Browse comedy movies on IMDb sorted by rating.",
          "website": "https://www.flyaway.com/", "reference_length": 4, "level": "easy"}]


def bench_file(tmp_path, rows=BENCH, jsonl=False):
    p = tmp_path / ("bench.jsonl" if jsonl else "bench.json")
    p.write_text("\n".join(json.dumps(r) for r in rows) if jsonl else json.dumps(rows))
    return p


# ------------------------------------------------------------ no browser --

def test_iter_tasks_streams_and_drops_a_torn_tail(tmp_path):
    p = write_tasks(tmp_path / "train_0.json", fixture_tasks())
    got = list(M.iter_tasks(p, chunk=4096))
    assert [t["annotation_id"] for t in got] == ["task-a", "task-b"]
    assert got[1]["actions"][2]["operation"]["value"] == "Paris"
    raw = p.read_bytes()
    cut = tmp_path / "train_0.part.json"
    cut.write_bytes(raw[: raw.index(b'{"website": "flyaway"') + 500])       # a ranged download
    assert [t["annotation_id"] for t in M.iter_tasks(cut, chunk=1000)] == ["task-a"]


def test_repair_html_closes_non_void_self_closing_tags():
    h = '<div a="1"><span class="x"/><a>t</a><input type="text"/><img src="y"/><br/></div>'
    assert M.repair_html(h) == '<div a="1"><span class="x"></span><a>t</a><input type="text"/><img src="y"/><br/></div>'


def test_site_keys_and_brands():
    assert M.site_key("https://www.united.com/en/us") == "united"
    assert M.site_key("united") == "united"
    assert M.site_key("sports.yahoo") == "sports.yahoo"
    assert M.site_key("https://finance.yahoo.com/") == "finance.yahoo"
    assert M.site_key("https://www.bbc.co.uk/") == "bbc"
    assert M.brand("sports.yahoo") == M.brand("https://finance.yahoo.com/") == "yahoo"


def test_exclusion_fuzzy_match_and_website_overlap(tmp_path):
    ex = M.Exclusion(M.load_benchmark([bench_file(tmp_path)]))
    assert ex.match("Find the cheapest flight from Boston to Denver on Ryanair") is not None   # punctuation
    assert ex.match("find cheapest flights from boston to denver on ryanair.") is not None      # small edits
    assert ex.match("Search for blue towels and open the first result.") is None
    assert ex.website_overlap("flyaway") == "https://www.flyaway.com/"
    assert ex.website_overlap("towelshop") is None
    jl = M.load_benchmark([bench_file(tmp_path, [{"task_id": "z", "task_description": "Rent a bike in Paris",
                                                   "website": "https://bikes.example/"}], jsonl=True)])
    assert jl[0].text == "Rent a bike in Paris"
    with pytest.raises(ValueError):
        M.Exclusion([])


def test_check_row_refuses_benchmark_tasks(tmp_path):
    ex = M.Exclusion(M.load_benchmark([bench_file(tmp_path)]))
    row = {"meta": {"task_id": "m2w:x", "split": "train",
                    "task_text": "Find the cheapest flight from Boston to Denver on Ryanair!"}}
    with pytest.raises(M.ContaminationError):
        M.check_row(row, ex)
    assert issubclass(M.ContaminationError, EvalSplitError)
    row["meta"]["task_text"] = "Search for blue towels."
    M.check_row(row, ex)
    row["meta"]["split"] = "test"
    with pytest.raises(EvalSplitError):
        M.check_row(row, ex)


def test_cli_requires_an_exclusion_list(tmp_path, capsys):
    p = write_tasks(tmp_path / "t.json", fixture_tasks())
    with pytest.raises(SystemExit) as e:
        M.main(["--input", str(p), "--out", str(tmp_path / "o.jsonl")])
    assert e.value.code == 2
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    assert M.main(["--input", str(p), "--exclude", str(empty), "--out", str(tmp_path / "o.jsonl")]) == 2
    assert "REFUSING" in capsys.readouterr().err
    assert not (tmp_path / "o.jsonl").exists()


def test_report_only_needs_no_html(tmp_path):
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text("\n".join(json.dumps(r) for r in [
        {"annotation_id": "1", "confirmed_task": "Find the cheapest flight from Boston to Denver on Ryanair",
         "website": "ryanair"},
        {"annotation_id": "2", "confirmed_task": "Book a table for two", "website": "opentable"}]))
    out = tmp_path / "rep.json"
    assert M.main(["--report-only", "--input", str(tasks), "--exclude", str(bench_file(tmp_path)),
                   "--out", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["tasks"] == 2 and [m["task_id"] for m in rep["text_matches"]] == ["m2w:1"]
    assert rep["website_overlap"] == {"ryanair -> https://www.ryanair.com/": 1}


def test_scrolls_follow_the_product_window():
    assert M.scrolls_to(0, 10, 100) == []
    assert M.scrolls_to(0, 45, 100) == ["scroll(down)"]
    assert M.scrolls_to(0, 99, 100) == ["scroll(down)", "scroll(down)"]
    assert M.scrolls_to(80, 5, 100) == ["scroll(up)", "scroll(up)"]
    assert M.scroll_window(40, True, 70) == 30                     # MicroAgent clamps at len - WINDOW
    assert M.q('say "hi" \\ é') == '"say \\"hi\\" \\\\ é"'           # grammar str: no \u escapes


# --------------------------------------------------------------- Chromium --

def _chromium_or_skip():
    pytest.importorskip("playwright")
    from scaffold.rl.harness.chromium import find_chromium
    if not find_chromium():
        pytest.skip("no cached Chromium build (set AWOS_RL_CHROMIUM)")


@pytest.fixture(scope="module")
def browser():
    _chromium_or_skip()
    with M.SnapshotBrowser() as br:
        yield br


def _convert(browser, tasks, **kw):
    agent = M.make_agent()
    return [M.convert_task(t, browser, agent=agent, **kw) for t in tasks]


def test_fixture_converts_to_product_rows(browser):
    a, b = _convert(browser, fixture_tasks())
    assert [r["messages"][2]["content"] for r in a.rows] == ['fill(1, "blue towels")', "click(2)", "click(1)"]
    assert [r["meta"]["map"] for r in a.rows] == ["self", "submit", "ancestor"]
    assert (a.steps, a.mapped, a.noop, a.stopped, a.done_row) == (3, 3, 0, "", False)
    assert a.reasons["no_final_page"] == 1                         # the last click has no page after it
    assert [r["messages"][2]["content"] for r in b.rows] == ['select(2, "Economy")', 'fill(3, "Paris")',
                                                             'done("Completed: Set the cabin to Economy and the '
                                                             'destination to Paris.")']
    assert (b.mapped, b.noop, b.reasons["noop:hover"], b.done_row) == (2, 1, 1, True)

    u1, u2, u3 = (r["messages"][1]["content"] for r in a.rows)
    assert u1.startswith("Goal: Search for blue towels and open the first result.\nStep 1 of 12.")
    assert "Your previous actions" not in u1
    assert '[1] textbox "Search products"' in u1 and '[2] button "Search"' in u1 and '[3] link "Deals"' in u1
    assert "Close sign-in" not in u1                               # the unrendered dialog is hidden
    assert '1. fill(1, "blue towels") -> ok; now on "Towel Shop"' in u2
    assert '= "blue towels"' in u2                                   # the typed value stays shown
    assert '2. click(2) -> ok; now on "Results"' in u3 and 'Pages so far: "Towel Shop" -> "Results"' in u3
    assert '[1] link "Blue Towel Set"' in u3 and '[2] link "Green Towel"' in u3   # <span/> did not swallow it
    assert "Accept cookies" not in u3                              # a dialog the annotator acted outside of
    assert "1. select(2, \"Economy\") -> ok" in b.rows[1]["messages"][1]["content"]
    assert '= "Paris"' in b.rows[2]["messages"][1]["content"]       # the done row sees the filled value
    for r in a.rows + b.rows:
        system, user, reply, meta = validate_row(r, "fixture")      # dataset.py's --extra-rows contract
        assert r["prompt"] == render_prompt(system, user) and r["completion"] == render_completion(reply)
        assert meta["split"] == "train" and meta["task_source"] == "mind2web"
    assert browser.blocked >= 1                                    # the stylesheet / image never left the page


def test_unmapped_step_stops_rows_and_is_counted(browser):
    (a, _b) = _convert(browser, fixture_tasks(semantic=False))
    assert a.stopped == "target_not_semantic" and a.reasons["target_not_semantic"] == 1
    assert len(a.rows) == 2 and a.mapped == 2
    (c, _d) = _convert(browser, fixture_tasks(semantic=False), after_skip="continue")
    assert len(c.rows) == 2 and c.stopped == ""


def test_far_target_gets_scroll_rows(browser):
    links = "".join(el("a", f"Item {i}", **({"data_pw_testid_buckeye": "s1"} if i == 44 else {}))
                    for i in range(1, 51))
    task = {"website": "list", "annotation_id": "task-s", "confirmed_task": "Open item 44.",
            "actions": [action("s1", page("Items", links))]}
    (res,) = _convert(browser, [task])
    assert [r["messages"][2]["content"] for r in res.rows] == ["scroll(down)", "click(44)"]
    assert "showing 41-50" in res.rows[1]["messages"][1]["content"]
    assert '1. scroll(down) -> ok; now on "Items"' in res.rows[1]["messages"][1]["content"]


def test_label_click_maps_to_its_checkbox(browser):
    body = (el("input", type="checkbox", id="kids", aria_label="Kids").replace("></input>", "/>")
            + el("label", "Kids", for_="kids", data_pw_testid_buckeye="l1"))
    task = {"website": "x", "annotation_id": "task-l", "confirmed_task": "Tick Kids.",
            "actions": [action("l1", page("Filters", body))]}
    (res,) = _convert(browser, [task])
    assert [r["messages"][2]["content"] for r in res.rows] == ["click(1)"] and res.rows[0]["meta"]["map"] == "label"


def test_build_excludes_benchmark_tasks_and_feeds_dataset(tmp_path, browser):
    p = write_tasks(tmp_path / "train_x.json", fixture_tasks())
    bench = BENCH + [{"task_id": "om3", "confirmed_task": "Search for blue towels and open the first result",
                      "website": "https://www.towelshop.com/"}]
    ex = M.Exclusion(M.load_benchmark([bench_file(tmp_path, bench)]))
    out = tmp_path / "rows.jsonl"
    stats = M.build([p], ex, out, chromium=browser.chromium, log=lambda *_: None)
    assert stats["tasks_in"] == 2 and stats["tasks_excluded_text"] == 1 and stats["tasks_converted"] == 1
    assert stats["excluded"][0]["om2w_task_id"] == "om3"
    assert stats["website_overlap"] == {"flyaway -> https://www.flyaway.com/": 1}
    assert stats["rows"] == 3 and stats["mapping_rate"] == 1.0 and stats["done_rows"] == 1
    rows = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert {r["meta"]["task_id"] for r in rows} == {"m2w:task-b"}
    assert rows[0]["meta"]["om2w_website_overlap"] == "https://www.flyaway.com/"
    train, valid, st = build_dataset([], val_frac=0.0, extra=[ExtraRows("m2w", out)])
    assert len(train) == 3 and st["train_by"]["task_source"] == {"mind2web": 3}
