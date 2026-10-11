"""work_miner: derive recurring work from local signals (v1, 2026-10-11)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scaffold.agent import work_miner as wm
from scaffold.agent import work_miner_label as wl
from scaffold.agent import work_miner_sessions as ws
from scaffold.agent import work_miner_shell as wsh


# ------------------------------------------------------------------ basics

def test_redact_strips_secrets():
    t = wm.redact("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV and token=abc123xyz789 mail me@x.com")
    assert "sk-ant" not in t and "abc123" not in t and "me@x.com" not in t


def test_classify_prompt_families():
    assert wm.classify_prompt("whats the status of the run")[0] == "status / progress check"
    assert wm.classify_prompt("fix the failing tests")[0] == "debug / fix"
    assert wm.classify_prompt("let's plan the strategy")[0] == "plan / strategy / discuss"
    assert wm.classify_prompt("continue")[0] == "continue / approve"
    assert wm.classify_prompt("my openrouter credits ran out")[0] == "keys / credentials / quota"
    assert wm.classify_prompt("zzz qqq")[0] == "other"
    # every rule family exists in the shared taxonomy
    assert {f for f, _ in wm.PROMPT_RULES} <= set(wl.TAXONOMY)


# --------------------------------------------------------------- labelling

class _FakeLlama(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(body)
        msg = body["messages"][-1]["content"]
        if "garbage" in msg:
            content = "not json at all"
        elif "badlabel" in msg:
            content = json.dumps({"label": "made up", "new_family": ""})
        elif "pizza" in msg:
            content = json.dumps({"label": "other", "new_family": "Personal errands!!"})
        else:
            content = json.dumps({"label": "status / progress check", "new_family": ""})
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def fake_server():
    _FakeLlama.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _FakeLlama)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_labeller_refuses_non_local_hosts():
    with pytest.raises(ValueError):
        wl.LocalLabeller(base_url="https://api.anthropic.com/v1")
    with pytest.raises(ValueError):
        wl.LocalLabeller(base_url="http://openrouter.ai/api/v1")


def test_labeller_constrained_output_and_cache(fake_server, tmp_path):
    cache = tmp_path / "labels.json"
    lab = wl.LocalLabeller(base_url=fake_server, cache_path=cache, workers=3, redact=wm.redact)
    assert lab.available()
    texts = ["how is it going", "order a pizza", "garbage reply", "badlabel please",
             "how is it going", "key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV broke"]
    out = lab.label_many(texts)
    assert out[wl.prompt_hash("how is it going")].label == "status / progress check"
    pz = out[wl.prompt_hash("order a pizza")]
    assert pz.label == "other" and pz.new_family == "personal errands"   # sanitised proposal
    assert wl.prompt_hash("garbage reply") not in out                     # unparseable -> fallback
    assert wl.prompt_hash("badlabel please") not in out                   # off-schema -> fallback
    assert lab.stats["failed"] == 2
    # request is schema-constrained, deterministic, and redacted before sending
    req = _FakeLlama.calls[0]
    assert req["response_format"]["json_schema"]["schema"]["properties"]["label"]["enum"] == wl.LABELS
    assert req["temperature"] == 0
    assert not any("sk-ant" in c["messages"][-1]["content"] for c in _FakeLlama.calls)
    # duplicate text labelled once
    assert sum("how is it going" in c["messages"][-1]["content"] for c in _FakeLlama.calls) == 1
    # cache holds hashes + labels, never prompt text
    raw = cache.read_text()
    assert "pizza" not in raw and "how is it going" not in raw
    # rerun is incremental: only the two failures go back to the server
    n = len(_FakeLlama.calls)
    lab2 = wl.LocalLabeller(base_url=fake_server, cache_path=cache, workers=2)
    out2 = lab2.label_many(texts)
    assert lab2.stats["cache"] == 3 and len(_FakeLlama.calls) - n == 2
    assert out2[wl.prompt_hash("order a pizza")].source == "cache"


def test_label_prompts_falls_back_to_rules_when_server_down(tmp_path):
    lab = wl.LocalLabeller(base_url="http://127.0.0.1:9/v1", cache_path=tmp_path / "c.json")
    labels, stats = wm.label_prompts(["continue", "zzz"], "auto", labeller=lab)
    assert stats["server"] is False and stats["rules_fallback"] == 2
    assert labels[wl.prompt_hash("continue")] == "continue / approve"
    with pytest.raises(RuntimeError):
        wm.label_prompts(["continue"], "model", labeller=lab)


def test_label_prompts_model_overrides_rules(fake_server, tmp_path):
    lab = wl.LocalLabeller(base_url=fake_server, cache_path=tmp_path / "c.json")
    labels, stats = wm.label_prompts(["zzz qqq", "order a pizza"], "auto", labeller=lab)
    assert labels[wl.prompt_hash("zzz qqq")] == "status / progress check"
    assert stats["unclassified_rules"] == 1.0 and stats["unclassified_final"] == 0.5
    assert stats["proposals"] == {"personal errands": 1}


# ------------------------------------------------------------------- shell

def test_shell_rejoins_zsh_multiline_and_heredoc():
    lines = [
        "git status",
        "for f in *.txt; do\\",              # zsh newline marker
        "echo\\",
        "  cat $f\\",
        "done",
        "find . \\\\",                        # typed continuation: literal \ + marker
        "  -name '*.py'",
        "cat <<EOF > x.txt",                  # heredoc without markers (bash style)
        "hello",
        "EOF",
        "echo 'multi",                        # open quote continues
        "line'",
        ": 1700000000:0;python3 -m pytest -q",
    ]
    cmds = wsh.rejoin(lines)
    texts = [c.text for c in cmds]
    assert len(cmds) == 6
    assert texts[1].splitlines()[0] == "for f in *.txt; do" and texts[1].endswith("done")
    assert texts[2] == "find . -name '*.py'"
    assert texts[3].endswith("EOF") and cmds[3].lines == 3
    assert cmds[4].lines == 2
    assert cmds[5].ts == 1700000000 and cmds[5].text == "python3 -m pytest -q"
    assert not any(t.strip() in ("\\", "echo\\", "echo") for t in texts)


def test_shell_unclosed_heredoc_or_quote_does_not_swallow_history():
    cmds = wsh.rejoin(["cat <<EOF", "ls", "echo it's", "pwd"])
    assert [c.text for c in cmds] == ["cat <<EOF", "ls", "echo it's", "pwd"]


def test_shell_family_normalisation():
    f = wsh.command_family
    assert f("git status") == "git: inspect"
    assert f("git -C repo commit -m x") == "git: commit/push"
    assert f("git worktree add ../x") == "git: sync/branch"
    assert f("python3 scripts/job_series.py --n 3") == "python script"
    assert f("python -m scaffold.agent.work_miner") == "python module"
    assert f("python3 -m pytest tests -q") == "run tests"
    assert f("pytest -x") == "run tests"
    assert f("npm test") == "run tests"
    assert f("python -m pip install x") == "python packages"
    assert f("python -c 'print(1)'") == "python inline"
    assert f("FOO=1 sudo du -sh ~") == "disk / system info"
    assert f("for x in a b; do\necho $x\ndone") == "shell script (multi-line)"
    assert f("# note\nls -la") == "navigate files"
    assert f("clea") == "terminal housekeeping"            # typo of clear
    assert f("weirdtool --x").startswith("other: ")


def test_read_shell_events(tmp_path):
    hist = tmp_path / "h"
    hist.write_text("git status\ngit commit -m x\nls\nfor a in b; do\\\necho\\\ndone\n: 1700000000:0;python3 run.py\n")
    events = list(wm.read_shell(hist))
    prof = wm.build_profile(events)
    fams = {r["family"]: r for r in prof["families"]}
    assert prof["totals"]["shell"] == 5
    assert fams["git: inspect"]["count"] == 1 and fams["python script"]["count"] == 1
    assert fams["shell script (multi-line)"]["count"] == 1
    assert fams["python script"]["active_days"] == 1


# ---------------------------------------------------------------- sessions

def _u(ts, text, **kw):
    return {"type": "user", "timestamp": ts, "uuid": kw.pop("uuid", ts + text[:5]),
            "message": {"content": text}, **kw}


def _a(ts, text="", stop="end_turn", tools=()):
    content = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": tid, "name": name, "input": inp} for tid, name, inp in tools]
    return {"type": "assistant", "timestamp": ts, "uuid": "a" + ts,
            "message": {"content": content, "stop_reason": stop}}


def _tr(ts, text, tool_id="t0", **kw):
    return {"type": "user", "timestamp": ts, "uuid": "r" + ts,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}, **kw}


def _write(tmp_path, recs, name="s1"):
    proj = tmp_path / "-Users-becmachlean-Projects-demo"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    return tmp_path


def test_owner_prompt_filter(tmp_path):
    recs = [
        _u("2026-10-10T10:00:00Z", "whats the status", origin={"kind": "human"}, promptSource="typed"),
        _u("2026-10-10T10:00:01Z", "subagent text", isSidechain=True),
        _tr("2026-10-10T10:00:02Z", "x"),
        _u("2026-10-10T10:00:03Z", "<system-reminder>noise</system-reminder>"),
        _u("2026-10-10T10:00:04Z", "<task-notification>done</task-notification>",
           origin={"kind": "task-notification"}, promptSource="system"),
        _u("2026-10-10T10:00:05Z", "peer says hi", origin={"kind": "peer"}, isMeta=True),
        _a("2026-10-10T10:00:06Z", "hi"),
        {"type": "user", "timestamp": "2026-10-11T09:00:00Z", "uuid": "z",
         "message": {"content": [{"type": "text", "text": "fix the bug"}]}},
    ]
    root = _write(tmp_path, recs)
    sub = root / "-Users-becmachlean-Projects-demo" / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "a.jsonl").write_text(json.dumps(_u("2026-10-12T00:00:00Z", "deep")))   # ignored
    events = list(wm.read_prompts(root))
    assert [e.family for e in events] == ["status / progress check", "debug / fix"]
    assert events[0].day == "2026-10-10"


def test_session_metrics_timing_and_preceders(tmp_path):
    recs = [
        _u("2026-10-05T10:00:00Z", "build the parser"),                 # W42
        _a("2026-10-05T10:01:00Z", tools=[("t1", "Bash", {"command": "x"})], stop="tool_use"),
        _tr("2026-10-05T10:02:00Z", "ok", "t1"),
        _a("2026-10-05T10:05:00Z", "Done. Want me to add tests?"),
        _u("2026-10-05T10:06:00Z", "yes"),                               # question
        _a("2026-10-05T10:10:00Z", tools=[("bg1", "Agent", {"prompt": "x"})], stop="tool_use"),
        _tr("2026-10-05T10:10:01Z", "launched", "bg1"),
        _a("2026-10-05T10:11:00Z", "Started a background agent."),
        _u("2026-10-05T10:20:00Z", "status?"),                           # background pending
        {"type": "user", "timestamp": "2026-10-05T10:30:00Z", "uuid": "n1", "origin": {"kind": "task-notification"},
         "promptSource": "system",
         "message": {"content": "<task-notification><tool-use-id>bg1</tool-use-id><status>completed</status></task-notification>"}},
        _a("2026-10-05T10:31:00Z", "The agent finished."),
        _u("2026-10-05T12:00:00Z", "continue"),                          # long silence
        _tr("2026-10-12T09:01:00Z", "Error: invalid x-api-key", "t9"),   # W42
        _a("2026-10-12T09:02:00Z", "The provider rejected the key."),
        _u("2026-10-12T09:03:00Z", "use the other key"),                 # key error
    ]
    root = _write(tmp_path, recs)
    sessions = ws.read_sessions(root)
    assert len(sessions) == 1
    ps = sessions[0].prompts
    assert [p.preceded_by for p in ps] == ["session start", "agent asked a question",
                                          "background task pending", "long silence", "key/auth error"]
    assert ps[1].gap_s == 360 and ps[1].agent_run_s == 300 and ps[1].idle_s == 60
    assert ps[1].agent_active_s == 300
    ws.apply_labels(sessions, lambda t: wm.classify_prompt(t)[0], wm.SUPERVISION)
    assert [p.supervision for p in ps] == [False, True, True, True, True]
    m = ws.session_metrics(sessions)
    assert m["owner_prompts"] == 5 and m["supervision_prompts"] == 4
    assert m["supervision_share"] == 0.8
    assert m["prompts_per_session"]["median"] == 5
    assert [w["week"] for w in m["weekly"]] == ["2026-W41", "2026-W42"]
    assert m["weekly"][0]["supervision_share"] == 0.75 and m["weekly"][1]["supervision_share"] == 1.0
    assert m["preceded_by_supervision"]["key/auth error"]["count"] == 1


def test_sessions_dedupe_copied_records(tmp_path):
    recs = [_u("2026-10-05T10:00:00Z", "build it", uuid="u1"), _a("2026-10-05T10:01:00Z", "ok")]
    _write(tmp_path, recs, "s1")
    _write(tmp_path, recs, "s2")          # resumed copy of the same records
    sessions = ws.read_sessions(tmp_path)
    assert sum(len(s.prompts) for s in sessions) == 1


# ----------------------------------------------------------------- report

def test_run_end_to_end_rules_only(tmp_path):
    root = _write(tmp_path / "p", [
        _u("2026-10-05T10:00:00Z", "run the benchmark series"),
        _a("2026-10-05T10:05:00Z", "done"),
        _u("2026-10-05T10:06:00Z", "continue"),
    ])
    hist = tmp_path / "h"
    hist.write_text("git status\npytest -q\n")
    out = tmp_path / "out"
    prof = wm.run(projects=root, history=hist, out_dir=out, mode="rules")
    md = (out / "work_profile.md").read_text()
    for heading in ("What you ask AI to do", "Session patterns", "Weekly supervision series",
                    "Top automation candidates"):
        assert heading in md
    assert prof["sessions"]["supervision_share"] == 0.5
    assert prof["candidates"] and all(c["how"] for c in prof["candidates"])
    stored = (out / "work_profile.json").read_text()
    assert "run the benchmark series" not in stored and "run the benchmark series" not in md
    assert prof["labelling"]["mode"] == "rules"


def test_cache_carries_concrete_labels_across_taxonomy_bump(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    c = wl.LabelCache(path)
    c.put("h1", wl.Label("debug / fix"), "m")
    c.put("h2", wl.Label("other", "personal errands"), "m")
    c.save()
    monkeypatch.setattr(wl, "TAXONOMY_VERSION", "v9")
    c2 = wl.LabelCache(path)
    assert c2.get("h1").label == "debug / fix"     # carried over
    assert c2.get("h2") is None                    # 'other' gets relabelled
