"""Record CDP fixtures (AX tree + DOM) for tests -> scaffold/rl/harness/fixtures/cdp_pages.json

    PYTHONPATH=. scaffold/rl/harness/.venv/bin/python -m scaffold.rl.harness.fixtures.record
"""
import json
from pathlib import Path

from scaffold.rl.harness.chromium import Chromium, SETTLE_JS

HERE = Path(__file__).resolve().parents[1]  # scaffold/rl/harness

PAGES = {
    "widgets": """<!doctype html><html><head><meta charset="utf-8"><title>Widgets · Demo</title></head><body>
<header role="banner"><strong>Demo</strong><nav aria-label="Main"><a href="/projects">Projects</a><a href="/logout">Sign out</a></nav></header>
<main id="main"><h1>Hello <em>world</em></h1>
<form aria-label="Sign in" method="post" action="/login">
<p><label for="e">Email</label> <input id="e" type="email" value="ada@example.com"></p>
<p><label for="p">Password</label><input id="p" type="password" value="hunter2-secret"></p>
<p><input type="checkbox" id="c" checked><label for="c">Remember me</label></p>
<p><label for="co">Country</label><select id="co"><option>India</option><option selected>France</option><option>Japan</option></select></p>
<textarea aria-label="Notes">hi there</textarea>
<fieldset role="radiogroup" aria-label="Scope"><legend>Scope</legend>
<input type="radio" name="s" id="r1"><label for="r1">Read-only</label>
<input type="radio" name="s" id="r2" checked><label for="r2">Read &amp; write</label></fieldset>
<button type="submit">Sign in</button></form>
<form><input name="q" placeholder="Search projects"><button>Go</button></form>
<p role="status">Saved.</p><div role="alert">Wrong email or password.</div>
<section aria-label="New API key"><p>Copy this key now.</p>
<code id="newkey" aria-label="New API key value">fk_live_abcdefghijklmnop1234XYZ</code></section>
<div role="alertdialog" aria-labelledby="t" aria-describedby="d"><h2 id="t">Confirm new key</h2><p id="d">This key grants write access — create?</p>
<form method="post" action="/new" aria-label="Create key"><button>Create key</button></form>
<form method="post" action="/new" aria-label="Cancel"><button>Cancel</button></form></div>
<table aria-label="API keys"><thead><tr><th>Name</th><th>Status</th><th>Actions</th></tr></thead>
<tbody><tr><td>ci-readonly</td><td>active</td><td><form method="post" action="/r" aria-label="Revoke key ci-readonly"><button>Revoke</button></form></td></tr>
<tr><td>old</td><td>revoked</td><td></td></tr></tbody></table>
<div role="tablist"><div role="tab" aria-selected="true">Overview</div><div role="tab">Usage</div></div>
<div hidden><button>Hidden button</button></div><div aria-hidden="true"><a href="/x">Hidden link</a></div>
<ul><li>one</li><li>two</li></ul>
<a href="/projects/a/keys/new" role="button">Create key</a>
</main></body></html>""",
}


def main():
    out = {}
    with Chromium() as ch:
        ctx = ch.browser.new_context()
        ctx.add_init_script(SETTLE_JS)
        pg = ctx.new_page()
        for name, html in PAGES.items():
            url = f"http://127.0.0.1:9/{name}"
            pg.route(url, lambda r, *_a, h=html: r.fulfill(status=200, content_type="text/html", body=h))
            pg.goto(url)
            cdp = ctx.new_cdp_session(pg)
            ax = cdp.send("Accessibility.getFullAXTree")
            dom = cdp.send("DOM.getDocument", {"depth": -1})
            ax["nodes"] = [n for n in ax["nodes"] if (n.get("role") or {}).get("value") != "InlineTextBox"]
            out[name] = {"url": url, "title": pg.title(), "ax": ax, "dom": dom}
        ctx.close()
    p = HERE / "fixtures" / "cdp_pages.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, separators=(",", ":")))
    print(p, p.stat().st_size)


if __name__ == "__main__":
    main()
