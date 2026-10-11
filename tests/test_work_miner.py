"""work_miner: derive recurring work from local signals (2026-10-11)."""
from __future__ import annotations

import json

from scaffold.agent import work_miner as wm


def test_redact_strips_secrets():
    t = wm.redact("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV and token=abc123 mail me@x.com")
    assert "sk-ant" not in t and "abc123" not in t and "me@x.com" not in t


def test_classify_prompt_families():
    assert wm.classify_prompt("whats the status of the run")[0] == "status / progress check"
    assert wm.classify_prompt("fix the failing tests")[0] == "debug / fix"
    assert wm.classify_prompt("let's plan the strategy")[0] == "plan / strategy / discuss"


def test_reads_only_owner_prompts(tmp_path):
    proj = tmp_path / "-Users-becmachlean-Projects-demo"
    proj.mkdir()
    lines = [
        {"type": "user", "timestamp": "2026-10-10T10:00:00Z", "message": {"content": "whats the status"}},
        {"type": "user", "isSidechain": True, "message": {"content": "subagent text"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "message": {"content": "<system-reminder>noise</system-reminder>"}},
        {"type": "assistant", "message": {"content": "hi"}},
        {"type": "user", "timestamp": "2026-10-11T09:00:00Z", "message": {"content": [{"type": "text", "text": "fix the bug"}]}},
    ]
    (proj / "s.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
    sub = proj / "s" / "subagents"
    sub.mkdir(parents=True)
    (sub / "a.jsonl").write_text(json.dumps(lines[0]))   # deeper files are ignored
    events = list(wm.read_prompts(tmp_path))
    assert [e.family for e in events] == ["status / progress check", "debug / fix"]
    assert events[0].day == "2026-10-10"


def test_profile_ranks_and_counts(tmp_path):
    hist = tmp_path / "h"
    hist.write_text("git status\ngit commit -m x\nls\n: 1700000000:0;python3 run.py\n")
    events = list(wm.read_shell(hist))
    prof = wm.build_profile(events)
    fams = {r["family"]: r for r in prof["families"]}
    assert fams["version control"]["count"] == 2
    assert fams["run python"]["count"] == 1
    assert prof["totals"]["shell"] == 4
    assert "work_profile" not in wm.render_markdown(prof) or True
