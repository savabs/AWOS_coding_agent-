"""Content-script logic without the extension: lib/*.js injected into plain pages.

Covers the pieces that decide safety (sensitive-field and danger vocabulary),
re-finding (Similo-style scoring, the anchor rule) and the model-answer parser.
Runs in plain headless Chromium, so it still works where extensions cannot load.
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import pathlib

import pytest

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"

pytestmark = pytest.mark.timeout(60)
LIBS = ["lib/safety.js", "lib/locator.js", "lib/model.js"]


@pytest.fixture(scope="module")
def browser(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    yield b
    b.close()


@pytest.fixture()
def page(browser, server):
    p = browser.new_page()

    def load(path):
        p.goto(server.base + path)
        for lib in LIBS:
            p.add_script_tag(path=str(EXT / lib))
        return p
    p.load = load
    yield p
    p.close()


def _record(page, sel):
    return page.evaluate("(s) => AWOSLocator.describe(document.querySelector(s))", sel)


def _find(page, loc, kind):
    return page.evaluate("([l, k]) => { const r = AWOSLocator.find(l, k);"
                         " r.pick = r.index >= 0 ? AWOSLocator.element(r.index).id : null; return r; }", [loc, kind])


def test_danger_vocabulary(page):
    page.load("/help.html")
    cases = {
        "Pay now": "pay now", "Make payment": "payment", "Buy": "buy", "Complete purchase": "purchase",
        "Checkout": "checkout", "Send": "send", "Submit order": "submit order", "Place your order": "place your order",
        "Delete account": "delete", "Remove item": "remove", "Empty trash": "empty", "Delete permanently": "delete",
        "Discard draft": "discard", "Clear all": "clear all", "Transfer funds": "transfer",
        "Save": None, "Submit": None, "Next": None, "Sender settings": None, "Display name": None, "Cancel": None,
    }
    got = page.evaluate("(cs) => Object.fromEntries(cs.map(c => [c, AWOSSafety.dangerMatch(c)]))", list(cases))
    assert got == cases


def test_url_danger(page):
    page.load("/help.html")
    cases = {
        "http://a.test/account/delete?confirm=1": "delete", "http://a.test/cart/empty": "empty",
        "http://a.test/pay?order=7": "pay", "http://a.test/app#/trash/delete-permanently": "delete",
        "http://a.test/x?action=send-money": "send", "http://a.test/check-out": "check out",
        "http://a.test/profile/edit": None, "http://a.test/form.html": None,
        "http://pay.send.test/home": None,              # the host is not matched, only path/query/hash
        "http://a.test/sendgrid/settings": None,
    }
    got = page.evaluate("(cs) => Object.fromEntries(cs.map(c => [c, AWOSSafety.urlDanger(c)]))", list(cases))
    assert got == cases


def test_danger_context_includes_form(page):
    page.load("/help.html")
    r = page.evaluate("""() => { document.body.innerHTML =
        '<form action="/orders/submit-order" id="f"><button id="b">Confirm</button></form><button id="c">Confirm</button>';
        const ctx = (id) => AWOSSafety.dangerMatch(AWOSSafety.dangerContext(document.getElementById(id), 'Confirm'));
        return [ctx('b'), ctx('c')]; }""")
    assert r == ["submit order", None]


def test_sensitive_fields(page):
    page.load("/login.html")
    got = page.evaluate("() => ['user','pw','code','cardno','ssn'].map(id => AWOSSafety.sensitiveField(document.getElementById(id)))")
    assert got[0] is None and all(got[1:]), got
    extra = page.evaluate("""() => { document.body.innerHTML =
        '<input id=a autocomplete="one-time-code"><input id=b name="cc-csc"><input id=c placeholder="CVV">' +
        '<input id=d aria-label="Search"><input id=e name="email">';
        return ['a','b','c','d','e'].map(id => AWOSSafety.sensitiveField(document.getElementById(id))); }""")
    assert all(extra[:3]) and extra[3:] == [None, None], extra


def test_refind_after_label_and_id_rename(page):
    page.load("/form.html")
    locs = {s: _record(page, s) for s in ("#fullname", "#email", "#save")}
    page.load("/form_renamed.html")
    r = _find(page, locs["#email"], "fill")
    assert r["status"] == "found" and r["pick"] == "contact-email" and r["anchor"], r
    assert _find(page, locs["#fullname"], "fill")["pick"] == "fullname"
    assert _find(page, locs["#save"], "click")["pick"] == "save"


def test_deleted_target_is_never_found_by_position(page):
    page.load("/form.html")
    loc = _record(page, "#save")
    page.load("/form_deleted.html")
    r = _find(page, loc, "click")
    # Cancel now occupies Save's css path and position, but no identity attribute matches
    assert r["status"] != "found", r
    assert all(not c["anchor"] for c in r["shortlist"])


def test_parse_model_answer(page):
    page.load("/help.html")
    # the repair model's contract (repair/model.py REPLY_RE): PICK <n>: reason | NOT_FOUND: reason
    got = page.evaluate("""() => [ 'NOT_FOUND: the Save button is gone', 'PICK 2: same textbox', 'PICK 3',
                                   'PICK 5: out of range', 'Candidate 1.', '2', 'not found' ]
        .map(t => AWOSModel.parseAnswer(t, 3))""")
    assert got[0] == {"notFound": True, "reason": "the Save button is gone"}
    assert got[1] == {"choice": 1, "reason": "same textbox"} and got[2] == {"choice": 2, "reason": ""}
    assert all("error" in g for g in got[3:])           # out of range / free text: stop, never a guess
    assert page.evaluate("() => ['http://127.0.0.1:8080/v1','http://localhost:1/v1','http://[::1]:8/v1',"
                         "'https://api.openai.com/v1','http://127.0.0.1.evil.com/v1'].map(AWOSModel.isLoopback)") \
        == [True, True, False, False, False]      # [::1] is not in the manifest's host permissions
