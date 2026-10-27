"""Live-web harness (scaffold/rl/web) without the network: a static fake site on 127.0.0.1 and a
fake Online_Mind2Web.json. Chromium parts are skipped when Playwright / a Chromium build is missing.

Run:  PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_web_harness.py -q
"""
import html
import json
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scaffold.agent.desktop import micro_agent as MA
from scaffold.agent.desktop.web_tools import parse_page
from scaffold.rl.web import source as S
from scaffold.rl.web.blocked import detect_block

# ------------------------------------------------------------------ fake site --

PRODUCTS = {"mug": "Blue Mug — $12", "lamp": "Desk Lamp — $30"}


def _page(title, body, status=200):
    return status, f"<!doctype html><html><head><title>{html.escape(title)}</title></head><body>{body}</body></html>"


def route(path, query):
    if path == "/":
        return _page("Shop Home", """
<h1>Shop</h1>
<form action="/search" method="get" role="search" aria-label="Site search">
  <input type="search" name="q" aria-label="Search products">
</form>
<a href="/about">About us</a> <a href="/deals">Deals</a>""")
    if path == "/search":
        q = (query.get("q") or [""])[0].strip().lower()
        hits = [v for k, v in PRODUCTS.items() if q and q in k]
        items = "".join(f'<li><a href="/item/{k}">{html.escape(v)}</a></li>' for k, v in PRODUCTS.items()
                        if v in hits)
        return _page(f"Results for {q}", f"<h1>Results for {html.escape(q)}</h1><ul>{items or '<li>No match</li>'}</ul>")
    if path.startswith("/item/"):
        k = path.rsplit("/", 1)[-1]
        return _page(PRODUCTS.get(k, "?"), f"<h1>{html.escape(PRODUCTS.get(k, '?'))}</h1><p>In stock.</p>"
                                          '<button type="button">Add to cart</button>')
    if path == "/about":
        return _page("About us", "<h1>About us</h1><p>We sell mugs and lamps since 1999.</p>")
    if path == "/deals":
        return _page("Deals", "<h1>Deals</h1><p>Nothing on sale today.</p>")
    if path == "/challenge":            # Cloudflare-style interstitial
        return _page("Just a moment...", "<h1>Checking if the site connection is secure</h1>"
                                         '<div id="challenge-form">Verify you are human</div>', 403)
    if path == "/captcha":
        return _page("Security check", "<h1>Please verify you are a human</h1>"
                                       "<p>Press and hold the button to confirm you're not a robot.</p>"
                                       '<button type="button">Press and hold</button>')
    if path == "/forbidden":
        return _page("Access Denied", "<h1>Access Denied</h1><p>You don't have permission to access this server.</p>", 403)
    if path == "/big":
        regions = "".join(f'<section role="region" aria-label="Promo {i}"><p>{"promo text " * 200}</p></section>'
                          for i in range(12))
        links = "".join(f'<a href="/item/mug?{i}">{"Very long product name " * 20} {i}</a><br>' for i in range(400))
        return _page("Big page", f"<h1>Big</h1><p>{'filler words ' * 2000}</p>{regions}{links}")
    if path == "/late":                 # an app shell: load fires at once, the controls come with a slow fetch
        return _page("Late shop", '<div id="app"></div><script>fetch("/js/late.html").then(r => r.text())'
                                  '.then(t => { document.getElementById("app").innerHTML = t; });</script>')
    if path == "/slowres":              # controls rendered by a script that is slow to arrive (load is late)
        return _page("Slow shop", '<div id="app"></div><script async src="/js/slow.js"></script>')
    churn = ('<div id="t"></div><script>setInterval(() => { document.getElementById("t")'
             '.setAttribute("data-x", String(Math.random())); }, 20);</script>')
    if path == "/never":                # never settles: a hung resource holds load, the DOM churns forever
        return _page("", churn + '<img src="/js/hang.png">')
    if path == "/churn":                # loads, never settles (DOM churns), never shows a control
        return _page("", churn)
    return _page("Not found", "<h1>Not found</h1>", 404)


SLOW_S, HANG_S = 2.5, 30.0
SLOW_BODY = {"/js/slow.js": (('document.getElementById("app").innerHTML = \'<a href="/about">Slow about</a> '
                              '<button type="button">Slow button</button>\';'), "application/javascript"),
             "/js/late.html": ('<a href="/deals">Late deals</a> <button type="button">Late button</button>',
                               "text/html")}


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path.startswith("/js/"):              # slow resources (SLOW_BODY) or a hung one
            time.sleep(SLOW_S if u.path in SLOW_BODY else HANG_S)
            body, ctype = SLOW_BODY.get(u.path, ("", "image/png"))
            status = 200
        else:
            status, body = route(u.path, urllib.parse.parse_qs(u.query))
            ctype = "text/html; charset=utf-8"
        raw = body.encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:                 # the browser gave up on a hung resource
            pass

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def fake_entries(base="https://shop.example"):
    lv = ["easy"] * 6 + ["medium"] * 10 + ["hard"] * 4
    return [{"task_id": f"t{i:02d}", "confirmed_task": f"Task number {i}", "website": f"{base}/" if i % 2 else
             base.replace("https://", ""), "reference_length": 3 + i % 5, "level": lv[i]} for i in range(20)]


def write_tasks(tmp_path, entries):
    p = tmp_path / "Online_Mind2Web.json"
    p.write_text(json.dumps(entries))
    return p


# -------------------------------------------------------------- parser / grammar --

def test_new_verbs_parse_and_old_replies_still_parse():
    P = MA.parse_text_action
    a = P('# search it\nfill_enter(3, "blue mug")')
    assert (a.verb, a.n, a.value) == (MA.Verb.FILL_ENTER, 3, "blue mug") and a.show() == 'fill_enter(3, "blue mug")'
    assert P("go_back()").verb is MA.Verb.GO_BACK and P("go_back()").show() == "go_back()"
    g = P('goto("https://en.wikipedia.org/wiki/Main_Page")')
    assert g.verb is MA.Verb.GOTO and g.value == "https://en.wikipedia.org/wiki/Main_Page"
    assert P("fill_enter(2, {{secret:q}})").value == "{{secret:q}}"
    assert P("click(1234)").n == 1234                          # big real pages: 4-digit control numbers
    for old, verb in [("click(3)", MA.Verb.CLICK), ('fill(2, "x")', MA.Verb.FILL), ("fill(2, {{secret:pw}})", MA.Verb.FILL),
                      ('select(4, "Read-only")', MA.Verb.SELECT), ("copy_to_secret(5, key)", MA.Verb.COPY),
                      ("scroll(down)", MA.Verb.SCROLL), ('done("ok")', MA.Verb.DONE),
                      ('needs_user("why")', MA.Verb.NEEDS_USER)]:
        assert P(old).verb is verb
    with pytest.raises(MA.ParseError):
        P("go_back(1)")
    with pytest.raises(MA.ParseError):
        P("fill_enter(3)")
    j = MA.parse_json_action('{"action": "goto", "url": "https://example.org/"}')
    assert j.verb is MA.Verb.GOTO and j.value == "https://example.org/"
    assert MA.parse_json_action('{"action": "fill_enter", "n": 2, "value": "q"}').verb is MA.Verb.FILL_ENTER
    with pytest.raises(MA.ParseError):
        MA.parse_json_action('{"action": "fill_enter", "value": "q"}')


def test_grammar_offers_web_verbs_only_when_asked():
    prod = MA.build_grammar(5, ["pw"], ["key"])
    assert prod == MA.build_grammar(5, ["pw"], ["key"], web=False)
    assert "fill_enter" not in prod and "go_back" not in prod and "goto" not in prod
    web = MA.build_grammar(5, web=True)
    assert "fillenter | scroll | done | needs | goback | goto" in web.replace("action ::= click | fill | select | ", "")
    assert 'goback ::= "go_back()"' in web and 'fillenter ::= "fill_enter(" num ", " str ")"' in web
    assert "fill_enter" not in MA.ACTION_SCHEMA["properties"]["action"]["enum"]    # product JSON prompt unchanged


def test_web_verbs_are_refused_when_not_enabled(site):
    br = MA.LoopbackBrowser(site)
    model = MA.ScriptedModel(["raw go_back()", 'raw goto("https://example.org/")', 'done "x"'])
    agent = MA.MicroAgent(br, model, grounder=MA.IndexGrounder(), store=MA.LocalSecretStore())
    res = agent.run(MA.Subtask(name="t", goal="look", start="/"))
    rej = [e["rejected"] for e in res.events if "rejected" in e]
    assert rej == ["go_back is not available here", "goto is not available here"]
    assert "go_back" not in model.grammars[0] and "fill_enter" not in model.prompts[0]


# -------------------------------------------------------------------- source --

def test_source_loads_a_local_task_file(tmp_path):
    p = write_tasks(tmp_path, fake_entries())
    src = S.OnlineMind2WebSource(str(p), dev_ids_path=str(tmp_path / "none.json"))
    tasks = list(src.tasks("all"))
    assert len(tasks) == 20
    t = tasks[1]
    assert (t.id, t.goal, t.start, t.source, t.split) == ("t01", "Task number 1", "https://shop.example/",
                                                         "online-mind2web", "all")
    assert t.meta == {"level": "easy", "website": "https://shop.example/", "reference_length": 4}
    assert tasks[0].start == "https://shop.example" and t.family == "shop.example" and t.tags == ["easy"]
    assert t.max_steps == 25 and src.reset(t) == t.start
    with pytest.raises(ValueError):
        list(src.tasks("train"))
    with pytest.raises(NotImplementedError):
        src.oracle(t)


def test_dev_split_is_fixed_stratified_and_committed(tmp_path):
    ents = [{"task_id": f"id{i:03d}", "confirmed_task": "x", "website": "a.com", "level": lv}
            for i, lv in enumerate(["easy"] * 83 + ["medium"] * 143 + ["hard"] * 74)]
    ids = S.select_dev(ents)
    assert len(ids) == 100 and ids == sorted(ids) and S.select_dev(list(reversed(ents))) == ids
    lv = {e["task_id"]: e["level"] for e in ents}
    from collections import Counter
    assert Counter(lv[i] for i in ids) == {"easy": 28, "medium": 47, "hard": 25}
    p = write_tasks(tmp_path, ents)
    dev = tmp_path / "dev_ids.json"
    doc = S.freeze_dev(ents, dev, source_file=p.name)
    assert doc["ids"] == ids and doc["by_level"] == {"easy": 28, "hard": 25, "medium": 47}
    src = S.OnlineMind2WebSource(str(p), dev_ids_path=str(dev))
    assert sorted(t.id for t in src.tasks("dev")) == ids and src.dev_ids_source == "committed:dev_ids.json"
    dev.write_text(json.dumps({"ids": ["nope"]}))
    with pytest.raises(ValueError):
        list(S.OnlineMind2WebSource(str(p), dev_ids_path=str(dev)).tasks("dev"))
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"ids": []}))
    src2 = S.OnlineMind2WebSource(str(p), dev_ids_path=str(empty))
    assert sorted(t.id for t in src2.tasks("dev")) == ids and src2.dev_ids_source == "computed"


def test_committed_dev_file_is_well_formed():
    d = json.loads(S.DEV_IDS.read_text())
    assert d["revision"] == S.DATASET_REVISION and d["seed"] == S.DEV_SEED
    assert d["size"] == len(d["ids"]) and (not d["ids"] or len(d["ids"]) == S.DEV_SIZE)


def test_download_needs_a_token(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="gated"):
        S.download(tmp_path / "x.json")


# --------------------------------------------------------------- url policy --

def test_web_url_policy():
    from scaffold.rl.harness.chromium import NotWebURL, check_web_url
    assert check_web_url("https://en.wikipedia.org/wiki/X")
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "chrome://settings",
                "http://127.0.0.1:8080/v1", "http://localhost/", "http://192.168.1.1/", "http://10.0.0.2/",
                "http://169.254.169.254/latest/meta-data"):
        with pytest.raises(NotWebURL):
            check_web_url(bad)
    assert check_web_url("http://127.0.0.1:9/", allow_private=True)
    with pytest.raises(NotWebURL):
        check_web_url("file:///x", allow_private=True)


# ---------------------------------------------------------- blocked detection --

def _p(path, status=200):
    st, body = route(path, {})
    return parse_page("https://site.example" + path, status if status != 200 else st, body)


def test_detect_block_kinds():
    assert detect_block(_p("/challenge"))["kind"] == "cloudflare"
    assert detect_block(_p("/captcha"))["kind"] == "captcha"
    b = detect_block(_p("/forbidden"))
    assert b["kind"] == "bot_wall" and b["status"] == 403
    assert detect_block(_p("/deals"), ["cloudflare"])["kind"] == "cloudflare"
    assert detect_block(_p("/"), start=True) is None and detect_block(_p("/about")) is None
    nf = _p("/missing")
    assert detect_block(nf, start=True)["kind"] == "http_404" and detect_block(nf) is None   # mid-episode: the model's 404
    assert detect_block(_p("/deals", status=429))["kind"] == "http_429"
    article = parse_page("https://en.wikipedia.org/wiki/CAPTCHA", 200,
                         "<title>CAPTCHA - Wikipedia</title><h1>CAPTCHA</h1><p>" + "A CAPTCHA is a test. " * 400 + "</p>")
    assert detect_block(article) is None                      # an article about CAPTCHAs is not a wall
    assert detect_block(_p("/deals"), ["captcha_widget"]) is not None     # widget on a near-empty page
    assert detect_block(_p("/big"), ["captcha_widget"]) is None           # invisible-badge sites stay usable


def test_clip_page_bounds_the_prompt():
    from scaffold.rl.web.episode import clip_page
    p = clip_page(_p("/big"))
    assert max(len(c.name) for c in p.controls) <= 150 and len(p.regions) <= 6
    assert all(len(r) <= 600 for r in p.regions)


# ----------------------------------------------------------- judge layout (unit) --

def test_judge_layout_from_a_synthetic_trajectory(tmp_path):
    from scaffold.rl.contract import Step, TaskSpec, Trajectory
    from scaffold.rl.web.episode import validate_judge_task, write_judge_task
    t = TaskSpec(id="abc123", source="online-mind2web", family="x", split="dev", goal="Find the mug price",
                 start="https://shop.example/", meta={"reference_length": 4})
    tr = Trajectory(task_id="abc123", model="m", outcome="completed_unverified",
                    steps=[Step(1, "s", "u", None, '# search\nfill_enter(1, "mug")', 'fill_enter(1, "mug")', True,
                                "https://shop.example/"),
                           Step(2, "s", "u", None, "click(9)", "click(9)", True, "https://shop.example/search"),
                           Step(3, "s", "u", None, 'done("$12")', 'done("$12")', True, "https://shop.example/item")],
                    events=[{"step": 1, "action": 'fill_enter(1, "mug")', "control": 'textbox "Search"'},
                            {"step": 2, "action": "click(9)", "rejected": "control 9 is not on this page"},
                            {"step": 3, "action": 'done("$12")'},
                            {"event": "harness", "answer": "$12", "final_url": "https://shop.example/item",
                             "blocked": None}])
    shots = {}
    for k in ("s0", "s1", "s2", "final"):
        f = tmp_path / f"{k}.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + k.encode())
        shots[k] = f
    write_judge_task(tmp_path / "abc123", t, tr, shots)
    assert validate_judge_task(tmp_path / "abc123") == []
    d = json.loads((tmp_path / "abc123" / "result.json").read_text())
    acts = [s["action"] for s in d["action_history"]]
    assert acts[0].startswith("page -> NAVIGATE -> open the task's start page https://shop.example/")
    assert acts[1] == 'textbox "Search" -> TYPE -> fill_enter(1, "mug") then press Enter | SUCCESS'
    assert acts[2].startswith("page -> CLICK -> click(9) (rejected:") and acts[2].endswith("| FAILED")
    assert acts[3] == "TASK_COMPLETE -> ANSWER: $12" and d["agent_final_answer"] == "$12"
    assert d["reference_length"] == 4 and d["action_history"][1]["thought"] == "search"
    assert d["harness_status"] == "ok" and d["level"] is None
    assert [s["screenshot"] for s in d["action_history"]] == ["0000.png", "0001.png", "0002.png", "0003.png"]
    # each step shows the page AFTER its action: step 1 (fill_enter) -> the shot taken before call 2
    assert (tmp_path / "abc123/trajectory/0001.png").read_bytes().endswith(b"s1")
    assert (tmp_path / "abc123/trajectory/0003.png").read_bytes().endswith(b"final")


# ------------------------------------------------------------- Chromium e2e --

def _chromium_ok():
    try:
        import playwright  # noqa: F401
        from scaffold.rl.harness.chromium import find_chromium
        return bool(find_chromium())
    except ImportError:
        return False


needs_chromium = pytest.mark.skipif(not _chromium_ok(), reason="Playwright / Chromium build missing")


@pytest.fixture(scope="module")
def webchrome():
    if not _chromium_ok():
        pytest.skip("Playwright / Chromium build missing")
    from scaffold.rl.harness.chromium import Chromium
    ch = Chromium(web=True)
    yield ch
    ch.close()


def _spec(site, path, script, tid="e2e", rl=5):
    return S.to_spec({"task_id": tid, "confirmed_task": "Find the price of the blue mug", "website": site + path,
                      "level": "easy", "reference_length": rl, "script": script}, "dev")


def _png_size(p: Path):
    raw = p.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", raw[16:24])


@needs_chromium
def test_new_actions_end_to_end_in_chromium(site, webchrome, tmp_path):
    from scaffold.rl.web.episode import run_web_episode, validate_judge_task
    script = ['fill_enter "Search products" "mug"', 'click "Blue Mug — $12"', "go_back",
              f'goto "{site}/about"', 'scroll down', 'done "The blue mug costs $12"']
    task = _spec(site, "/", script)
    traj, score = run_web_episode(task, MA.ScriptedModel(script), model_label="scripted", chromium=webchrome,
                                  judge_root=tmp_path / "judge", allow_private=True)
    h = traj.events[-1]
    assert traj.error == "" and h["blocked"] is None, (traj.error, h.get("traceback"))
    assert [s.action for s in traj.steps] == ['fill_enter(1, "mug")', "click(1)", "go_back()",
                                              f'goto("{site}/about")', "scroll(down)",
                                              'done("The blue mug costs $12")']
    assert [s.url.replace(site, "") for s in traj.steps] == ["/", "/search?q=mug", "/item/mug", "/search?q=mug",
                                                            "/about", "/about"]
    assert [x.split(" ->")[0] for x in h["browser_log"]] == ["GET /", "FILL textbox", "ENTER textbox",
                                                             "CLICK link", "BACK", "GET /about"]
    rej = [e for e in traj.events if "rejected" in e]
    assert [e["step"] for e in rej] == [5]                    # scroll on a short page: "nothing more to scroll"
    assert h["answer"] == "The blue mug costs $12" and traj.outcome == "completed_unverified"
    assert score.success is False and score.notes.startswith("unjudged")
    tdir = tmp_path / "judge" / "e2e"
    assert validate_judge_task(tdir) == []
    d = json.loads((tdir / "result.json").read_text())
    assert len(d["action_history"]) == 7 and d["agent_final_answer"] == "The blue mug costs $12"
    verbs = [s["action"].split(" -> ")[1] for s in d["action_history"][:-1]]
    assert verbs == ["NAVIGATE", "TYPE", "CLICK", "GO_BACK", "NAVIGATE", "SCROLL"]
    assert all(_png_size(tdir / "trajectory" / s["screenshot"]) == (1280, 800) for s in d["action_history"])
    assert not (tdir / ".raw").exists() and h["max_prompt_tokens_est"] < 6000
    # the system prompt and grammar of a web episode carry the extra verbs
    assert "fill_enter" in traj.steps[0].system and "goback" in traj.steps[0].grammar


@needs_chromium
def test_private_hosts_are_refused_without_the_test_flag(site, webchrome, tmp_path):
    from scaffold.rl.harness.chromium import ChromiumBackend, NotWebURL
    with pytest.raises(NotWebURL):
        ChromiumBackend(site, chromium=webchrome, web=True)
    b = ChromiumBackend(site, chromium=webchrome, web=True, allow_private=True)
    try:
        b.open(site + "/")
        with pytest.raises(MA.WebError):
            b.goto("file:///etc/passwd")
    finally:
        b.close()
    from scaffold.rl.harness.chromium import Chromium
    with pytest.raises(ValueError):                          # the loopback-only browser cannot do web mode
        ChromiumBackend("https://example.org", chromium=type("C", (), {"web": False, "browser": None})(), web=True)
    assert Chromium is not None


@needs_chromium
@pytest.mark.parametrize("path,kind", [("/challenge", "cloudflare"), ("/captcha", "captcha"),
                                       ("/forbidden", "bot_wall"), ("/missing", "http_404")])
def test_blocked_start_pages_stop_before_the_model(site, webchrome, tmp_path, path, kind):
    from scaffold.rl.web.episode import run_web_episode, validate_judge_task
    task = _spec(site, path, ['done "x"'], tid=f"blk{kind}")
    model = MA.ScriptedModel(['done "x"'])
    traj, score = run_web_episode(task, model, model_label="scripted", chromium=webchrome,
                                  judge_root=tmp_path / "judge", allow_private=True)
    h = traj.events[-1]
    assert h["blocked"]["kind"] == kind and h["stop_by"] == f"blocked:{kind}"
    assert traj.steps == [] and model.prompts == [] and traj.outcome == "needs_user" and traj.error == ""
    assert score.success is False and score.notes == f"blocked:{kind}"
    tdir = tmp_path / "judge" / task.id
    assert validate_judge_task(tdir) == []
    d = json.loads((tdir / "result.json").read_text())
    assert d["harness_status"] == "blocked" and d["harness_note"].startswith(kind) and d["level"] == "easy"
    assert d["agent_final_answer"] is None and d["action_history"][-1]["action"].startswith(
        f"TASK_COMPLETE -> STOPPED: blocked page ({kind})")


@needs_chromium
def test_blocked_mid_episode_and_load_failure(site, webchrome, tmp_path):
    from scaffold.rl.web.episode import run_web_episode
    script = [f'goto "{site}/challenge"', 'done "x"']
    traj, _ = run_web_episode(_spec(site, "/", script, tid="mid"), MA.ScriptedModel(script), model_label="s",
                              chromium=webchrome, judge_root=tmp_path / "judge", allow_private=True)
    assert traj.events[-1]["blocked"]["kind"] == "cloudflare" and len(traj.steps) == 1
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    s.close()
    traj, score = run_web_episode(_spec(dead, "/", ['done "x"'], tid="dead"), MA.ScriptedModel(['done "x"']),
                                  model_label="s", chromium=webchrome, judge_root=tmp_path / "judge",
                                  allow_private=True)
    assert traj.events[-1]["blocked"]["kind"] == "load_failed" and score.notes == "blocked:load_failed"


@needs_chromium
def test_runner_cli_writes_trajectories_judge_layout_and_summary(site, tmp_path):
    import os
    import subprocess
    import sys
    from scaffold.rl.web.runner import main as main_inproc

    def main(argv):          # the CLI in its own process (one sync Playwright per thread)
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(root))
        return subprocess.run([sys.executable, "-m", "scaffold.rl.web", *argv], cwd=root, env=env,
                              capture_output=True, text=True, timeout=300).returncode
    ents = [{"task_id": "ok1", "confirmed_task": "Find the price of the blue mug", "website": site + "/",
             "level": "easy", "reference_length": 4,
             "script": ['fill_enter "Search products" "mug"', 'done "$12"']},
            {"task_id": "cf1", "confirmed_task": "Anything", "website": site + "/challenge", "level": "hard",
             "reference_length": 6, "script": ['done "x"']}]
    tj = write_tasks(tmp_path, ents)
    out = tmp_path / "run"
    argv = ["run", "--model", "scripted", "--split", "all", "--tasks-json", str(tj), "--allow-private",
            "--out", str(out)]
    assert main(argv) == 0
    rows = [json.loads(x) for x in (out / "trajectories.jsonl").read_text().splitlines()]
    assert sorted(r["task"]["id"] for r in rows) == ["cf1", "ok1"]
    summ = json.loads((out / "summary.json").read_text())
    o = summ["overall"]
    assert o["episodes"] == 2 and o["answered"] == 1 and o["blocked"] == 1
    assert o["blocked_by_kind"] == {"cloudflare": 1} and summ["blocked_tasks"][0]["id"] == "cf1"
    assert sorted(p.name for p in (out / "judge").iterdir()) == ["cf1", "ok1"]   # only task folders
    assert main_inproc(["validate", str(out / "judge")]) == 0
    assert main(argv + ["--shard", "1/2"]) == 0                # resumable: nothing left to run
    assert len((out / "trajectories.jsonl").read_text().splitlines()) == 2


@needs_chromium
def test_big_real_page_prompt_stays_under_budget(site, webchrome, tmp_path):
    from scaffold.rl.web.episode import run_web_episode
    script = ["scroll down", "scroll down", 'done "x"']
    traj, _ = run_web_episode(_spec(site, "/big", script, tid="big"), MA.ScriptedModel(script), model_label="s",
                              chromium=webchrome, judge_root=tmp_path / "judge", allow_private=True)
    h = traj.events[-1]
    assert traj.error == "" and h["blocked"] is None
    assert len(traj.steps) == 3 and h["max_prompt_tokens_est"] < 6000, h["max_prompt_tokens_est"]
    assert "showing 41-80" in traj.steps[1].user and "showing 81-120" in traj.steps[2].user


# ------------------------------------------------------- settle before reading --

@needs_chromium
@pytest.mark.parametrize("path,names", [("/late", ["Late deals", "Late button"]),
                                        ("/slowres", ["Slow about", "Slow button"])])
def test_first_observation_waits_for_late_controls(site, webchrome, tmp_path, path, names):
    """The model's first prompt has the controls a page renders late: an app shell whose controls come
    with a slow fetch (load fired, the page is not idle, the first read is empty), or a slow async
    script (load comes after settle_timeout)."""
    from scaffold.rl.web.episode import run_web_episode
    model = MA.ScriptedModel(['done "x"'])
    traj, _ = run_web_episode(_spec(site, path, ['done "x"'], tid="late" + path.strip("/")), model,
                              model_label="s", chromium=webchrome, judge_root=tmp_path / "judge",
                              allow_private=True, backend_kw={"settle_timeout": 1.0})
    h = traj.events[-1]
    assert traj.error == "" and h["blocked"] is None, (traj.error, h.get("blocked"))
    first = traj.steps[0].user
    assert all(n in first for n in names), first
    op = h["timings"][0]
    assert op["op"] == "open" and 1000 < op["settle_ms"] < 8000 + 500
    s = h["settle"]
    assert s["ops"] == len(h["timings"]) and s["total_ms"] >= op["settle_ms"]
    if path == "/late":
        assert op.get("reread") == 1 and s["rereads"] >= 1    # read empty once, then again with controls
    else:
        assert op.get("load_wait_ms") and s["load_waits"] >= 1 and "reread" not in op


@needs_chromium
@pytest.mark.parametrize("path", ["/never", "/churn"])
def test_a_page_that_never_settles_is_read_within_the_cap(site, webchrome, monkeypatch, path):
    """/never: load never fires (hung image), the load wait takes the whole cap. /churn: loads, never
    idle, never shows a control: the empty-page wait and the re-read stay inside the cap."""
    from scaffold.rl.harness.chromium import ChromiumBackend
    monkeypatch.setenv("AWOS_WEB_SETTLE_S", "2.5")
    b = ChromiumBackend(site, chromium=webchrome, web=True, allow_private=True, settle_timeout=1.0)
    try:
        assert b.web_cap == 2.5
        t0 = time.monotonic()
        p = b.open(site + path)
        took = time.monotonic() - t0
        op = b.timings[-1]
        assert p.controls == [] and p._live["settled"] is False
        assert 2500 - 300 <= op["settle_ms"] <= 2500 + 300 and took < 2.5 + 2.0, (op, took)
        if path == "/never":
            assert op.get("load_wait_ms", 0) > 1000
        else:
            assert op.get("reread") == 1 and "load_wait_ms" not in op
    finally:
        b.close()


@needs_chromium
def test_settle_cap_zero_is_the_old_read(site, webchrome):
    from scaffold.rl.harness.chromium import ChromiumBackend
    off = ChromiumBackend(site, chromium=webchrome, web=True, allow_private=True, settle_timeout=1.0,
                          web_settle_cap=0)
    try:                                                     # 0 = settle_timeout only, read what is there
        p = off.open(site + "/late")
        assert off.web_cap == 0 and "reread" not in off.timings[-1]
        assert "Late button" not in [c.name for c in p.controls]
    finally:
        off.close()


def test_settle_cap_env_and_summary(monkeypatch):
    from scaffold.rl.harness import chromium as C
    from scaffold.rl.web.episode import settle_summary
    monkeypatch.delenv("AWOS_WEB_SETTLE_S", raising=False)
    assert C.default_web_settle_cap() == C.WEB_SETTLE_CAP_S == 8.0
    for raw, want in (("3", 3.0), ("0", 0.0), ("-1", 0.0), ("junk", C.WEB_SETTLE_CAP_S)):
        monkeypatch.setenv("AWOS_WEB_SETTLE_S", raw)
        assert C.default_web_settle_cap() == want
    tm = [{"op": "open", "settle_ms": 3000.0, "load_wait_ms": 900.0, "reread": 1},
          {"op": "click", "settle_ms": 120.0}]
    assert settle_summary(tm) == {"ops": 2, "total_ms": 3120.0, "max_ms": 3000.0, "load_waits": 1, "rereads": 1}
    assert settle_summary([]) == {"ops": 0, "total_ms": 0.0, "max_ms": 0.0, "load_waits": 0, "rereads": 0}


# --------------------------------------------------------------------- CI --

def test_ci_matrix_env_and_workflow():
    import importlib.util
    import re
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("web_matrix", root / "scripts/rl/web_matrix.py")
    wm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wm)
    env = wm.parse_env(root / "ci/rl_web.env")
    specs = wm.model_specs(wm.parse_env(root / "ci/rl_eval.env"))
    matrix, models = wm.build(env, specs)
    want = env["MODELS"].split()
    assert [m["model"] for m in models["include"]] == want
    assert len(matrix["include"]) == len(want) * int(env["SHARDS"])
    assert {j["split"] for j in matrix["include"]} == {"dev"}
    m2, _ = wm.build(env, specs, models="qwen3.5-2b", split="all", shards="3")
    assert [(j["shard"], j["shards"], j["split"]) for j in m2["include"]] == [(1, 3, "all"), (2, 3, "all"),
                                                                             (3, 3, "all")]
    with pytest.raises(SystemExit):
        wm.build(env, specs, split="train")
    text = (root / "ci/rl_web.env").read_text()
    assert not re.search(r"hf_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{10,}|TOKEN=", text)      # no secrets in the env file
    wf = (root / ".github/workflows/rl-web.yml").read_text()
    assert '"bench-cloud/rl-web-**"' in wf and "max-parallel: 12" in wf and "secrets.HF_TOKEN" in wf
    assert "playwright install --with-deps chromium" in wf and '--shard "$SHARD/$SHARDS"' in wf
    ladder = (root / ".github/workflows/ladder.yml").read_text()
    assert '"!bench-cloud/rl-**"' in ladder                  # rl-web pushes never start the paid ladder


def test_model_server_down_is_an_infra_error_rerun_on_resume(tmp_path):
    """A model server stopped mid-run (the agent ends needs_user "model call failed: URLError") is infra:
    resume reruns it and the summary keeps it out of the scores, also for records written before the check."""
    from scaffold.rl.web import runner as R
    p = tmp_path / "trajectories.jsonl"
    rows = [{"task": {"id": "a"}, "trajectory": {"outcome": "needs_user", "reason": "model call failed: URLError"}},
            {"task": {"id": "b"}, "trajectory": {"outcome": "needs_user", "reason": "model call failed: ValueError"}},
            {"task": {"id": "c"}, "trajectory": {"outcome": "completed_unverified", "reason": "done"}}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    recs = {r["task"]["id"]: r["trajectory"] for r in R.read_web_records(p)}
    assert recs["a"]["error"].startswith("model server unreachable")
    assert not recs["b"].get("error") and not recs["c"].get("error")
    assert R.server_down("model call failed: ConnectionRefusedError") and not R.server_down("why")
