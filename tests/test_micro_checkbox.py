"""click(n) on a checkbox toggles it; its state is shown and submitted (HTML path)."""
import urllib.parse

from scaffold.agent.desktop import web_tools
from scaffold.agent.desktop.web_tools import LoopbackBrowser, checkbox_on, parse_page

HTML = """<title>Prefs</title>
<form aria-label="Prefs" method="post" action="/save">
  <label for="a">Weekly digest</label><input type="checkbox" id="a" name="digest" value="yes">
  <label for="b">Upsell</label><input type="checkbox" id="b" name="upsell" checked>
  <input type="submit" value="Save">
</form>"""


def test_checkbox_state_shown():
    p = parse_page("http://127.0.0.1:1/", 200, HTML)
    lines = [c.line() for c in p.controls]
    assert lines[0].endswith("= unchecked") and "Weekly digest" in lines[0]
    assert lines[1].endswith("= checked")


def test_click_toggles_and_submit_sends_checked_only(monkeypatch):
    br = LoopbackBrowser("http://127.0.0.1:1")
    p = parse_page("http://127.0.0.1:1/", 200, HTML)
    sent = {}

    def fake_request(url, data=None):
        sent["fields"] = dict(urllib.parse.parse_qsl((data or b"").decode()))
        return p
    monkeypatch.setattr(br, "_request", fake_request)
    br.click(p, 1)                                   # tick digest
    br.click(p, 2)                                   # untick upsell
    assert checkbox_on(p.controls[0].value) and not checkbox_on(p.controls[1].value)
    br.click(p, 3)
    assert sent["fields"] == {"digest": "yes"}


def test_ax_value_forms():
    assert checkbox_on("1") and checkbox_on(1) and not checkbox_on("0") and not checkbox_on("")
    assert "checkbox toggles" in __import__("scaffold.agent.desktop.micro_agent", fromlist=["SYSTEM"]).SYSTEM
    assert web_tools.Control(1, "checkbox", "X", value="1").line().endswith("= checked")
