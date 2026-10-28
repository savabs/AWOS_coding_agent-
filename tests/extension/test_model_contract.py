"""The extension speaks the repair model's trained format exactly.

G6 (Qwen3.5 2B) was trained on scaffold/agent/desktop/repair/model.py's SYSTEM + prompt(fp, shortlist)
with "PICK <n>: reason" / "NOT_FOUND: reason" replies. extension/lib/model.js must build the same
messages byte for byte from the same control fields; otherwise the trained model sees a format it
never learned. Runs lib/model.js in plain headless Chromium (no extension needed).
"""
import pathlib

import pytest

from scaffold.agent.desktop.repair import model as PY
from scaffold.agent.desktop.repair.similo import Fingerprint

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"
pytestmark = pytest.mark.timeout(60)

STEP = {"role": "textbox", "name": "Message", "context": "New message", "block": "To Subject Send Save",
        "siblings": ["To", "Subject", "Send", "Save"]}
CANDS = [
    {"role": "textbox", "name": "Text", "context": "New message", "block": "To Subject Send Save",
     "siblings": ["To", "Subject", "Send", "Save"]},
    {"role": "button", "name": "Don't send", "context": "", "block": 'Say "hi" \\ back',
     "siblings": ["O'Brien", 'a "quoted" one']},
    {"role": "link", "name": "", "context": "", "block": "", "siblings": []},
    {"role": "textbox", "name": "Note", "context": "Quick note", "block": "x" * 150, "siblings": ["Save"]},
]


def _fp(d):
    return Fingerprint(role=d["role"], name=d["name"], context=d["context"], label=d["block"],
                       siblings=tuple(d["siblings"]))


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


def test_messages_equal_python_prompt(page):
    msgs = page.evaluate("([s, c]) => AWOSModel.buildMessages({loc: s}, c.map(d => ({desc: d})))", [STEP, CANDS])
    assert msgs[0] == {"role": "system", "content": PY.SYSTEM}
    assert msgs[1] == {"role": "user", "content": PY.prompt(_fp(STEP), [_fp(c) for c in CANDS])}


def test_grammar_and_repr_match(page):
    assert page.evaluate("() => AWOSModel.GRAMMAR") == PY.GRAMMAR
    for s in ["plain", "Don't", 'say "hi"', "both ' and \"", "back\\slash", "tab\tnew\nline"]:
        assert page.evaluate("(s) => AWOSModel.pyRepr(s)", s) == repr(s), s
