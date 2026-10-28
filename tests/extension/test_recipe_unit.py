"""Recipe format v1 (docs/specs/recipe_v1.md) without the extension: lib/safety.js + lib/recipe.js in a plain page.

  - export strips typed values and secrets (fill -> {{param}} or ask:true), scrubs typed values from every other
    field, strips URL queries / fragments, keeps the result under 64 KB
  - the share link round-trips (base64url(deflate-raw(JSON)) in the fragment) and decodes like the spec says
  - validation rejects bad and oversized recipes, literal fill values, undeclared params; inflating stops at 64 KB
  - toRecording makes an untrusted, domain-locked recording and recomputes danger from the description
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import base64
import json
import pathlib
import zlib

import pytest

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"
pytestmark = pytest.mark.timeout(60)

BASE = "https://bank.example.com"
LOC = {"tag": "input", "role": "textbox", "name": "Full name", "label": "Full name", "id": "fullname", "css": "#fullname",
       "neighbor": "Full name", "block": "", "siblings": ["Email", "Plan"], "pos": {"x": 1, "y": 2, "w": 3, "h": 4}}


def rec(**kw):
    r = {
        "id": "rec-1", "name": "Statement", "goal": "Statement for October 2026 saved for Ada Lovelace",
        "description": "", "startUrl": BASE + "/statements?session=abc123#top",
        "steps": [
            {"action": "goto", "url": BASE + "/statements?session=abc123#top"},
            {"action": "fill", "loc": dict(LOC), "url": BASE + "/statements?q=1", "value": "Ada Lovelace", "sensitive": None, "mergeKey": "f1"},
            {"action": "fill", "loc": dict(LOC, name="Password", id="pw", text="", neighbor="Password"), "url": BASE + "/statements",
             "value": "{{secret:password}}", "sensitive": "password"},
            {"action": "fill", "loc": dict(LOC, name="Month", id="month", neighbor="near October 2026 here"), "url": BASE + "/statements",
             "value": "October 2026", "sensitive": None, "param": "month"},
            {"action": "select", "loc": dict(LOC, role="combobox", name="Account", id="acct"), "value": "chk", "optionText": "Checking"},
            {"action": "click", "loc": dict(LOC, role="button", name="Download", id="dl", href="/dl?token=SECRETTOKEN#x",
                                            block="Hello Ada Lovelace, your statement"), "url": BASE + "/statements?x=y",
             "danger": None, "navigates": True},
        ],
        "params": [{"name": "month", "label": "Which month", "example": "September 2026", "kind": "text"}],
        "expect": {"urlPattern": BASE + "/statements", "texts": ["Statement for October 2026", "Signed in as Ada Lovelace"]},
        "expectDownload": {"nameRegex": r"statement.*\.pdf$"},
        "lastParams": {"month": "August 2026"}, "schedule": {"kind": "daily", "time": "09:00"},
    }
    r.update(kw)
    return r


@pytest.fixture(scope="module")
def page(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()
    p.set_content("<html><body></body></html>")
    for lib in ("lib/safety.js", "lib/recipe.js"):
        p.add_script_tag(path=str(EXT / lib))
    yield p
    b.close()


def export(page, r):
    return page.evaluate("(r) => AWOSRecipe.fromRecording(r)", r)


def validate(page, obj):
    return page.evaluate("(o) => AWOSRecipe.validate(o)", obj)


def good_recipe(**kw):
    r = {"awos_recipe": 1, "name": "Demo", "description": "d", "start_url": BASE + "/statements",
         "params": [{"name": "month", "label": "Which month", "example": "October 2026", "kind": "text"}],
         "steps": [{"action": "goto", "url": BASE + "/statements"},
                   {"action": "fill", "loc": {"role": "textbox", "name": "Month"}, "value": "{{month}}"},
                   {"action": "fill", "loc": {"role": "password", "name": "Password"}, "ask": True, "sensitive": "password"},
                   {"action": "click", "loc": {"role": "button", "name": "Go"}}],
         "checks": {"goal": "Statement for {{month}}", "expect_text": ["Done"], "expect_url": BASE + "/done",
                    "expect_download": {"name_regex": "statement.*"}},
         "created_with": "awos-replay 0.4"}
    r.update(kw)
    return r


# ---- export ---------------------------------------------------------------------------------------------

def test_export_strips_typed_values_and_secrets(page):
    out = export(page, rec())
    r, blob = out["recipe"], json.dumps(out["recipe"])
    assert r["awos_recipe"] == 1 and r["created_with"] == "awos-replay 0.4"
    for secret in ("Ada Lovelace", "October 2026", "abc123", "SECRETTOKEN", "session=", "August 2026", "09:00"):
        assert secret not in blob, secret
    fills = [s for s in r["steps"] if s["action"] == "fill"]
    assert fills[0] == {"action": "fill", "loc": fills[0]["loc"], "url": BASE + "/statements", "ask": True}   # not a param -> ask
    assert fills[1]["ask"] is True and fills[1]["sensitive"] == "password" and "value" not in fills[1]
    assert fills[2]["value"] == "{{month}}" and "ask" not in fills[2]
    assert r["params"] == [{"name": "month", "label": "Which month", "example": "September 2026", "kind": "text"}]
    sel = next(s for s in r["steps"] if s["action"] == "select")
    assert sel["value"] == "chk" and sel["optionText"] == "Checking"           # dropdown choices are not typed values


def test_export_scrubs_typed_values_everywhere_and_strips_urls(page):
    out = export(page, rec())
    r = out["recipe"]
    assert r["start_url"] == BASE + "/statements"
    assert all("?" not in (s.get("url") or "") and "#" not in (s.get("url") or "") for s in r["steps"])
    click = r["steps"][-1]
    assert click["loc"]["href"] == "/dl" and "Ada" not in click["loc"]["block"]
    month = r["steps"][3]
    assert month["loc"]["neighbor"] == "near {{month}} here"                    # a param's value becomes its token
    assert r["checks"]["expect_text"] == ["Statement for {{month}}"]            # the one with "Ada Lovelace" is dropped
    assert any("dropped the expected text" in w for w in out["warnings"])
    assert r["checks"]["goal"] == "Statement for {{month}} saved for [typed value]"
    assert r["checks"]["expect_download"] == {"name_regex": r"statement.*\.pdf$"}
    assert r["checks"]["expect_url"] == BASE + "/statements"
    assert "lastParams" not in json.dumps(r) and "schedule" not in json.dumps(r) and "mergeKey" not in json.dumps(r)


def test_secret_field_never_becomes_a_param(page):
    r = rec()
    r["steps"][2]["param"] = "month"                                           # even if a param is attached to it
    out = export(page, r)
    pw_step = out["recipe"]["steps"][2]
    assert pw_step.get("ask") is True and "value" not in pw_step
    assert any("secret field" in w for w in out["warnings"])


def test_contenteditable_text_is_not_exported(page):
    r = rec()
    r["steps"][1]["loc"]["text"] = "Ada Lovelace"
    r["steps"][1]["value"] = "xy"                                              # too short to scrub, still dropped from loc.text
    r["steps"][1]["loc"]["text"] = "xy typed"
    out = export(page, r)
    assert "text" not in out["recipe"]["steps"][1]["loc"]


def test_export_over_64kb_is_refused(page):
    r = rec(steps=[{"action": "goto", "url": BASE + "/a"}] +
            [{"action": "click", "loc": dict(LOC, role="button", name="B%d" % k, block="x" * 380, neighbor="y" * 380,
                                              css="z" * 380), "url": BASE + "/a"} for k in range(150)])
    err = page.evaluate("(r) => { try { AWOSRecipe.fromRecording(r); return ''; } catch (e) { return String(e.message); } }", r)
    assert "64 KB" in err


# ---- validation -------------------------------------------------------------------------------------------

def test_valid_recipe_passes_and_unknown_keys_dropped(page):
    v = validate(page, dict(good_recipe(), evil="x"))
    assert v["ok"], v
    assert "evil" not in v["recipe"]


@pytest.mark.parametrize("mutate, why", [
    (lambda r: r.update(awos_recipe=2), "awos_recipe"),
    (lambda r: r.update(name=""), "name"),
    (lambda r: r.update(start_url="javascript:alert(1)"), "start_url"),
    (lambda r: r.update(start_url=BASE + "/s?token=1"), "query"),
    (lambda r: r["steps"][1].update(value="Ada Lovelace"), "literal"),
    (lambda r: r["steps"][1].update(value="{{nope}}"), "undeclared"),
    (lambda r: r["steps"][2].pop("ask"), "exactly one"),
    (lambda r: r["steps"][1].update(sensitive="password"), "sensitive"),
    (lambda r: r["steps"].append({"action": "eval", "loc": {}}), "action"),
    (lambda r: r["steps"].append({"action": "goto", "url": BASE + "/x#frag"}), "fragment"),
    (lambda r: r["params"].append(dict(r["params"][0])), "repeated"),
    (lambda r: r["params"][0].update(name="Bad Name"), "params[0].name"),
    (lambda r: r["checks"].update(expect_download={"name_regex": "("}), "expect_download"),
    (lambda r: r["checks"].update(expect_url=BASE + "/x?y=1"), "expect_url"),
    (lambda r: r["steps"][3]["loc"].update(href="/a?b=c"), "href"),
    (lambda r: r.update(steps=[]), "steps"),
    (lambda r: r.update(checks="x"), "checks"),
])
def test_bad_recipes_rejected(page, mutate, why):
    r = good_recipe()
    mutate(r)
    v = validate(page, r)
    assert not v["ok"] and any(why in e for e in v["errors"]), v


def test_oversized_and_non_object_rejected(page):
    big = good_recipe(description="x" * 1990)
    big["steps"] += [{"action": "click", "loc": {"role": "button", "name": "n" * 399, "block": "b" * 399, "css": "c" * 399}}] * 150
    v = validate(page, big)
    assert not v["ok"] and "64 KB" in v["errors"][0]
    assert not validate(page, [1, 2])["ok"] and not validate(page, None)["ok"] and not validate(page, "str")["ok"]


# ---- share link -----------------------------------------------------------------------------------------

def test_share_link_round_trip_and_spec_encoding(page):
    r = export(page, rec())["recipe"]
    link = page.evaluate("(r) => AWOSRecipe.encodeLink(r)", r)
    assert link.startswith("https://awos.tirramind.com/r/#")
    frag = link.split("#", 1)[1]
    assert "=" not in frag and "+" not in frag and "/" not in frag                # base64url, no padding
    raw = base64.urlsafe_b64decode(frag + "=" * (-len(frag) % 4))
    assert json.loads(zlib.decompress(raw, -15)) == r                             # deflate-raw, per the spec
    back = page.evaluate("(l) => AWOSRecipe.decodeLink(l)", link)
    assert back["ok"] and back["recipe"] == r
    assert page.evaluate("(f) => AWOSRecipe.decodeLink(f)", frag)["recipe"] == r  # bare fragment works too


def test_decode_rejects_garbage_and_zip_bombs(page):
    for bad in ["https://awos.tirramind.com/r/#", "https://awos.tirramind.com/r/#!!!", "https://x/#AAAA"]:
        assert not page.evaluate("(l) => AWOSRecipe.decodeLink(l)", bad)["ok"], bad
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    bomb = co.compress(b'{"a":"' + b"A" * 3_000_000 + b'"}') + co.flush()          # ~3 MB of JSON in a few KB
    frag = base64.urlsafe_b64encode(bomb).decode().rstrip("=")
    v = page.evaluate("(f) => AWOSRecipe.decodeLink(f)", frag)
    assert not v["ok"] and "64 KB" in v["errors"][0]
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    notjson = base64.urlsafe_b64encode(co.compress(b"hello") + co.flush()).decode().rstrip("=")
    assert not page.evaluate("(f) => AWOSRecipe.decodeLink(f)", notjson)["ok"]


# ---- import -------------------------------------------------------------------------------------------------

def test_to_recording_is_untrusted_and_recomputes_danger(page):
    r = good_recipe()
    r["steps"] = [{"action": "fill", "loc": {"role": "textbox", "name": "Month"}, "value": "{{month}}"},
                  {"action": "click", "loc": {"role": "button", "name": "Pay now"}, "danger": None}]
    v = validate(page, r)
    assert v["ok"] and "danger" not in v["recipe"]["steps"][1]                    # shipped flags are dropped
    rr = page.evaluate("(r) => AWOSRecipe.toRecording(r, 'rec-x')", v["recipe"])
    assert rr["imported"] is True and rr["site"] == "example.com" and rr["approvedDomains"] == []
    assert rr["steps"][0] == {"action": "goto", "url": BASE + "/statements"}       # always starts at start_url
    assert rr["steps"][1]["param"] == "month" and rr["steps"][2]["danger"] == "pay now"
    v2 = validate(page, good_recipe())
    rr2 = page.evaluate("(r) => AWOSRecipe.toRecording(r, 'rec-y')", v2["recipe"])
    ask = rr2["steps"][2]
    assert ask["ask"] is True and ask["value"] == "{{secret:password}}"


def test_registrable_domain_and_strict_vocabulary(page):
    got = page.evaluate("""() => ['bank.example.com', 'a.b.example.co.uk', 'example.com', 'foo.github.io', '127.0.0.1', 'localhost',
                                  'www.amazon.com.au'].map(AWOSRecipe.registrableDomain)""")
    assert got == ["example.com", "example.co.uk", "example.com", "foo.github.io", "127.0.0.1", "localhost", "amazon.com.au"]
    assert page.evaluate("() => AWOSRecipe.sameSite('https://login.example.com/x', 'example.com')") is True
    assert page.evaluate("() => AWOSRecipe.sameSite('https://example.com.evil.io/x', 'example.com')") is False
    s = page.evaluate("""() => [AWOSSafety.dangerMatchStrict('Submit', false), AWOSSafety.dangerMatchStrict('Submit', true),
                               AWOSSafety.dangerMatchStrict('Confirm booking', true), AWOSSafety.dangerMatchStrict('Save profile', true),
                               AWOSSafety.dangerMatchStrict('Pay now', false)]""")
    assert s == [None, "submit", "confirm", None, "pay now"]
