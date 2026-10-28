"""The real built site (site/public, served on localhost:8000) with the real unpacked extension: a gallery recipe
link opens /r/, the page finds the extension, "Add to AWOS Replay" hands the recipe over, nothing is imported until
the user confirms in the popup, and the page then says it was added.
Run:  python3 site/build.py && PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -k site_recipe
"""
import functools
import http.server
import re
import threading
from pathlib import Path

import pytest

from test_recipe_v04 import _wait

pytestmark = pytest.mark.timeout(150)
PUBLIC = Path(__file__).resolve().parents[2] / "site" / "public"


@pytest.fixture(scope="module")
def site_8000():
    if not (PUBLIC / "r" / "index.html").exists():
        pytest.skip("build the site first: python3 site/build.py")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(PUBLIC))
    handler.log_message = lambda *a, **k: None
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8000), handler)
    except OSError:
        pytest.skip("port 8000 is busy")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://localhost:8000"
    srv.shutdown()


def test_site_recipe_page_imports_into_extension(x, site_8000):
    gallery = x.ctx.new_page()
    gallery.goto(site_8000 + "/recipes/")
    links = [h for h in gallery.eval_on_selector_all("a[href*='/r/#']", "els => els.map(e => e.href)")]
    assert len(set(links)) == 6, links

    page = x.ctx.new_page()
    page.goto(links[0])
    page.wait_for_selector("#r-view:not([hidden])")
    assert _wait(lambda: page.evaluate("document.documentElement.dataset.awosReplay")) == "0.4.0"
    title = page.inner_text("#r-title")
    n_before = len(x.state()["recordings"])

    page.click("#r-add")
    pend = _wait(lambda: x.state()["pendingImport"])
    assert pend["recipe"]["name"] == title and pend["source"].startswith("page http://localhost:8000")
    assert len(x.state()["recordings"]) == n_before                    # nothing imported before the user confirms

    popup = x.popup(x.tab_id(page))
    popup.wait_for_selector("#importpanel:not([hidden])")
    popup.click("#imp-ok")
    _wait(lambda: re.search(r"Added\.", page.inner_text("body")))
    recs = x.state()["recordings"]
    assert len(recs) == n_before + 1 and any(r.get("imported") and r.get("name") == title for r in recs.values())
