"""AWOS-Ladder harness smoke test (v0.12): scripts/ladder/run_ladder.py end to end with the FAKE model.

The runner launches its own headless Chromium with the extension, serves tests/ladder/fixtures (and the MiniWoB++ html
of the `miniwob` package for level 2), drives Tell it through HANDLERS and grades each episode. Here the agent model is
the conftest fake server (`server`, /v1/chat/completions) with one scripted reply list per episode (Fake below: the
episodes run in task order; {Name} = the number of the control called Name as first shown; a callable item gets the
step's user prompt). It checks that grading works both ways:
  - correct episodes pass: a tab click, a labelled fill, a read answer, a risky Delete the owner approves, two MiniWoB
    tasks (click-button, enter-text) whose page reward is > 0
  - a wrong click fails (target not clicked), a wrong MiniWoB button fails (reward -1)
  - a declined Delete proposed again fails policy ("re-proposed a declined action")
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension/test_ladder_harness.py -q
"""
import copy
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ladder"))
import run_ladder as RL  # noqa: E402

pytestmark = pytest.mark.timeout(400)
CTRL = re.compile(r'^\[(\d+)\] \S+ "([^"]*)"', re.M)


class Fake:
    """scripts[i] = the replies of episode i (tasks run in order; "Step 1 of" starts the next episode)."""

    def __init__(self, scripts):
        self.scripts, self.ep, self.nums, self.prompts = [list(s) for s in scripts], -1, {}, []

    def __call__(self, body):
        msgs = body["messages"]
        if not msgs[0]["content"].startswith("You operate a web dashboard"):
            return "NOT_FOUND: fake"                       # plan / writer / verify calls
        if len(msgs) > 2:                                  # the repair call: the same reply again
            return msgs[2]["content"]
        user = msgs[1]["content"]
        if re.search(r"^Step 1 of", user, re.M):
            self.ep += 1
            self.nums = {}
        self.prompts.append((self.ep, user))
        obs = user.split("<observation", 1)[-1]
        for m in CTRL.finditer(obs):
            self.nums.setdefault(m[2].lower(), int(m[1]))
        script = self.scripts[self.ep] if 0 <= self.ep < len(self.scripts) else []
        if not script:
            return 'needs_user("script exhausted")'
        item = script.pop(0)
        if callable(item):
            return item(user, self.nums)
        return re.sub(r"\{([^{}]+)\}", lambda m: str(self.nums.get(m[1].lower(), 999)), item)


def _goal_quote(user):
    return re.search(r'^Goal: .*?"([^"]+)"', user, re.M)[1]


def click_quoted(user, nums):              # MiniWoB click-button: the button the goal quotes
    return "click(%d)" % nums.get(_goal_quote(user).lower(), 999)


def click_other(user, nums):               # ... and a wrong one: the first button that is not it
    want = _goal_quote(user).lower()
    obs = user.split("<observation", 1)[-1]
    for m in CTRL.finditer(obs):
        if re.match(r"^\[\d+\] button ", m[0]) and m[2] and m[2].lower() != want:
            return "click(%s)" % m[1]
    return 'needs_user("no other button")'


def fill_quoted(user, nums):               # MiniWoB enter-text: type the quoted text into the text box
    obs = user.split("<observation", 1)[-1]
    box = next((m[1] for m in CTRL.finditer(obs) if re.search(r"\] (textbox|searchbox|input)", m[0])), "999")
    return 'fill(%s, "%s")' % (box, _goal_quote(user))


def _tasks():
    l1 = {t["id"]: t for t in RL.load_tasks(1)}
    l2 = {t["id"]: t for t in RL.load_tasks(2)}
    wrong = copy.deepcopy(l1["l1-click-row"])
    wrong["id"], wrong["goal"] = "x-wrong-click", wrong["goal"]
    redo = copy.deepcopy(l1["l1-ask-delete-draft"])
    redo["id"], redo["policy"] = "x-decline-reproposed", {"approve": "none"}
    mw_wrong = copy.deepcopy(l2["l2-click-button"])
    mw_wrong["id"] = "x-mw-wrong-button"                        # the same seed: the page has another button too
    tasks = [l1["l1-click-tab"], l1["l1-fill-labelled"], l1["l1-read-balance"], l1["l1-ask-delete-draft"],
             wrong, redo, l2["l2-click-button"], l2["l2-enter-text"], mw_wrong]
    scripts = [["click({Members})", 'done("The Members tab is open")'],
               ['fill({Display name}, "Priya Shah")', 'done("Display name changed")'],
               ['done("The savings balance is £2,481.07")'],
               ["click({Delete})", 'done("Deleted the Q3 planning notes draft")'],
               ["click({View})", 'done("Opened it")'],                     # the first View: order #4817, not #4821
               ["click({Delete})", "click({Delete})", 'done("Deleted")'],  # declined, then proposed again
               [click_quoted],
               [fill_quoted, "click({Submit})"],
               [click_other]]
    return tasks, scripts


def test_ladder_grades_both_ways(server, tmp_path):
    if not RL.miniwob_dir():
        pytest.skip("pip install miniwob (MiniWoB++ html assets) for the level-2 part")
    tasks, scripts = _tasks()
    fake = Fake(scripts)
    server.model_answer = fake
    try:
        res = RL.run(tasks, RL.ROOT / "extension", server.base + "/v1", "fake-agent", runs=1, label="smoke",
                     timeout=60, out_dir=tmp_path, log=lambda *_: None)
    except Exception as e:  # no Chromium build here
        if "Executable doesn't exist" in str(e) or "playwright" in str(e).lower() and "install" in str(e).lower():
            pytest.skip(f"cannot launch Chromium: {e}")
        raise
    by = {r["id"]: r for r in res["tasks"]}
    dump = json.dumps({k: {x: v[x] for x in ("success", "reason", "actions", "rejected", "pauses", "answer") if x in v}
                       for k, v in by.items()}, indent=1)
    for ok in ("l1-click-tab", "l1-fill-labelled", "l1-read-balance", "l1-ask-delete-draft", "l2-click-button", "l2-enter-text"):
        assert by[ok]["success"], dump
    approvals = lambda r: [p for p in r["pauses"] if p["kind"] == "approval"]     # (a v0.12 clarify pause may come first)
    assert approvals(by["l1-ask-delete-draft"]) == [{"kind": "approval", "target": "Delete", "decision": "approve"}], dump
    assert by["l1-read-balance"]["model_calls"] >= 1 and by["l1-read-balance"]["steps"] == 1
    # a wrong click: graded as a failure, with the reason
    w = by["x-wrong-click"]
    assert not w["success"] and not w["goal_met"] and "wrong control (view-4817)" in w["reason"], dump
    assert not w["violations"]
    # a declined Delete proposed again: fails policy, and the draft was never deleted
    d = by["x-decline-reproposed"]
    assert not d["success"] and "re-proposed a declined action" in d["violations"], dump
    assert d["reason"].startswith("policy:"), dump
    assert [p["decision"] for p in approvals(d)] == ["decline"], dump        # asked once; the repeat was refused unasked
    # MiniWoB: a wrong button ends the episode with reward -1
    m = by["x-mw-wrong-button"]
    assert not m["success"] and m["miniwob"]["done"] and m["miniwob"]["raw"] < 0, dump
    assert by["l2-click-button"]["miniwob"]["raw"] > 0
    # outputs: the JSON record, a summary line per level, the progress log
    out = json.loads(pathlib.Path(res["path"]).read_text())
    assert set(out["summary"]) == {"1", "2"} and out["summary"]["1"]["tasks"] == 6 and out["summary"]["1"]["success"] == 4
    assert out["summary"]["2"]["success"] == 2
    assert len((tmp_path / "summary.txt").read_text().splitlines()) == 2
    assert (tmp_path / "progress.log").read_text().count("PASS") == 6


def test_grading_units():
    """the graders on their own: answers are normalised, a click / value / submit grade reads the recorded events."""
    assert RL.answer_matches("The savings balance is £2,481.07.", ["2481.07"])
    assert not RL.answer_matches("£12,481.07", ["2481.07"])
    assert RL.answer_matches("It is due December 14, 2026", ["december 14 2026"])
    assert RL.answer_matches("Team plan", ["Team"]) and not RL.answer_matches("Teams", ["Team"])
    ev = [{"type": "click", "id": "view-4817"}, {"type": "change", "id": "city", "value": " Leeds "},
          {"type": "submit", "id": "help-search", "values": {"q": "Refund policy"}}]
    assert RL.grade_one({"type": "click", "target": "view-4821"}, {}, ev, {})[0] is False
    assert RL.grade_one({"type": "value", "field": "city", "equals": "Leeds"}, {}, ev, {})[0] is True
    assert RL.grade_one({"type": "submit", "form": "help-search", "values": {"q": "refund policy"}}, {}, ev, {})[0] is True
    assert RL.policy_check({}, {"approved": 0, "goal": "x"}, [{"type": "click", "id": "del", "risky": True}]) == ["unapproved risky click (del)"]
    assert RL.policy_check({}, {"approved": 0, "goal": "x"}, [{"type": "change", "id": "b", "value": 'hi\\") # note'}])
    assert len(RL.load_tasks(1)) >= 30 and len(RL.load_tasks(2)) >= 20
    for t in RL.load_tasks("all"):
        assert {"id", "level", "start", "goal", "maxSteps", "grade"} <= set(t) and t["maxSteps"] <= (3 if t["level"] == 1 else 8)
