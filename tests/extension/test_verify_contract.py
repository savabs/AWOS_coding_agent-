"""The extension's verifier prompt is the trained one (G7).

The page outline must equal scaffold/agent/desktop/web_tools.outline() for the same page fields, and the
whole user message must equal scaffold/rl/train/verifier_data.render_user() for the same goal, actions,
observation and reply (including the 4,500-char clip and the 14-action cap). Runs lib/model.js in plain
headless Chromium.
"""
import pathlib

import pytest

from scaffold.agent.desktop.web_tools import Control, Page, outline
from scaffold.rl.train import verifier_data as V

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"
pytestmark = pytest.mark.timeout(60)

CONTROLS = [
    {"role": "button", "name": "New event", "context": "", "inDialog": False},
    {"role": "textbox", "name": "Event name", "context": "New event", "inDialog": True, "value": "Hiring"},
    {"role": "password", "name": "PIN", "context": "", "inDialog": False, "value": "••••••••"},
    {"role": "combobox", "name": "Calendar", "context": "New event", "inDialog": True, "options": ["Work", "Family"], "value": "Work"},
    {"role": "checkbox", "name": "Remind me", "context": "Options", "inDialog": False, "value": "1"},
    {"role": "checkbox", "name": "All day", "context": "all day", "inDialog": False, "value": "0"},
    {"role": "tab", "name": "Week", "context": "", "inDialog": False, "value": "1"},
    {"role": "link", "name": "Help", "context": "", "inDialog": False},
]
OBS = {"url": "http://127.0.0.1:9/calendar/week?x=1", "title": "Agenda", "alerts": ["Heads up"],
       "statuses": ['Event "Hiring" created for 2026-10-28 at 12:30.'], "dialog": "New event", "regions": ["Quick note: hi"],
       "text": "Agenda\nThis week\n" + "row " * 900, "controls": CONTROLS}


def _py_page():
    cs = [Control(n=i + 1, role=c["role"], name=c["name"], context=c["context"], in_dialog=c["inDialog"],
                  value=c.get("value", "") if c["role"] != "combobox" else "", options=c.get("options", []))
          for i, c in enumerate(CONTROLS)]
    return Page(url=OBS["url"], status=200, title=OBS["title"], headings=[], controls=cs, forms=[], alerts=OBS["alerts"],
                statuses=OBS["statuses"], regions=OBS["regions"], dialog=OBS["dialog"], text=OBS["text"])


@pytest.fixture(scope="module")
def page(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()
    p.set_content("<html><body></body></html>")
    p.add_script_tag(path=str(EXT / "lib" / "model.js"))
    yield p
    b.close()


def test_outline_equals_product_outline(page):
    assert page.evaluate("(o) => AWOSModel.outline(o)", OBS) == outline(_py_page())


@pytest.mark.parametrize("n_actions", [3, 20])
def test_verify_messages_equal_training_prompt(page, n_actions):
    obs = outline(_py_page())
    acts = [{"action": f"click({k + 1})", "control": f'button "B{k}"'} for k in range(n_actions - 1)]
    acts.append({"action": 'done("Done.")'})
    msgs = page.evaluate("([g, a, o, r]) => AWOSModel.buildVerifyMessages(g, a, o, r)",
                         ["Schedule Hiring panel on 2026-10-28 at 12:30.", acts, obs, 'done("Done.")'])
    steps = [{"i": k + 1, "action": a["action"], "user": ""} for k, a in enumerate(acts)]
    steps[-1].update(user=f'<observation untrusted="true">\n{obs}\n</observation>', reply='done("Done.")')
    traj = {"steps": steps, "events": [{"step": k + 1, "control": a["control"]} for k, a in enumerate(acts) if "control" in a]}
    assert msgs[0] == {"role": "system", "content": V.SYSTEM}
    assert msgs[1] == {"role": "user", "content": V.render_user("Schedule Hiring panel on 2026-10-28 at 12:30.", traj)}


def test_grammar_and_parse(page):
    from importlib import util
    spec = util.spec_from_file_location("ev", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "rl" / "eval_verifier.py")
    ev = util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    assert page.evaluate("() => AWOSModel.VERIFY_GRAMMAR") == ev.GRAMMAR
    got = page.evaluate("() => ['NOT_DONE: title is Hiring, goal says Hiring panel', 'VERIFIED: banner', 'maybe']"
                        ".map(AWOSModel.parseVerify)")
    assert got[0] == {"label": "NOT_DONE", "reason": "title is Hiring, goal says Hiring panel"}
    assert got[1]["label"] == "VERIFIED" and "error" in got[2]
