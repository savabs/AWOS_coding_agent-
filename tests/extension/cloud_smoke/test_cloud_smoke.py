"""AWOS Replay on REAL public websites (CI: .github/workflows/replay-cloud-smoke.yml).

Skipped unless AWOS_REPLAY_SMOKE=1. Needs AWOS_REAL_MODEL_URL (a loopback llama-server serving the G7 2B GGUF)
for the repair model and the verifier; without it the run still goes, and any model call fails as "unreachable".

Each chore is scripted as a person doing it once in headless Chromium while the unpacked extension records
(the same worker-handler driver as tests/extension/test_extension_replay.py), saved with a goal + expect_text
checks (+ expect_download), then REPLAYED once in a fresh tab. Per chore we keep the verdict (VERIFIED / CHECK /
UNVERIFIED / FAILED, or the pause state), how each step's target was found (locator score vs model repair),
the verifier's opinion, the run log and the recording. A "drift" chore replays a copy of the Hacker News
recording whose click target was edited (name / text / href) to force the model-repair path on a live page.

Read-only browsing a person would do: no logins, nothing submitted but public search terms, no bot-check
workarounds, a pause of a few seconds between actions, one recording + one replay per site.

The tests assert only that each replay reaches an end state; the verdicts are the data (set
AWOS_REPLAY_SMOKE_STRICT=1 to also require VERIFIED). Output (AWOS_REPLAY_SMOKE_OUT, default runs/replay-smoke):
results.json, summary.md (the table), <chore>.json (recording + run).
Run:  AWOS_REPLAY_SMOKE=1 AWOS_REAL_MODEL_URL=http://127.0.0.1:8080/v1 PYTHONPATH=.:tests/extension \
      python -m pytest tests/extension/cloud_smoke -q -s -p no:cacheprovider
"""
import copy
import json
import os
import pathlib
import re
import time
import traceback

import pytest

pytestmark = [pytest.mark.timeout(600),
              pytest.mark.skipif(os.environ.get("AWOS_REPLAY_SMOKE") != "1", reason="AWOS_REPLAY_SMOKE=1 not set")]

MODEL_URL = os.environ.get("AWOS_REAL_MODEL_URL", "http://127.0.0.1:8080/v1")
OUT = pathlib.Path(os.environ.get("AWOS_REPLAY_SMOKE_OUT", "runs/replay-smoke"))
STRICT = os.environ.get("AWOS_REPLAY_SMOKE_STRICT") == "1"
PAUSE = float(os.environ.get("AWOS_REPLAY_SMOKE_PAUSE", "2.5"))     # polite pacing between person actions
RESULTS = []


def _pause(k=1.0):
    time.sleep(PAUSE * k)


# ---- the person's side: one function per chore, returns what to save on the recording ---------------------

def wikipedia(page):
    page.goto("https://en.wikipedia.org/wiki/Main_Page", wait_until="domcontentloaded")
    yield
    _pause()
    page.fill("#searchInput", "Alan Turing")
    _pause()
    page.click("#searchform button")
    page.wait_for_url(re.compile(r"/wiki/Alan_Turing|search=Alan"), timeout=30000)
    yield {"goal": "Open the Wikipedia article about Alan Turing",
           "texts": ["Alan Turing", "mathematician"]}


def mdn(page):
    page.goto("https://developer.mozilla.org/en-US/", wait_until="domcontentloaded")
    yield
    _pause()
    page.get_by_role("button", name="Search").first.click()
    _pause(0.5)
    page.get_by_role("combobox", name="Search").first.fill("Array.prototype.map")   # inside a shadow root
    _pause()
    page.get_by_role("option", name=re.compile(r"Array\.prototype\.map\(\)$")).first.click()   # a suggestion, also in the shadow root
    page.wait_for_url(re.compile(r"/Array/map"), timeout=30000)
    yield {"goal": "Find the MDN reference page for Array.prototype.map()",
           "texts": ["Array.prototype.map()", "callbackFn"]}


def python_docs(page):
    """(PyPI's search answers headless browsers with a "Client Challenge" bot check: not worked around, so the
    package-search chore runs on docs.python.org, whose search results are rendered by JavaScript.)"""
    page.goto("https://docs.python.org/3/", wait_until="domcontentloaded")
    yield
    _pause()
    page.locator("form.inline-search input[name=q]:visible").first.fill("json")
    _pause()
    page.locator("form.inline-search input[type=submit]:visible").first.click()
    page.wait_for_url(re.compile(r"search\.html"), timeout=30000)
    link = page.locator("#search-results a[href*='library/json.html']").first
    link.wait_for(timeout=60000)
    _pause()
    link.click()
    page.wait_for_url(re.compile(r"library/json\.html"), timeout=30000)
    yield {"goal": "Find the json module page in the Python docs",
           "texts": ["JSON encoder and decoder"]}


def hackernews(page):
    page.goto("https://news.ycombinator.com/", wait_until="domcontentloaded")
    yield
    _pause()
    page.click(".pagetop a[href='newest']")
    page.wait_for_url(re.compile(r"/newest"), timeout=30000)
    yield {"goal": "Open the Hacker News page of the newest stories",
           "texts": ["Hacker News"]}


def github_release(page):
    page.goto("https://github.com/jqlang/jq", wait_until="domcontentloaded")
    yield
    _pause()
    page.click("a[href^='/jqlang/jq/releases/tag/']")
    page.wait_for_url(re.compile(r"/releases/tag/"), timeout=30000)
    _pause()
    for _ in range(6):                             # the asset list is a lazy fragment: it loads when scrolled to
        page.mouse.wheel(0, 1200)
        time.sleep(1)
    asset = page.locator("a[href*='/releases/download/'][href$='sha256sum.txt']").first
    asset.scroll_into_view_if_needed(timeout=30000)
    _pause(0.5)
    with page.expect_download(timeout=60000):
        asset.click()
    yield {"goal": "Download the sha256sum.txt checksums of the latest jq release",
           "texts": ["Assets", "sha256sum.txt"], "urlPattern": "https://github.com/jqlang/jq/releases/tag/*",
           "download": r"sha256sum\.txt$"}


def openstreetmap(page):
    page.goto("https://www.openstreetmap.org/", wait_until="domcontentloaded")
    yield
    _pause()
    page.click("a[href='/copyright']")
    page.wait_for_url(re.compile(r"/copyright"), timeout=30000)
    yield {"goal": "Read the OpenStreetMap copyright and licence page",
           "texts": ["Open Database License"]}


CHORES = {"wikipedia": wikipedia, "mdn": mdn, "python_docs": python_docs, "hackernews": hackernews,
          "github_release": github_release, "openstreetmap": openstreetmap}


# ---- the extension's side ----------------------------------------------------------------------------------

@pytest.fixture()
def xs(x):
    x.set_settings(endpoint=MODEL_URL, model="local", timeoutMs=120000, verifyTimeoutMs=180000, findTimeoutMs=8000)
    return x


def _fresh_tab(x):
    return x.sw.evaluate("chrome.tabs.create({url: 'about:blank', active: true}).then(t => t.id)")


def _record(x, name, script):
    """drive the person's script while recording -> (saved recording, record seconds)."""
    page = x.ctx.new_page()
    t0 = time.time()
    gen = script(page)
    next(gen)                                      # on the start page
    page.wait_for_load_state("load", timeout=30000)
    x.call("start_recording", tabId=x.tab_id(page), name=name)
    time.sleep(0.5)
    meta = next(gen)
    time.sleep(1.5)                                # last navigation reaches the worker
    rec = x.call("stop_recording")
    pat = meta.get("urlPattern", rec["expect"]["urlPattern"])
    rec = x.call("save_recording", id=rec["id"], goal=meta["goal"], expect={"urlPattern": pat, "texts": meta["texts"]},
                 expectDownload={"nameRegex": meta["download"]} if meta.get("download") else None)
    page.close()
    return rec, round(time.time() - t0, 1)


def _replay(x, rec_id, timeout=420):
    tab = _fresh_tab(x)
    t0 = time.time()
    x.call("run", recId=rec_id, tabId=tab)
    run = x.wait_run(statuses=("done", "failed", "awaiting_approval", "needs_user"), timeout=timeout)
    secs = round(time.time() - t0, 1)
    if run["status"] in ("awaiting_approval", "needs_user"):        # never approve anything on a real site
        x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])
    return run, secs


def _how(r):
    h = r["how"]
    if h.startswith("score"):
        return "locator " + h.replace("score ", "")
    return "MODEL " + h.replace("model pick ", "pick ")


def _summarise(name, rec, run, rec_secs, secs, note=""):
    res = (run or {}).get("result") or {}
    verdict = res.get("verdict") or (run or {}).get("status", "?")
    if run and run["status"] in ("awaiting_approval", "needs_user"):
        verdict = "PAUSED (" + run["status"] + ")"
    steps = [s for s in rec["steps"]] if rec else []
    found = []
    for r in (run or {}).get("resolved", []):
        st = steps[r["step"]] if r["step"] < len(steps) else {}
        found.append(f"{r['step'] + 1}. {st.get('action', '?')} \"{(r.get('name') or '')[:40]}\" via {_how(r)}")
    v = (run or {}).get("verifier") or {}
    if steps and not note and all(s["action"] == "goto" for s in steps):
        note = "recorder captured no click / fill (only page URLs): the replay just reopens the last URL"
    row = {"chore": name, "verdict": verdict, "code": res.get("code", ""), "reason": res.get("reason", ""),
           "steps": len(steps), "actions": [s["action"] for s in steps], "found": found,
           "model_calls": (run or {}).get("modelCalls", []),
           "verifier": (v.get("label", "") + ": " + v.get("reason", "")) if v.get("label") else v.get("error", ""),
           "checks": res.get("checks", []), "record_s": rec_secs, "replay_s": secs, "note": note,
           "log": [l["text"] for l in (run or {}).get("log", [])]}
    RESULTS.append(row)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"summary": row, "recording": rec, "run": run}, indent=2, default=str))
    print(f"\n== {name}: {verdict} {res.get('code', '')} {res.get('reason', '')}\n   " + "\n   ".join(found + row["log"]))
    return row


def _check(row, run):
    assert run is not None and run["status"] in ("done", "failed", "awaiting_approval", "needs_user"), row
    if STRICT:
        assert row["verdict"] == "VERIFIED", row


@pytest.mark.parametrize("name", list(CHORES))
def test_chore(xs, name):
    rec, run, rec_secs, secs, note = None, None, 0, 0, ""
    try:
        rec, rec_secs = _record(xs, name, CHORES[name])
        _pause()
        run, secs = _replay(xs, rec["id"])
    except Exception as e:
        note = "harness: " + "".join(traceback.format_exception_only(type(e), e)).strip()[:400]
        if xs.state()["recState"]:                # the person's script broke mid-recording: keep what it got
            rec = xs.call("stop_recording")
    row = _summarise(name, rec, run, rec_secs, secs, note)
    assert not note, note
    _check(row, run)


def test_drift_model_repair(xs):
    """Hacker News recording, then the click step's stored target edited as if the site renamed it:
    "new" -> "newest stories", href newest -> latest, no id / css. Scoring cannot anchor on it, so the
    replay must ask the local model over the shortlist of the live page."""
    rec, run, rec_secs, secs, note = None, None, 0, 0, ""
    try:
        base, rec_secs = _record(xs, "drift-src", hackernews)
        all_ = xs.sw.evaluate("chrome.storage.local.get('recordings').then(o => o.recordings)")
        rec = copy.deepcopy(all_[base["id"]])
        rec["id"], rec["name"] = base["id"] + "-drift", "hackernews_drift"
        k = next(i for i, s in enumerate(rec["steps"]) if s["action"] == "click")
        loc = rec["steps"][k]["loc"]
        loc.update({"name": "newest stories", "text": "newest stories", "href": "latest", "id": "", "css": ""})
        all_[rec["id"]] = rec
        xs.sw.evaluate("(r) => chrome.storage.local.set({recordings: r})", all_)
        _pause()
        run, secs = _replay(xs, rec["id"])
    except Exception as e:
        note = "harness: " + "".join(traceback.format_exception_only(type(e), e)).strip()[:400]
    row = _summarise("hackernews_drift", rec, run, rec_secs, secs, note)
    assert not note, note
    _check(row, run)


@pytest.fixture(scope="module", autouse=True)
def _write_summary():
    yield
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2, default=str))
    lines = ["| chore | verdict | steps | how each step was found | verifier | record s | replay s |",
             "|---|---|---|---|---|---|---|"]
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    for r in RESULTS:
        verdict = r["verdict"] + (f" {r['code']}" if r["code"] and r["code"] not in r["verdict"] else "")
        why = " ".join(w for w in (r["reason"], r["note"]) if w)
        lines.append(f"| {esc(r['chore'])} | **{esc(verdict)}**{'<br>' + esc(why[:200]) if why else ''} | "
                     f"{r['steps']} ({esc(' '.join(r['actions']))}) | {esc('<br>'.join(r['found'])) or '-'} | "
                     f"{esc(r['verifier'][:160]) or '-'} | {r['record_s']} | {r['replay_s']} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
