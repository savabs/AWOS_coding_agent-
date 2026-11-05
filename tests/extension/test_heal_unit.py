"""Self-healing steps (v0.10, extension/lib/heal.js) without the extension: AWOSHeal in a plain page.

  - record appends a value-free alternate, dedupes by role + name + context (any case), caps 4 (worst goes)
  - confirm: VERIFIED marks that run's alternates verified, FAILED removes them (verified ones stay)
  - candidates: promoted alternates (2 verified uses) before the recording, demoted recording after 2 failures in a row
  - outcome drops an alternate that failed twice and never worked; revert forgets everything learned
  - the recorded loc is never changed and the input recording is never mutated
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension/test_heal_unit.py -q
"""
import copy
import pathlib

import pytest

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"
pytestmark = pytest.mark.timeout(60)

AT = 1793880000000  # 2026-11-05 (local date checked loosely below)
PRIMARY = {"tag": "button", "role": "tab", "name": "Billing", "context": "Settings", "css": "div > button:nth-of-type(4)",
           "text": "Billing", "pos": {"x": 10, "y": 20, "w": 30, "h": 40}}


def rec():
    return {"id": "rec-1", "steps": [
        {"action": "goto", "url": "https://claude.ai/"},
        {"action": "click", "loc": dict(PRIMARY, name="Profile")},
        {"action": "click", "loc": copy.deepcopy(PRIMARY)},
    ]}


def alt(name, **kw):
    d = {"tag": "button", "role": "tab", "name": name, "context": "Settings", "css": "div > button", "text": name}
    d.update(kw)
    return d


@pytest.fixture(scope="module")
def page(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()
    p.set_content("<html><body></body></html>")
    p.add_script_tag(path=str(EXT / "lib/heal.js"))
    yield p
    b.close()


def H(page, fn, *args):
    return page.evaluate("([fn, args]) => AWOSHeal[fn].apply(null, args)", [fn, list(args)])


def meta(run="run-1", by="4b", at=AT):
    return {"by": by, "runId": run, "at": at}


def test_record_appends_without_values_and_keeps_primary(page):
    r0 = rec()
    r = H(page, "record", r0, 2, alt("Plans & billing", value="4111 1111", defaultValue="x", href="/settings/billing?tok=S#f"),
          meta())
    a = r["steps"][2]["alternates"]
    assert len(a) == 1
    assert a[0]["loc"]["name"] == "Plans & billing" and a[0]["by"] == "4b" and a[0]["runId"] == "run-1"
    assert a[0]["ok"] == 0 and a[0]["fail"] == 0 and a[0]["verified"] is False and a[0]["at"] == AT
    assert "value" not in a[0]["loc"] and "defaultValue" not in a[0]["loc"]
    assert a[0]["loc"]["href"] == "/settings/billing"
    assert r["steps"][2]["loc"] == PRIMARY
    assert r0 == rec()  # input untouched (pure)


def test_record_dedupes_case_insensitive(page):
    r = H(page, "record", rec(), 2, alt("Plans & billing"), meta("run-1"))
    r = H(page, "record", r, 2, alt("PLANS &  Billing", context=" settings ", css="other"), meta("run-2"))
    a = r["steps"][2]["alternates"]
    assert len(a) == 1 and a[0]["runId"] == "run-2" and a[0]["loc"]["css"] == "other"


def test_record_caps_four_dropping_worst(page):
    r = rec()
    for i, n in enumerate(["A", "B", "C", "D"]):
        r = H(page, "record", r, 2, alt(n), meta("run-" + n, at=AT + i))
    r = H(page, "confirm", r, "run-A", "VERIFIED")
    r = H(page, "record", r, 2, alt("E"), meta("run-E", at=AT + 10))
    names = [x["loc"]["name"] for x in r["steps"][2]["alternates"]]
    assert len(names) == 4
    assert "A" in names and "E" in names and "B" not in names  # oldest unverified goes


def test_confirm_verified_vs_failed(page):
    r = H(page, "record", rec(), 2, alt("Plans & billing"), meta("run-1"))
    r = H(page, "record", r, 1, alt("Account", role="button"), meta("run-2"))
    v = H(page, "confirm", r, "run-1", "VERIFIED")
    assert v["steps"][2]["alternates"][0]["verified"] is True
    assert v["steps"][1]["alternates"][0]["verified"] is False
    f = H(page, "confirm", v, "run-2", "FAILED")
    assert "alternates" not in f["steps"][1] or f["steps"][1]["alternates"] == []
    assert len(f["steps"][2]["alternates"]) == 1
    # a verified alternate survives a later failed run that re-recorded it
    again = H(page, "record", f, 2, alt("plans & billing"), meta("run-3"))
    again = H(page, "confirm", again, "run-3", "FAILED")
    assert len(again["steps"][2]["alternates"]) == 1
    # no proof either way: stays pending
    u = H(page, "confirm", H(page, "record", rec(), 2, alt("X"), meta("run-9")), "run-9", "UNVERIFIED")
    assert u["steps"][2]["alternates"][0]["verified"] is False


def keys(page, step):
    return [(c["source"], c["loc"]["name"]) for c in H(page, "candidates", step)]


def test_candidates_order_and_promotion(page):
    r = H(page, "record", rec(), 2, alt("Verified"), meta("run-1"))
    r = H(page, "record", r, 2, alt("Pending"), meta("run-2"))
    r = H(page, "confirm", r, "run-1", "VERIFIED")
    step = r["steps"][2]
    assert keys(page, step) == [("primary", "Billing"), ("alternate", "Verified")]  # a pending one is never tried
    k = [c for c in H(page, "candidates", step) if c["loc"]["name"] == "Verified"][0]["key"]
    r = H(page, "outcome", r, 2, k, True)
    assert keys(page, r["steps"][2])[0] == ("primary", "Billing")  # one use is not enough
    r = H(page, "outcome", r, 2, k, True)
    assert keys(page, r["steps"][2]) == [("alternate", "Verified"), ("primary", "Billing")]


def test_unverified_alternate_is_never_a_candidate(page):
    r = H(page, "record", rec(), 2, alt("Pending"), meta("run-2"))
    assert keys(page, r["steps"][2]) == [("primary", "Billing")]
    r = H(page, "confirm", r, "run-2", "CHECK")                    # no proof either way: still pending, still not tried
    assert keys(page, r["steps"][2]) == [("primary", "Billing")] and r["steps"][2]["alternates"][0]["verified"] is False


def test_check_then_verified_rescue_is_promoted(page):
    # review v0.10: an alternate first recorded in a CHECK run, rescued again in later VERIFIED runs, gets verified
    # and promoted; the recording steps back after 2 misses
    r = H(page, "record", rec(), 2, alt("Plans"), meta("run-A"))
    r = H(page, "confirm", r, "run-A", "CHECK")
    k = r["steps"][2]["alternates"][0]["key"]
    for run in ("run-B", "run-C"):
        r = H(page, "outcome", r, 2, "primary", False)
        r = H(page, "record", r, 2, alt("Plans"), meta(run))             # the model rescues the same control again
        r = H(page, "confirm", r, run, "VERIFIED")
        assert r["steps"][2]["alternates"][0]["verified"] is True
    r = H(page, "outcome", r, 2, k, True)
    r = H(page, "outcome", r, 2, k, True)
    a = r["steps"][2]["alternates"][0]
    assert a["ok"] == 2 and a["verified"] is True and r["steps"][2]["primaryFails"] == 2
    assert keys(page, r["steps"][2]) == [("alternate", "Plans"), ("primary", "Billing")]
    assert "not verified yet" not in H(page, "describeHeal", r["steps"][2], 2)


def test_verified_use_marks_a_pending_alternate_verified(page):
    r = H(page, "record", rec(), 2, alt("Pending"), meta("run-A"))
    r = H(page, "confirm", r, "run-A", "UNVERIFIED")
    k = r["steps"][2]["alternates"][0]["key"]
    r = H(page, "outcome", r, 2, k, True)                            # only called for steps of a VERIFIED run
    assert r["steps"][2]["alternates"][0]["verified"] is True
    assert keys(page, r["steps"][2]) == [("primary", "Billing"), ("alternate", "Pending")]


def test_primary_demoted_after_two_fails_only_with_verified_alt(page):
    r = H(page, "record", rec(), 2, alt("Pending"), meta("run-2"))
    r = H(page, "outcome", r, 2, "primary", False)
    r = H(page, "outcome", r, 2, "primary", False)
    assert r["steps"][2]["primaryFails"] == 2
    assert keys(page, r["steps"][2])[0] == ("primary", "Billing")  # nothing verified to fall back on
    r = H(page, "record", r, 2, alt("Verified"), meta("run-1"))
    r = H(page, "confirm", r, "run-1", "VERIFIED")
    assert keys(page, r["steps"][2]) == [("alternate", "Verified"), ("primary", "Billing")]
    r = H(page, "outcome", r, 2, "primary", True)  # success resets the streak
    assert "primaryFails" not in r["steps"][2]
    assert keys(page, r["steps"][2])[0] == ("primary", "Billing")


def test_outcome_drops_bad_alternate(page):
    r = H(page, "record", rec(), 2, alt("Bad"), meta("run-1"))
    r = H(page, "record", r, 2, alt("Once good"), meta("run-1"))
    cs = {a["loc"]["name"]: a["key"] for a in r["steps"][2]["alternates"]}
    r = H(page, "outcome", r, 2, cs["Bad"], False)
    assert len(r["steps"][2]["alternates"]) == 2
    r = H(page, "outcome", r, 2, cs["Bad"], False)
    r = H(page, "outcome", r, 2, cs["Once good"], True)
    r = H(page, "outcome", r, 2, cs["Once good"], False)
    r = H(page, "outcome", r, 2, cs["Once good"], False)
    names = [a["loc"]["name"] for a in r["steps"][2]["alternates"]]
    assert names == ["Once good"]  # worked once: kept (but no longer first in line)
    assert H(page, "outcome", r, 2, "alt:missing", True) == r


def test_describe_heal(page):
    assert H(page, "describeHeal", rec()["steps"][2], 2) == ""
    r = H(page, "record", rec(), 2, alt("Plans & billing"), meta("run-1"))
    line = H(page, "describeHeal", r["steps"][2], 2)
    assert line.startswith('step 3 now also matches tab "Plans & billing" (fixed by the local model on 2026-11-0')
    assert line.endswith("not verified yet)")
    r = H(page, "confirm", r, "run-1", "VERIFIED")
    k = r["steps"][2]["alternates"][0]["key"]
    r = H(page, "outcome", H(page, "outcome", r, 2, k, True), 2, k, True)
    assert H(page, "describeHeal", r["steps"][2], 2).endswith(", verified twice)")
    u = H(page, "record", rec(), 1, alt('Say "hi"\nnow', role="menuitem"), meta("r", by="user"))
    assert 'menu item "Say \'hi\' now" (fixed by you' in H(page, "describeHeal", u["steps"][1], 1)


def test_revert_and_primary_never_mutated(page):
    r0 = rec()
    r = H(page, "record", r0, 2, alt("A"), meta("run-1"))
    r = H(page, "record", r, 2, alt("B", value="secret"), meta("run-2"))
    r = H(page, "confirm", r, "run-1", "VERIFIED")
    for c in H(page, "candidates", r["steps"][2]):
        r = H(page, "outcome", r, 2, c["key"], False)
        r = H(page, "outcome", r, 2, c["key"], False)
    assert r["steps"][2]["loc"] == PRIMARY and r["steps"][1]["loc"] == r0["steps"][1]["loc"]
    assert "secret" not in str(r)
    back = H(page, "revert", r, 2)
    assert back == r0
    assert H(page, "candidates", r0["steps"][0]) == []  # a goto has nothing to locate
