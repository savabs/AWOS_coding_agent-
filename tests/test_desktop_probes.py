"""Probes: getters, comparators, the Probe spec, and the V1 validity rule."""
import json
import plistlib
import sqlite3
import sys

import pytest

from scaffold.agent.desktop.probes import (
    COMPARATORS, GETTERS, Probe, ProbeSet, load_probe, validate_probe,
)


def probe(getter, op, expected=None, **cmp):
    c = {"op": op, **cmp}
    if expected is not None:
        c["expected"] = expected
    return Probe("p", getter, c)


def test_file_getters(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    assert probe({"type": "file_exists", "path": "a.txt"}, "equals", True).holds(tmp_path)
    assert not probe({"type": "file_exists", "path": "b.txt"}, "equals", True).holds(tmp_path)
    assert probe({"type": "file_content", "path": "a.txt"}, "equals", "hello").holds(tmp_path)
    h = probe({"type": "file_hash", "path": "a.txt"}, "regex", "^[0-9a-f]{64}$")
    assert h.holds(tmp_path)


def test_missing_file_fails_never_passes(tmp_path):
    # not_equals would pass on None if getter errors were swallowed as values
    r = probe({"type": "file_content", "path": "nope.txt"}, "not_equals", "x").evaluate(tmp_path)
    assert not r.passed and r.error.startswith("getter")


def test_structured_getters(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"rows": [{"n": 1}, {"n": 2.5}]}))
    assert probe({"type": "json_value", "path": "d.json", "key": "rows.1.n"},
                 "approx", 2.5, tolerance=0.01).holds(tmp_path)
    with open(tmp_path / "p.plist", "wb") as f:
        plistlib.dump({"A": {"B": True}}, f)
    assert probe({"type": "plist_value", "path": "p.plist", "key": "A.B"}, "equals", True).holds(tmp_path)
    (tmp_path / "c.ini").write_text("[s]\nk = v\n")
    assert probe({"type": "ini_value", "path": "c.ini", "section": "s", "key": "k"},
                 "equals", "v").holds(tmp_path)
    con = sqlite3.connect(tmp_path / "x.db")
    con.execute("create table t(a)")
    con.executemany("insert into t values (?)", [(1,), (2,)])
    con.commit()
    con.close()
    assert probe({"type": "sqlite_query", "path": "x.db", "sql": "select sum(a) from t"},
                 "equals", 3).holds(tmp_path)
    assert probe({"type": "sqlite_query", "path": "x.db", "sql": "select a from t", "mode": "rows"},
                 "set_equals", [[2], [1]]).holds(tmp_path)


def test_sqlite_probe_is_read_only(tmp_path):
    con = sqlite3.connect(tmp_path / "x.db")
    con.execute("create table t(a)")
    con.commit()
    con.close()
    r = probe({"type": "sqlite_query", "path": "x.db", "sql": "insert into t values (1)"},
              "truthy").evaluate(tmp_path)
    assert not r.passed and "readonly" in (r.error or "").replace(" ", "").lower()


def test_html_field_getter(tmp_path):
    (tmp_path / "f.html").write_text(
        '<input id="a" value="x"><input name="c" type="checkbox" checked>'
        '<select id="s"><option value="1">1</option><option value="2" selected>2</option></select>'
        '<textarea id="t">hi</textarea>')
    g = lambda f: {"type": "html_field", "path": "f.html", "field": f}
    assert probe(g("a"), "equals", "x").holds(tmp_path)
    assert probe(g("c"), "equals", True).holds(tmp_path)
    assert probe(g("s"), "equals", "2").holds(tmp_path)
    assert probe(g("t"), "equals", "hi").holds(tmp_path)


def test_glob_and_comparators(tmp_path):
    for n in ("a.pdf", "b.pdf", "c.txt"):
        (tmp_path / n).write_text("")
    g = {"type": "glob", "pattern": "*.pdf"}
    assert probe(g, "set_equals", ["b.pdf", "a.pdf"]).holds(tmp_path)
    assert probe(g, "length", 2).holds(tmp_path)
    assert probe(g, "contains", "a.pdf").holds(tmp_path)
    assert COMPARATORS["regex"]("Total: 42", {"expected": r"\d+"})
    assert not COMPARATORS["regex"](42, {"expected": r"\d+"})
    assert COMPARATORS["glob_match"]("shot_1.png", {"expected": "shot_*.png"})


def test_unknown_types_rejected():
    with pytest.raises(ValueError):
        Probe("x", {"type": "llm_judge"}, {"op": "equals", "expected": 1})
    with pytest.raises(ValueError):
        Probe("x", {"type": "file_exists", "path": "a"}, {"op": "vibes"})


def test_spec_roundtrip_and_load(tmp_path):
    p = Probe("port", {"type": "ini_value", "path": "s.ini", "section": "a", "key": "b"},
              {"op": "equals", "expected": "1"})
    assert load_probe(json.dumps(p.to_dict())).to_dict() == p.to_dict()
    (tmp_path / "p.json").write_text(json.dumps(p.to_dict()))
    assert load_probe(tmp_path / "p.json").id == "port"


def test_v1_validity_rule(tmp_path):
    start, end = tmp_path / "s", tmp_path / "e"
    start.mkdir(), end.mkdir()
    (end / "out.txt").write_text("done")
    good = Probe("g", {"type": "file_content", "path": "out.txt"}, {"op": "equals", "expected": "done"})
    assert validate_probe(good, start, end).valid
    # passes on start too -> vacuous, rejected
    vacuous = Probe("v", {"type": "file_exists", "path": "."}, {"op": "equals", "expected": True})
    v = validate_probe(vacuous, start, end)
    assert not v.valid and not v.fails_on_start
    # fails on end -> wrong probe, rejected
    wrong = Probe("w", {"type": "file_content", "path": "out.txt"}, {"op": "equals", "expected": "DONE"})
    assert not validate_probe(wrong, start, end).valid


def test_probeset_all_of(tmp_path):
    (tmp_path / "a").write_text("1")
    ps = ProbeSet.from_list([
        {"id": "a", "getter": {"type": "file_exists", "path": "a"}, "comparator": {"op": "truthy"}},
        {"id": "b", "getter": {"type": "file_exists", "path": "b"}, "comparator": {"op": "truthy"}},
    ])
    assert not ps.holds(tmp_path)
    (tmp_path / "b").write_text("1")
    assert ps.holds(tmp_path)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS hooks")
def test_defaults_read_on_scratch_plist(tmp_path):
    with open(tmp_path / "dom.plist", "wb") as f:
        plistlib.dump({"K": 7}, f)
    assert probe({"type": "defaults_read", "domain": "dom.plist", "key": "K"}, "equals", "7").holds(tmp_path)
    assert not probe({"type": "defaults_read", "domain": "dom.plist", "key": "Missing"},
                     "truthy").holds(tmp_path)


def test_process_running_getter(tmp_path):
    assert "process_running" in GETTERS
    r = probe({"type": "process_running", "name": "definitely-not-a-proc-xyz"}, "equals", False)
    assert r.holds(tmp_path)


def test_ax_element_path_is_sanitised(tmp_path):
    r = probe({"type": "ax_attribute", "process": "Finder", "attribute": "AXTitle",
               "element": 'window 1" & do shell script "id'}, "truthy").evaluate(tmp_path)
    assert not r.passed and r.error
