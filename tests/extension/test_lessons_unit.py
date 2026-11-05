"""Per-site lessons (v0.10, docs/research/experience_reuse_2026-11-05.md) without the extension: lib/lessons.js in a plain page.

  - plain-words corrections parse rule-based into do / avoid; URLs, emails, long numbers and tokens are masked
  - add merges near-identical lessons on the same host and keeps at most 20 per host (disabled, then useless first)
  - retrieve stays on the host, skips disabled / unconfirmed model lessons, ranks state > goal > utility, max k
  - score / prune: wins, losses, loss streaks and staleness disable a lesson with a reason; the UI can undo it
  - render is the short block the prompt gets
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension/test_lessons_unit.py -q
"""
import pathlib

import pytest

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"
pytestmark = pytest.mark.timeout(60)

NOW = 1_794_000_000_000
DAY = 24 * 3600 * 1000
CTX = {"host": "claude.ai", "stateKey": "dialog:Settings", "stateLabel": "Settings dialog",
       "goal": "download the October invoice", "now": NOW}


@pytest.fixture(scope="module")
def page(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()
    p.set_content("<html><body></body></html>")
    p.add_script_tag(path=str(EXT / "lib/lessons.js"))
    yield p
    b.close()


def L(page, expr, *args):
    return page.evaluate("(a) => { const L = AWOSLessons; return (" + expr + ")(...a); }", list(args))


def corr(page, text, ctx=None):
    return L(page, "(t, c) => L.fromCorrection(t, c)", text, ctx or CTX)


@pytest.mark.parametrize("text,do,avoid", [
    ("use the Settings tab, not Profile", "Settings tab", "Profile"),
    ("Click Billing instead of Customize.", "Billing", "Customize"),
    ("instead of Chats and tasks, open the profile menu", "profile menu", "Chats and tasks"),
    ("pick 'Download PDF' rather than View", "Download PDF", "View"),
    ("don't click Customize", "", "Customize"),
    ("Do not open the chat history links", "", "chat history links"),
    ("never press Delete account", "", "Delete account"),
    ("Settings is under the profile menu", "open the profile menu first, then Settings", ""),
    ("Billing is in Settings", "open the Settings first, then Billing", ""),
    ("wait for the invoice list to load", "wait for the invoice list to load", ""),
])
def test_correction_phrasings(page, text, do, avoid):
    it = corr(page, text)
    assert it["do"] == do and it["avoid"] == avoid, it
    assert it["src"] == "user" and it["confirmed"] is True and it["host"] == "claude.ai"
    assert it["state"] == "dialog:Settings" and it["created"] == NOW
    assert it["hits"] == it["wins"] == it["losses"] == it["streakLoss"] == 0 and it["disabled"] is False
    assert it["when"] == 'on Settings dialog for "download the October invoice"'


def test_correction_masks_secrets_and_rejects_empty(page):
    it = corr(page, "open https://claude.ai/settings/billing?token=abc123 not card 4111 1111 1111 1111, mail ada@ex.com "
                    "key sk_live_9fA8b7C6d5E4f3G2h1")
    blob = it["do"] + it["avoid"]
    for leak in ("https", "token", "4111", "ada@ex.com", "sk_live"):
        assert leak not in blob, blob
    assert "«link»" in blob and "«number»" in blob and "«email»" in blob and "«masked»" in blob
    assert corr(page, "   ") is None
    assert corr(page, "use Billing", {"host": ""}) is None


def test_add_dedupes_near_identical(page):
    a = corr(page, "use the Settings tab, not Profile")
    b = corr(page, "Use Settings, not the Profile button", dict(CTX, now=NOW + 1000))
    c = corr(page, "use the Settings tab, not Profile", dict(CTX, host="example.com"))
    d = corr(page, "click Billing instead of Profile")
    led = L(page, "(a, b, c, d) => [a, b, c, d].reduce((l, x) => L.add(l, x), [])", a, b, c, d)
    assert len(led) == 3, led                              # b merged into a; other host and other do are kept
    assert led[0]["id"] == a["id"] and led[0]["used"] == NOW + 1000
    # a user re-stating a disabled lesson turns it back on; the input ledger is not mutated
    off = L(page, "(l, id) => L.setDisabled(l, id, true)", led, a["id"])
    assert off[0]["disabled"] and off[0]["why"]
    back = L(page, "(l, b) => L.add(l, b)", off, b)
    assert back[0]["disabled"] is False and "why" not in back[0] and len(back) == 3


def test_add_caps_per_host(page):
    led = L(page, """(now) => {
        let l = [];
        for (let i = 0; i < 20; i++) l = L.add(l, L.make({host: 'claude.ai', do: 'step ' + 'abcdefghijklmnopqrst'[i] + ' word' + i,
                                                         src: 'verified'}, now + i));
        l = l.map((x, i) => Object.assign(x, {wins: i === 3 ? 0 : 5, losses: i === 3 ? 4 : 0, disabled: i === 7}));
        l = L.add(l, L.make({host: 'other.com', do: 'elsewhere'}, now));
        l = L.add(l, L.make({host: 'claude.ai', do: 'brand new lesson', src: 'user'}, now + 99));   // drops the disabled one
        l = L.add(l, L.make({host: 'claude.ai', do: 'another new lesson', src: 'user'}, now + 100)); // drops utility -4
        return l; }""", NOW)
    mine = [x for x in led if x["host"] == "claude.ai"]
    assert len(mine) == 20 and len(led) == 21
    dos = [x["do"] for x in mine]
    assert "step h word7" not in dos and "step d word3" not in dos
    assert "brand new lesson" in dos and "another new lesson" in dos


def test_retrieve_k_host_isolation_and_ranking(page):
    led = L(page, """(now) => {
        const m = (f, t) => L.make(Object.assign({src: 'user'}, f), t);
        let l = [];
        l = L.add(l, m({host: 'claude.ai', do: 'generic tip', goal: 'something else'}, now + 50));
        l = L.add(l, m({host: 'claude.ai', do: 'open Billing tab', goal: 'download October invoice'}, now));
        l = L.add(l, m({host: 'claude.ai', do: 'state rule', state: 'dialog:Settings'}, now));
        l = L.add(l, m({host: 'claude.ai', do: 'model guess unconfirmed', src: 'model', state: 'dialog:Settings'}, now));
        l = L.add(l, m({host: 'claude.ai', do: 'model guess confirmed', src: 'model', confirmed: true, goal: 'invoice'}, now));
        l = L.add(l, m({host: 'evil.com', do: 'other host', state: 'dialog:Settings', goal: 'download October invoice'}, now));
        l = L.add(l, Object.assign(m({host: 'claude.ai', do: 'disabled rule', state: 'dialog:Settings'}, now), {disabled: true}));
        return l; }""", NOW)
    q = {"host": "claude.ai", "stateKey": "dialog:Settings", "goal": "download the October invoice"}
    got = L(page, "(l, q) => L.retrieve(l, q)", led, q)
    assert [x["do"] for x in got] == ["state rule", "open Billing tab"]
    allk = L(page, "(l, q) => L.retrieve(l, q, 10)", led, q)
    dos = [x["do"] for x in allk]
    assert "other host" not in dos and "disabled rule" not in dos and "model guess unconfirmed" not in dos
    assert "model guess confirmed" in dos and len(dos) == 4
    assert L(page, "(l) => L.retrieve(l, {host: 'nowhere.com'})", led) == []
    # utility breaks a tie in goal / state
    led2 = L(page, """(l) => l.map(x => x.do === 'generic tip' ? Object.assign(x, {wins: 3}) : x)""", led)
    top = L(page, "(l) => L.retrieve(l, {host: 'claude.ai', goal: 'zzz'}, 1)", led2)
    assert top[0]["do"] == "generic tip"


def test_render(page):
    assert L(page, "() => L.render([])") == ""
    a = corr(page, "use the Settings tab, not Profile")
    b = corr(page, "don't click Customize", dict(CTX, stateLabel="", stateKey="", goal=""))
    text = L(page, "(a, b) => L.render([a, b])", a, b)
    assert text == ("Rules learned on this site (from earlier runs):\n"
                    '- When on Settings dialog for "download the October invoice": do Settings tab. Avoid: Profile.\n'
                    "- When on this site: Avoid: Customize.")


def test_score_lifecycle(page):
    a = corr(page, "use the Settings tab, not Profile")
    b = corr(page, "click Billing instead of Customize")
    led = [a, b]
    led = L(page, "(l, id) => L.score(l, [id], 'VERIFIED', 5)", led, a["id"])
    assert led[0]["hits"] == 1 and led[0]["wins"] == 1 and led[0]["used"] == 5 and led[1]["hits"] == 0
    led = L(page, "(l, id) => L.score(l, [id], 'UNVERIFIED')", led, a["id"])
    led = L(page, "(l, id) => L.score(l, [id], 'CHECK')", led, a["id"])
    assert led[0]["hits"] == 3 and led[0]["wins"] == 1 and led[0]["losses"] == 0
    led = L(page, "(l, id) => L.score(l, [id], 'FAILED')", led, a["id"])
    led = L(page, "(l, id) => L.score(l, [id], 'halted')", led, a["id"])
    assert led[0]["losses"] == 2 and led[0]["streakLoss"] == 2
    led = L(page, "(l, id) => L.score(l, [id], 'VERIFIED')", led, a["id"])
    assert led[0]["streakLoss"] == 0 and led[0]["wins"] == 2


def test_prune_reasons_and_undo(page):
    base = corr(page, "use the Settings tab, not Profile")
    rows = [dict(base, id="streak", streakLoss=2, hits=2, losses=2, used=NOW),
            dict(base, id="useless", hits=5, wins=1, used=NOW),
            dict(base, id="stale", hits=1, wins=1, used=NOW - 61 * DAY),
            dict(base, id="good", hits=5, wins=4, used=NOW - 10 * DAY)]
    out = {x["id"]: x for x in L(page, "(l, now) => L.prune(l, now)", rows, NOW)}
    assert out["streak"]["disabled"] and "in a row" in out["streak"]["why"]
    assert out["useless"]["disabled"] and "1 of 5" in out["useless"]["why"]
    assert out["stale"]["disabled"] and "60 days" in out["stale"]["why"]
    assert out["good"]["disabled"] is False and "why" not in out["good"]
    led = L(page, "(l, now) => L.prune(l, now)", rows, NOW)
    led = L(page, "(l) => L.setDisabled(l, 'streak', false)", led)
    s = [x for x in led if x["id"] == "streak"][0]
    assert s["disabled"] is False and s["streakLoss"] == 0 and "why" not in s
    assert [x["id"] for x in L(page, "(l) => L.remove(l, 'stale')", led)] == ["streak", "useless", "good"]


def test_list_filters_and_sorts(page):
    a = corr(page, "use Billing", dict(CTX, now=NOW))
    b = corr(page, "use Invoices", dict(CTX, now=NOW + 5))
    c = corr(page, "use Statements", dict(CTX, host="bank.example", now=NOW))
    led = [a, c, b]
    assert [x["do"] for x in L(page, "(l) => L.list(l, 'claude.ai')", led)] == ["use Invoices", "use Billing"]
    assert [x["host"] for x in L(page, "(l) => L.list(l)", led)] == ["bank.example", "claude.ai", "claude.ai"]
