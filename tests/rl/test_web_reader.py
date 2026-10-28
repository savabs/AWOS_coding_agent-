"""The product's page reader on real page shapes (docs/specs/web_data.md, "Gaps in the product's
observation"): controls inside headings, ARIA options / menu items / grid cells / tree items and
JS-clickable elements, very deep pages, unnamed icon controls, look-alike controls, and the re-read
after typing. Each fixture is a local HTML page served on 127.0.0.1 and run end to end through the
live-web harness (scaffold/rl/web/episode.run_web_episode: ChromiumBackend -> axmap -> driver.read_web
-> MicroAgent with the real WebAXGrounder). No network beyond localhost.

Run:  PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_web_reader.py -q
"""
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scaffold.agent.desktop import micro_agent as MA
from scaffold.agent.desktop.live.driver import read_web, to_micro_page
from scaffold.rl.harness.axmap import clickable_ids, icon_words, snapshot_from_cdp
from scaffold.rl.web import source as S

STATUS = '<div role="status" id="st"></div>'
SAY = "function say(t) { document.getElementById('st').textContent = t; }"

PAGES = {
    # 1. links inside headings
    "/heading": f"""<h1>Search results</h1>
<h2><a href="/heading/mug">Blue Mug</a></h2><p>A sturdy mug.</p>
<h2><a href="/heading/lamp">Desk Lamp</a> <button type="button" onclick="say('saved lamp')">Save</button></h2>
<h2>Plain heading</h2>{STATUS}<script>{SAY}</script>""",
    "/heading/mug": "<h1>Blue Mug</h1><p>Price: $12</p>",
    # 2. custom combobox: options appear after typing (debounced), picked on mousedown
    "/combo": f"""<h1>Pick a city</h1>
<label for="city">City</label>
<input id="city" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="lb">
<ul role="listbox" id="lb" aria-label="Suggestions"></ul>{STATUS}
<script>{SAY}
const CITIES = ['New York', 'Newark', 'Boston'];
const inp = document.getElementById('city'), lb = document.getElementById('lb');
let t = null;
inp.addEventListener('input', () => {{
  clearTimeout(t);
  t = setTimeout(() => {{
    const q = inp.value.toLowerCase();
    lb.innerHTML = '';
    for (const c of CITIES.filter(c => q && c.toLowerCase().startsWith(q))) {{
      const li = document.createElement('li');
      li.setAttribute('role', 'option'); li.textContent = c;
      li.addEventListener('mousedown', e => {{ e.preventDefault(); inp.value = c; lb.innerHTML = '';
                                               say('City set to ' + c); }});
      lb.appendChild(li);
    }}
    inp.setAttribute('aria-expanded', lb.children.length ? 'true' : 'false');
  }}, 250);
}});
</script>""",
    # 3. a menu (menuitem / menuitemcheckbox)
    "/menu": f"""<h1>Document</h1>
<button type="button" aria-haspopup="menu" id="mb">Actions</button>
<div role="menu" id="m" hidden aria-label="Actions">
  <div role="menuitem" tabindex="-1" data-act="Renamed">Rename</div>
  <div role="menuitem" tabindex="-1" data-act="Duplicated">Duplicate</div>
  <div role="menuitemcheckbox" aria-checked="false" tabindex="-1" data-act="Pinned">Pin to top</div>
</div>{STATUS}
<script>{SAY}
document.getElementById('mb').addEventListener('click', () => {{ document.getElementById('m').hidden = false; }});
document.getElementById('m').addEventListener('click', e => {{
  const it = e.target.closest('[role^=menuitem]');
  if (it) {{ say(it.dataset.act); document.getElementById('m').hidden = true; }}
}});
</script>""",
    # 4. a date picker: two month grids, cells handled by delegation on the grid
    "/calendar": f"""<h1>Choose a date</h1>
{''.join(f'''<table role="grid" aria-label="{m} 2026" data-m="{m}">
<tr>{''.join(f'<th role="columnheader">{d}</th>' for d in "MTWTFSS")}</tr>
{''.join('<tr>' + ''.join(f'<td role="gridcell" tabindex="-1">{w * 7 + k + 1}</td>' for k in range(7)) + '</tr>'
         for w in range(2))}
</table>''' for m in ("April", "May"))}{STATUS}
<script>{SAY}
for (const g of document.querySelectorAll('[role=grid]'))
  g.addEventListener('click', e => {{ const c = e.target.closest('[role=gridcell]');
                                      if (c) say('Picked ' + g.dataset.m + ' ' + c.textContent); }});
</script>""",
    # 5. very deep pages: 3,000 nested spans (CDP's DOM encoder gives up) and 600 nested divs (an AX
    #    tree deeper than Python's recursion limit would allow read_web to walk)
    "/deep": f"""<h1>Deep page</h1><div id="root"></div><div id="root2"></div>{STATUS}
<script>{SAY}
let x = document.getElementById('root');
for (let i = 0; i < 3000; i++) {{ const y = document.createElement('span'); x.appendChild(y); x = y; }}
const b = document.createElement('button'); b.textContent = 'Deep button';
b.addEventListener('click', () => say('deep button pressed')); x.appendChild(b);
let z = document.getElementById('root2');
for (let i = 0; i < 600; i++) {{ const y = document.createElement('div'); z.appendChild(y); z = y; }}
const a = document.createElement('a'); a.href = '/heading/mug'; a.textContent = 'Deep link'; z.appendChild(a);
</script>""",
    # 6. unnamed icon controls
    "/icons": f"""<h1>Toolbar</h1>
<button type="button" class="btn icon-cart" onclick="say('cart opened')"></button>
<button type="button" onclick="say('search opened')"><i class="fa fa-search"></i></button>
<button type="button" onclick="say('liked')"><svg width="10" height="10"><use href="#icon-heart"></use></svg></button>
<a href="/account/settings"><span class="glyph"></span></a>
<p>Quantity</p><button type="button" onclick="say('qty up')"></button>
<button type="button"></button>{STATUS}<script>{SAY}</script>""",
    # 7. look-alike controls: "Add to cart" in each product card, "Details" in each table row
    "/cards": f"""<h1>Products</h1>
<ul>
  <li><h3>Blue Mug</h3><span>$12</span><button type="button" onclick="say('added Blue Mug')">Add to cart</button></li>
  <li><h3>Desk Lamp</h3><span>$30</span><button type="button" onclick="say('added Desk Lamp')">Add to cart</button></li>
  <li><h3>Tea Pot</h3><span>$25</span><button type="button" onclick="say('added Tea Pot')">Add to cart</button></li>
</ul>
<table><tr><th>Order</th><th>Total</th><th></th></tr>
<tr><td>A-100</td><td>$12</td><td><a href="#a100" onclick="say('order A-100')">Details</a></td></tr>
<tr><td>B-200</td><td>$30</td><td><a href="#b200" onclick="say('order B-200')">Details</a></td></tr>
</table>{STATUS}<script>{SAY}</script>""",
    # 8. JS-clickable elements with no role: listener on <li>, cursor:pointer <div> (delegated handler)
    "/clicky": f"""<h1>Suggestions</h1>
<ul id="sugg"><li>Paris, France</li><li>Paris, Texas</li></ul>
<div id="cards"><div class="card" style="cursor:pointer" data-v="Rome"><span>Rome</span> <small>Italy</small></div></div>
<p>Not clickable text</p>{STATUS}
<script>{SAY}
for (const li of document.querySelectorAll('#sugg li')) li.addEventListener('click', () => say('chose ' + li.textContent));
document.getElementById('cards').addEventListener('click', e => {{ const c = e.target.closest('.card');
                                                                   if (c) say('chose ' + c.dataset.v); }});
</script>""",
}


def _html(path):
    title = path.strip("/").replace("/", " ") or "home"
    return f"<!doctype html><html><head><title>{title}</title></head><body>{PAGES[path]}</body></html>"


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        raw = (_html(path) if path in PAGES else "<title>Not found</title><h1>Not found</h1>").encode()
        self.send_response(200 if path in PAGES else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


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


# ------------------------------------------------------------------ helpers --

_LINE = re.compile(r'^\[(\d+)\] (\S+) "((?:[^"\\]|\\.)*)"(.*)$', re.M)


def controls(user):
    """[(n, role, name, rest-of-line)] of the observation in a prompt."""
    obs = user.split("<observation", 1)[-1]
    return [(int(m[1]), m[2], m[3], m[4]) for m in _LINE.finditer(obs)]


def pick(user, role, name, ctx=None):
    hits = [n for n, r, nm, rest in controls(user) if r == role and nm == name
            and (ctx is None or f'(in "{ctx}")' in rest)]
    assert len(hits) == 1, (role, name, ctx, controls(user))
    return hits[0]


class FnModel:
    """Each step is a function of the prompt (the observation) -> one reply in the grammar."""
    output_format = "gbnf"

    def __init__(self, steps):
        self.steps, self.prompts = list(steps), []

    def decide(self, system, user, grammar=None):
        self.prompts.append(user)
        i = len(self.prompts) - 1
        return self.steps[i](user) if i < len(self.steps) else 'needs_user("script exhausted")'


def run(site, chrome, tmp_path, path, steps, tid):
    from scaffold.rl.web.episode import run_web_episode
    task = S.to_spec({"task_id": tid, "confirmed_task": "Reader fixture " + tid, "website": site + path,
                      "level": "easy", "reference_length": 3}, "dev")
    model = FnModel(steps)
    traj, _ = run_web_episode(task, model, model_label="fixture", chromium=chrome, judge_root=tmp_path / "judge",
                              allow_private=True)
    h = traj.events[-1]
    assert traj.error == "" and h["blocked"] is None, (traj.error, h.get("traceback"), h.get("blocked"))
    return traj, model, h


def no_refusal(traj):
    """No grounding refusal (the agent never stopped at needs_user on a number it was given)."""
    assert traj.outcome != "needs_user", (traj.outcome, traj.reason)
    assert not [e for e in traj.events if "rejected" in e], [e for e in traj.events if "rejected" in e]


# ------------------------------------------------------------- end to end --

@needs_chromium
def test_links_and_buttons_inside_headings(site, webchrome, tmp_path):
    seen = {}

    def first(u):
        seen["c"] = controls(u)
        return f"click({pick(u, 'button', 'Save')})"
    traj, model, h = run(site, webchrome, tmp_path, "/heading", [
        first, lambda u: f"click({pick(u, 'link', 'Blue Mug')})", lambda u: 'done("Blue Mug is $12")'], "heading")
    assert [(r, nm) for _n, r, nm, _ in seen["c"]] == [("link", "Blue Mug"), ("link", "Desk Lamp"), ("button", "Save")]
    p0 = model.prompts[0]
    assert p0.count("Blue Mug") == 2, p0                      # one heading line + one control line
    assert "status: saved lamp" in model.prompts[1].lower() or "saved lamp" in model.prompts[1]
    assert traj.steps[-1].url.endswith("/heading/mug") and "Price: $12" in model.prompts[2]
    assert [x.split(" ->")[0] for x in h["browser_log"]] == ["GET /heading", "CLICK button", "CLICK link"]
    no_refusal(traj)


@needs_chromium
def test_custom_combobox_options_appear_after_typing(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/combo", [
        lambda u: f'fill({pick(u, "textbox", "City")}, "New")',
        lambda u: f"click({pick(u, 'option', 'Newark')})",
        lambda u: 'done("Newark")'], "combo")
    after_fill = model.prompts[1]                              # the page fill returned (re-read)
    assert [(r, nm) for _n, r, nm, _ in controls(after_fill) if r == "option"] == [("option", "New York"),
                                                                                   ("option", "Newark")]
    assert "City set to Newark" in model.prompts[2]
    assert not [c for c in controls(model.prompts[2]) if c[1] == "option"]       # the list closed
    assert [x.split(" ->")[0] for x in h["browser_log"]] == ["GET /combo", "FILL textbox", "CLICK option"]
    fill_t = next(t for t in h["timings"] if t["op"] == "fill")
    assert fill_t["settle_ms"] >= 150 and fill_t["observe_ms"] > 0
    no_refusal(traj)


@needs_chromium
def test_menu_items(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/menu", [
        lambda u: f"click({pick(u, 'button', 'Actions')})",
        lambda u: f"click({pick(u, 'menuitem', 'Duplicate')})",
        lambda u: 'done("duplicated")'], "menu")
    opened = controls(model.prompts[1])
    assert [(r, nm) for _n, r, nm, _ in opened] == [("button", "Actions"), ("menuitem", "Rename"),
                                                    ("menuitem", "Duplicate"), ("checkbox", "Pin to top")]
    assert next(rest for _n, r, _nm, rest in opened if r == "checkbox").strip().endswith("= unchecked")
    assert "Duplicated" in model.prompts[2]
    assert [x.split(" ->")[0] for x in h["browser_log"]] == ["GET /menu", "CLICK button", "CLICK menuitem"]
    no_refusal(traj)


@needs_chromium
def test_calendar_gridcells_in_two_months(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/calendar", [
        lambda u: f"click({pick(u, 'gridcell', '12', ctx='May 2026')})",
        lambda u: 'done("May 12")'], "calendar")
    cells = [(nm, rest) for _n, r, nm, rest in controls(model.prompts[0]) if r == "gridcell"]
    assert len(cells) == 28
    assert ('12', ' (in "April 2026")') in cells and ('12', ' (in "May 2026")') in cells
    assert "Picked May 12" in model.prompts[1]
    no_refusal(traj)


@needs_chromium
def test_very_deep_page(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/deep", [
        lambda u: f"click({pick(u, 'button', 'Deep button')})",
        lambda u: f"click({pick(u, 'link', 'Deep link')})",
        lambda u: 'done("ok")'], "deep")
    assert "deep button pressed" in model.prompts[1]
    assert traj.steps[-1].url.endswith("/heading/mug")
    assert [x.split(" ->")[0] for x in h["browser_log"]] == ["GET /deep", "CLICK button", "CLICK link"]
    no_refusal(traj)


@needs_chromium
def test_deep_page_reads_through_flat_document(site, webchrome):
    from scaffold.rl.harness.chromium import ChromiumBackend
    b = ChromiumBackend(site, chromium=webchrome, web=True, allow_private=True)
    try:
        p = b.open(site + "/deep")
        assert b.deep_reads == 1
        assert [c.name for c in p.controls] == ["Deep button", "Deep link"]
    finally:
        b.close()


@needs_chromium
def test_unnamed_icon_controls_get_names(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/icons", [
        lambda u: f"click({pick(u, 'button', 'search (unnamed)')})",
        lambda u: f"click({pick(u, 'button', 'button after Quantity (unnamed)')})",
        lambda u: 'done("ok")'], "icons")
    got = [(r, nm) for _n, r, nm, _ in controls(model.prompts[0])]
    assert got == [("button", "cart (unnamed)"), ("button", "search (unnamed)"), ("button", "heart (unnamed)"),
                   ("link", "link to account/settings (unnamed)"), ("button", "button after Quantity (unnamed)"),
                   ("button", "button after Quantity (unnamed 2)")], got
    assert "search opened" in model.prompts[1] and "qty up" in model.prompts[2]
    no_refusal(traj)


@needs_chromium
def test_look_alike_controls_get_their_item_as_context(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/cards", [
        lambda u: f"click({pick(u, 'button', 'Add to cart', ctx='Desk Lamp')})",
        lambda u: f"click({pick(u, 'link', 'Details', ctx='B-200 | $30')})",
        lambda u: 'done("ok")'], "cards")
    lines = [f'{r} "{nm}"{rest}' for _n, r, nm, rest in controls(model.prompts[0])]
    assert lines == ['button "Add to cart" (in "Blue Mug")', 'button "Add to cart" (in "Desk Lamp")',
                     'button "Add to cart" (in "Tea Pot")', 'link "Details" (in "A-100 | $12")',
                     'link "Details" (in "B-200 | $30")'], lines
    assert "added Desk Lamp" in model.prompts[1] and "order B-200" in model.prompts[2]
    no_refusal(traj)


@needs_chromium
def test_js_clickable_elements(site, webchrome, tmp_path):
    traj, model, h = run(site, webchrome, tmp_path, "/clicky", [
        lambda u: f"click({pick(u, 'clickable', 'Paris, Texas')})",
        lambda u: f"click({pick(u, 'clickable', 'Rome Italy')})",
        lambda u: 'done("ok")'], "clicky")
    got = [(r, nm) for _n, r, nm, _ in controls(model.prompts[0])]
    assert got == [("clickable", "Paris, France"), ("clickable", "Paris, Texas"), ("clickable", "Rome Italy")], got
    assert "Not clickable text" in model.prompts[0]
    assert "chose Paris, Texas" in model.prompts[1] and "chose Rome" in model.prompts[2]
    assert [x.split(" ->")[0] for x in h["browser_log"]] == ["GET /clicky", "CLICK clickable", "CLICK clickable"]
    no_refusal(traj)


@needs_chromium
def test_snapshot_dom_index_equals_get_document(site, webchrome):
    """read_cdp reads the DOM index from DOMSnapshot (one call, any depth): the same tag + attributes per
    node as DOM.getDocument(depth=-1) gave on pages it can encode."""
    from scaffold.rl.harness.axmap import dom_index, flat_document
    from scaffold.rl.harness.chromium import ChromiumBackend
    b = ChromiumBackend(site, chromium=webchrome, web=True, allow_private=True)
    try:
        for path in ("/cards", "/icons", "/calendar", "/clicky"):
            b.open(site + path)
            doc = b.cdp.send("DOM.getDocument", {"depth": -1})
            snap = b.cdp.send("DOMSnapshot.captureSnapshot", {"computedStyles": ["cursor"]})
            want = {k: (v["tag"], v["attrs"]) for k, v in dom_index(doc).items()}
            got = {k: (v["tag"], v["attrs"]) for k, v in dom_index(flat_document(snap)).items()}
            assert got == want, path
    finally:
        b.close()


@needs_chromium
def test_every_number_grounds_to_one_control(site, webchrome):
    """On every fixture page, the real grounder accepts every control number (no ambiguous / unnamed
    refusals), and the reader keeps control names non-empty."""
    from scaffold.agent.desktop import micro_wiring as mw
    from scaffold.rl.harness.chromium import ChromiumBackend
    g = mw.default_grounder("real")
    b = ChromiumBackend(site, chromium=webchrome, web=True, allow_private=True)
    try:
        for path in PAGES:
            p = b.open(site + path)
            for c in p.controls:
                assert c.name.strip(), (path, c)
                assert g.resolve(p, c.n).control is c, (path, c.n, c.name, g.resolve(p, c.n).refusal)
    finally:
        b.close()


# ------------------------------------------------------------- pure functions --

def test_icon_words():
    assert icon_words("fa fa-shopping-cart") == "shopping cart"
    assert icon_words("btn icon-cart") == "cart"
    assert icon_words("glyphicon glyphicon-trash") == "trash"
    assert icon_words("search-icon big") == "search"
    assert icon_words("fa fa-fw fa-lg") == ""
    assert icon_words("btn btn-primary") == ""


def test_clickable_ids_topmost_pointer_and_listeners():
    strings = ["HTML", "BODY", "DIV", "SPAN", "auto", "pointer", "LI"]
    snap = {"strings": strings, "documents": [{
        "nodes": {"parentIndex": [-1, 0, 1, 2, 1], "nodeType": [1, 1, 1, 1, 1], "nodeName": [0, 1, 2, 3, 6],
                  "backendNodeId": [10, 11, 12, 13, 14], "isClickable": {"index": [1, 4]}},
        "layout": {"nodeIndex": [0, 1, 2, 3, 4], "styles": [[4], [4], [5], [5], [4]]}}]}
    # body never counts; DIV sets cursor:pointer (its SPAN only inherits it); LI has a listener
    assert clickable_ids(snap) == {12, 14}
    assert clickable_ids(None) == set()


def test_cdp_fixture_options_and_unnamed_without_a_browser():
    """axmap + read_web on a hand-written CDP tree: an ARIA option outside a <select> becomes an option
    control, an unnamed button takes its DOM hint, a heading's link is a control."""
    def n(i, role, name="", kids=(), bid=None, props=None, parent=None):
        return {"nodeId": str(i), "role": {"value": role}, "name": {"value": name}, "childIds": [str(k) for k in kids],
                "backendDOMNodeId": bid if bid is not None else 100 + i, "ignored": False,
                "properties": [{"name": k, "value": {"value": v}} for k, v in (props or {}).items()],
                **({"parentId": str(parent)} if parent is not None else {})}
    nodes = [n(1, "RootWebArea", "T", (2, 4, 6, 8)),
             n(2, "heading", "Mug", (3,), props={"level": 2}), n(3, "link", "Mug", (31,), props={"url": "http://h/m"}),
             n(31, "StaticText", "Mug"),
             n(4, "listbox", "", (5,)), n(5, "option", "Paris", (51,), props={"selected": False}),
             n(51, "StaticText", "Paris"),
             n(6, "button", "", (), bid=600, props={"focusable": True}),
             n(8, "StaticText", "after")]
    dom = {"root": {"nodeType": 9, "children": [{"nodeType": 1, "backendNodeId": 600, "localName": "button",
                                                  "attributes": ["class", "btn fa-heart", "id", "b1"]}]}}
    page = to_micro_page(snapshot_from_cdp({"nodes": nodes}, dom, "http://h/", "T"))
    assert [(c.role, c.name) for c in page.controls] == [("link", "Mug"), ("option", "Paris"),
                                                         ("button", "heart (unnamed)")]
    assert page.headings == ["Mug"] and page.text.splitlines() == ["Mug", "Paris", "after"]
    v = read_web(snapshot_from_cdp({"nodes": nodes}, dom, "http://h/", "T"))
    assert [c.kind for c in v.controls] == ["link", "option", "button"]
    assert MA.web_tools.CLICKABLE_ROLES[:4] == ("link", "button", "checkbox", "tab")
