"""CloudTextModel: Claude replies normalised to the local GBNF text form, thinking settings, spend cap."""
from types import SimpleNamespace

import pytest

from scaffold.rl.harness import models as M


def test_normalize_keeps_reason_and_one_action():
    assert M.normalize_text_reply("# open the keys page\nclick(4)") == "# open the keys page\nclick(4)"
    assert M.normalize_text_reply("Sure! Here is my answer:\n```\nclick(4)\n```") == "click(4)"
    assert M.normalize_text_reply('#reason\nfill(2, "Ada")\nclick(3)') == '# reason\nfill(2, "Ada")'
    assert M.normalize_text_reply("I think we should click the button") == ""
    long = "# " + "x" * 400 + "\ndone(\"ok\")"
    assert len(M.normalize_text_reply(long).splitlines()[0]) <= 162


@pytest.mark.parametrize("model,thinking", [
    ("claude-opus-5-5", None), ("claude-sonnet-5-5", {"type": "between_tools"}),
    ("claude-haiku-5-5", {"type": "disabled"}),
])
def test_thinking_settings_follow_current_api(model, thinking):
    extra = M.CloudTextModel(model=model, max_usd=1)._extra()
    assert extra["output_config"] == {"effort": "low"}
    assert extra.get("thinking") == thinking
    assert "temperature" not in extra


class _FakeMessages:
    def __init__(self, text, stop="end_turn"):
        self.text, self.stop, self.calls = text, stop, []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop, usage=SimpleNamespace(input_tokens=2000, output_tokens=100),
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=self.text)])


def test_decide_records_cost_and_caps_spend(monkeypatch):
    monkeypatch.setitem(M._SPEND, "usd", 0.0)
    m = M.CloudTextModel(model="claude-sonnet-5-5", max_usd=0.006)
    fake = _FakeMessages("# go\nclick(2)")
    m._client = SimpleNamespace(messages=fake)
    assert m.decide("SYS", "USER") == "# go\nclick(2)"
    kw = fake.calls[0]
    assert kw["system"].startswith("SYS") and "temperature" not in kw and kw["messages"][0]["content"] == "USER"
    assert m.usage["usd"] == pytest.approx(0.005)            # 2000*2e-6 + 100*10e-6
    m.decide("SYS", "USER")                                  # 0.010 spent now
    with pytest.raises(RuntimeError, match="budget"):
        m.decide("SYS", "USER")


def test_refusal_becomes_needs_user(monkeypatch):
    monkeypatch.setitem(M._SPEND, "usd", 0.0)
    m = M.CloudTextModel(model="claude-opus-5-5", max_usd=1)
    m._client = SimpleNamespace(messages=_FakeMessages("", stop="refusal"))
    assert m.decide("S", "U").startswith("needs_user(") and m.usage["refusals"] == 1
