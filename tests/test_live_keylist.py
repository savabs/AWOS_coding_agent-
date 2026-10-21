"""Issue A: the post-create API-key list readback on live Safari (local-Qwen dd variant c HALT).

Live evidence (dd-20261021-054912, provider=local): the key was created, captured and the
injection flagged, then ``GET /projects/storefront-web/keys -> verified`` and the ledger
check said "could not read the key list of storefront-web" -> HALT. The scripted run of the
same variant read the same page fine; the difference is timing (the local model sat on the
"Key created" page for 5 extra steps, VM under model load). Real Safari updates the web
area's AXURL at commit, before the new document's AX tree exists, and an AX walk under load
can stop at its time budget; the old ``_load`` accepted any two equal reads, so it could
settle on an empty / half-built web area with no key table.

These tests rebuild that on the fake Safari from tests/test_live_driver.py (real testbed HTML):
the keys page's AX tree appears late (empty, or without the table), or is truncated. They
check the readback waits for the table, never reads half a table as "absent", only reads the
right project's page, and that an unprovable effect still HALTs.
"""
import json
import re

import pytest

from scaffold.agent.desktop import micro_wiring as mw
from scaffold.agent.desktop.live import driver as D
from scaffold.agent.desktop.live import probe as P
from scaffold.agent.desktop.web_tools import Page
from tests.test_live_driver import VM, FakeRuntime, FakeSafari, actor, owner_window


# ------------------------------------------------------------ lagging Safari --

class LagRuntime(FakeRuntime):
    """children() of a web area whose document is still being built returns its early
    state until the simulated clock reaches ``reveal_at``."""

    def children(self, el):
        if el is not None and el.meta.get("reveal_at") is not None and self.t < el.meta["reveal_at"]:
            return list(el.meta.get("early") or [])
        return super().children(el)


class LagSafari(FakeSafari):
    """Keys pages (/projects/<id>/keys) expose their AX tree late.

    mode "empty": nothing under the web area at first (AXURL already updated);
    mode "partial": heading and links, but no table yet.
    ``after_create_only``: lag only once a key has been created (the post-create readback).
    ``lag=None``: the table never appears."""

    def __init__(self, rt, *, lag=2.0, mode="empty", after_create_only=False):
        super().__init__(rt)
        self.lag, self.mode, self.after_create_only = lag, mode, after_create_only
        self.created = False

    def load(self, w, url, data=None):
        if data is not None and url.rstrip("/").endswith("/keys/new") and b"name=" in data:
            self.created = True
        super().load(w, url, data)

    def _render(self, w, url, raw):
        super()._render(w, url, raw)
        web = w.meta["web"]
        web.meta.pop("reveal_at", None)
        if not re.search(r"/projects/[\w-]+/keys/?$", url):
            return
        if self.after_create_only and not self.created:
            return
        at = float("inf") if self.lag is None else self.rt.t + self.lag
        if self.mode == "empty":
            web.meta.update(early=[], reveal_at=at)
            return
        for t in [x for x in self.walk(web) if x.attrs.get("AXRole") == "AXTable"]:
            t.parent.meta.update(early=[k for k in t.parent.kids if k is not t], reveal_at=at)


def _world(**kw):
    rt = LagRuntime()
    saf = LagSafari(rt, **kw)
    owner_window(rt, saf)
    return rt, saf


@pytest.fixture
def dd(tmp_path):
    """A seeded dev_dashboard (variant c) on 127.0.0.1 and a signed-in live backend factory."""
    import sys
    tb = P.REPO / "testbeds" / "dev_dashboard"
    if str(tb) not in sys.path:
        sys.path.insert(0, str(tb))
    run = P._load(tb / "run.py", "dd_keylist_run")
    truth, srv = run.setup(tmp_path / "dd", "c", 7)
    env = json.loads((tmp_path / "dd/sandbox/AGENT_ENV.json").read_text())
    yield {"truth": truth, "srv": srv, "url": env["dashboard_url"], "root": tmp_path / "dd"}
    srv.stop()


def _backend(rt, saf, d):
    from scaffold.agent.desktop.live.actions import SAFARI
    b = D.MacBrowserBackend(d["url"], actor=actor(rt, allow_apps={SAFARI}))
    p = b.open("/login")
    kc = json.loads((d["root"] / "sandbox/Keychain/fakeservice.json").read_text())
    b.fill(p, 1, kc["username"])
    b.fill(p, 2, kc["password"])
    p = b.click(p, 3)
    code = re.search(r"Code: (\d{6})", (d["root"] / "sandbox/Authenticator/FakeService.txt").read_text()).group(1)
    b.fill(p, 1, code)
    p = b.click(p, 2)
    assert p.title.startswith("Projects"), p.title
    return b


# ------------------------------------------------------- reproduce the race --

def test_early_keys_page_has_no_table_so_one_raw_read_cannot_certify(dd):
    """What the old reader saw: the URL is already /keys but the AX tree is not built."""
    rt, saf = _world(lag=3.0, mode="empty")
    b = _backend(rt, saf, dd)
    saf.load(b.window, dd["url"] + "/projects/storefront-web/keys")      # commit, tree not built yet
    early = D.to_micro_page(b._snap())
    assert early.url.endswith("/projects/storefront-web/keys")
    assert mw.parse_key_rows(early, "storefront-web") is None             # no table -> cannot read
    assert not b._web_ready(b._snap())                                    # and _load will not settle on it


@pytest.mark.parametrize("mode", ["empty", "partial"])
def test_readback_waits_for_the_key_table(dd, mode):
    rt, saf = _world(lag=3.0, mode=mode)
    b = _backend(rt, saf, dd)
    rows = mw.read_key_rows(b, "storefront-web")
    assert rows is not None, b.log
    assert [r["name"] for r in rows] == ["ci-readonly", "old-deploy", "2025-intern"], rows
    assert all(r["status"] in ("active", "revoked", "expired") for r in rows)
    assert any(len(r["last4"]) == 4 for r in rows)                       # masked fk_live_…abcd parsed


def test_table_that_never_appears_is_unreadable_not_empty(dd):
    rt, saf = _world(lag=None, mode="partial")
    b = _backend(rt, saf, dd)
    assert mw.read_key_rows(b, "storefront-web", wait_s=2.0) is None     # None, never []


def test_truncated_snapshot_never_reads_as_absent(dd):
    """A walk cut by the node/time budget mid-table must not yield 'key not there'
    (that would let a retry create a duplicate)."""
    rt, saf = _world(lag=0.0)
    b = _backend(rt, saf, dd)
    full = b.open("/projects/storefront-web/keys")
    assert mw.parse_key_rows(full, "storefront-web")
    n = b._snap().nodes
    b.actor.max_nodes = n - 8                                             # last rows cut off
    b.load_timeout = 2.0
    cut = b._snap()
    assert cut.truncated
    assert mw.parse_key_rows(D.to_micro_page(cut), "storefront-web") is None
    assert mw.read_key_rows(b, "storefront-web", wait_s=1.0) is None
    v = mw.key_verdict(b, "storefront-web", "storefront-web-awos", mw.make_store("memory"), "fakeservice_key")
    assert not v.confirmed and not v.absent                               # indeterminate -> HALT path


# --------------------------------------------------------------- parse rules --

def _page(text, url="http://127.0.0.1:1/projects/storefront-web/keys", status=200, **live):
    p = Page(url=url, status=status, title="storefront-web · API keys", headings=[], controls=[], forms=[],
             alerts=[], statuses=[], regions=[], dialog="", text=text)
    if live:
        p._live = live
    return p


TABLE = ("Create key\nAPI keys\nName | Scope | Status | Created | Key | Actions\n"
         "web-prod | Read-only | active | 2026-01-02 | fk_live_…Ab12 | Revoke\n"
         "Name | Scope | Status | Created | Key | Actions\n"
         "2025-intern | Read-only | expired | 2025-06-01 | ...e5f6 | \n"
         "storefront-web-awos | Read-only | active | 2026-10-19 | fk_live_… |")


def test_parse_header_by_content_masked_and_trailing_cells():
    rows = mw.parse_key_rows(_page(TABLE), "storefront-web")
    assert [r["name"] for r in rows] == ["web-prod", "2025-intern", "storefront-web-awos"]
    assert [r["last4"] for r in rows] == ["Ab12", "e5f6", ""]           # fully masked -> no last4 check
    assert rows[1]["status"] == "expired" and rows[2]["status"] == "active"


def test_parse_refuses_other_page_partial_or_error():
    assert mw.parse_key_rows(_page(TABLE, url="http://127.0.0.1:1/projects/storefront-web-staging/keys"),
                             "storefront-web") is None                     # look-alike project
    assert mw.parse_key_rows(_page(TABLE, url="http://127.0.0.1:1/projects/storefront-web/keys/new"),
                             "storefront-web") is None                     # stale "Key created" page
    assert mw.parse_key_rows(_page(TABLE, truncated=True, settled=True), "storefront-web") is None
    assert mw.parse_key_rows(_page(TABLE, truncated=False, settled=False), "storefront-web") is None
    assert mw.parse_key_rows(_page(TABLE, status=404), "storefront-web") is None
    assert mw.parse_key_rows(_page("Key created\nCopy this key now."), "storefront-web") is None
    assert mw.parse_key_rows(_page(TABLE, truncated=False, settled=True), "storefront-web")


# ----------------------------------------------- end to end: dd variant c --

LOCAL_LIKE_C = {"sign_in": P.SIGN_IN,
                "create_key": P.create_steps("storefront-web", "Read-only", False)[:-2]
                + ['copy "New API key value" fakeservice_key'] * 5 + ['done "key created and saved"']}


def _demo(tmp_path, monkeypatch, rt, script=LOCAL_LIKE_C):
    monkeypatch.setitem(P.DD_SCRIPTS, "c", script)
    lines = []
    res = P.demo_dd("c", 7, tmp_path / "dd", runtime=rt, vm_check=VM, out=lines.append, settle=1.0)
    return res, lines


@pytest.mark.parametrize("mode,after_create_only", [("empty", True), ("partial", True), ("empty", False),
                                                    ("partial", False)])
def test_dd_c_local_like_run_passes_when_keys_page_builds_late(tmp_path, monkeypatch, mode, after_create_only):
    """The live failure, reproduced: same steps as the local-Qwen run (5x copy_to_secret,
    12/12 steps), the post-create keys page builds late. Before the fix the after-create
    cases ended exactly like dd-20261021-054912 ("completed_unverified: dashboard check:
    could not read the key list of storefront-web ... HALT"); now verified and saved."""
    rt, saf = _world(lag=3.0, mode=mode, after_create_only=after_create_only)
    res, lines = _demo(tmp_path, monkeypatch, rt)
    assert res["pass"], (res.get("reasons"), res.get("error"), lines[-25:])
    assert res["report_status"] == "done"
    assert [s["outcome"] for s in res["subtasks"]] == ["verified", "verified"]
    # with the repeat fix merged, the duplicate copies are skipped and the subtask
    # auto-completes once the success condition holds -> fewer than the 12 live steps
    assert res["subtasks"][1]["steps"] < 12
    actions = [json.loads(x) for x in (tmp_path / "dd/harness/actions.jsonl").read_text().splitlines()]
    assert sum(a["action"] == "create_key" for a in actions) == 1         # exactly one key made
    assert not re.search(r"fk_live_[A-Za-z0-9]{32}", "\n".join(lines))


def test_dd_c_post_create_list_unreadable_still_halts(tmp_path, monkeypatch):
    """The rule stays: if the dashboard list never becomes readable after creating, the
    effect is unproven -> no 'done', nothing written to the .env, one key only."""
    rt, saf = _world(lag=None, mode="partial", after_create_only=True)
    res, lines = _demo(tmp_path, monkeypatch, rt)
    assert not res["pass"] and res["report_status"] != "done"
    assert res["subtasks"][1]["outcome"] != "verified"
    assert "could not read the key list" in res["subtasks"][1]["reason"]
    assert "HALT" in res["subtasks"][1]["reason"] or "reconcile" in res["subtasks"][1]["reason"]
    env = (tmp_path / "dd/sandbox/workspace/storefront-web/.env")
    assert not env.exists() or "FAKESERVICE_API_KEY=fk_live_" not in env.read_text()
    assert sum(1 for u in saf.loads if u.rstrip("/").endswith("/keys/new")) <= 2   # GET form + one POST


def test_dd_c_pre_create_list_unreadable_needs_user_creates_nothing(tmp_path, monkeypatch):
    """Cannot see the existing keys -> do not create blind (no POST, owner asked)."""
    rt, saf = _world(lag=None, mode="partial")
    res, lines = _demo(tmp_path, monkeypatch, rt)
    assert res["report_status"] == "needs_user"
    assert res["subtasks"][1]["outcome"] == "needs_user"
    assert "could not read the key list" in res["subtasks"][1]["reason"]
    assert not saf.created and not any("/keys/new" in u for u, _f in saf.posts)


def test_key_rows_why_names_the_reason_without_values():
    assert mw.key_rows_why(_page(TABLE, truncated=False, settled=True), "storefront-web") == ""
    assert mw.key_rows_why(_page(TABLE, status=404), "storefront-web") == "http 404"
    assert "not /projects/storefront-web/keys" in mw.key_rows_why(
        _page(TABLE, url="http://127.0.0.1:1/projects/storefront-web/keys/new"), "storefront-web")
    assert mw.key_rows_why(_page(TABLE, truncated=False, settled=False), "storefront-web") == "page never settled"
    why = mw.key_rows_why(_page("Key created\nfk_live_…Ab12"), "storefront-web")
    assert why.startswith("no key table header") and "Ab12" not in why


def test_pre_create_needs_user_reason_carries_diagnostic(tmp_path, monkeypatch):
    rt, saf = _world(lag=None, mode="partial")
    res, lines = _demo(tmp_path, monkeypatch, rt)
    assert res["report_status"] == "needs_user"
    reason = res["subtasks"][1]["reason"]
    assert "could not read the key list" in reason and "not risking a duplicate); " in reason
