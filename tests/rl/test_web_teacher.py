"""Teacher-data pipeline (docs/specs/web_teacher.md) without the network or an API key: a fake Anthropic client,
synthetic Mind2Web headers / Online-Mind2Web files, synthetic judged trajectories.

Run:  PYTHONPATH=.:tests .venv/bin/python -m pytest tests/rl/test_web_teacher.py -q
"""
import io
import json
import re
from pathlib import Path

import pytest

from scaffold.agent.desktop import micro_agent as MA
from scaffold.rl.contract import Score, Step, TaskSpec, Trajectory
from scaffold.rl.harness.runner import record
from scaffold.rl.web import m2w_tasks as MT
from scaffold.rl.web import teacher as T
from scaffold.rl.web.source import TRAIN_SOURCE, OnlineMind2WebSource

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = MA.SYSTEM + MA.SYSTEM_WEB
GRAMMAR = MA.build_grammar(5, web=True)
USER = "Goal: find the blue mug.\n<observation>\n[1] textbox \"Search\"\n[2] link \"Mugs\"\n</observation>"


# ------------------------------------------------------------------ fakes --

class _U:
    def __init__(self, i, o, cw=0, cr=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cw, cr


class _B:
    def __init__(self, text):
        self.type, self.text = "text", text


class _R:
    def __init__(self, text, usage, stop="end_turn"):
        self.content, self.usage, self.stop_reason = [_B(text)], usage, stop


class FakeClient:
    """anthropic.Anthropic stand-in: scripted replies, records every request."""

    def __init__(self, replies, usage=(2000, 20, 0, 0), stop="end_turn"):
        self.replies, self.usage, self.stop = list(replies), usage, stop
        self.requests = []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        return _R(self.replies.pop(0), _U(*self.usage), self.stop)


def teacher(replies, max_usd=1.0, **kw):
    return T.TeacherModel("claude-haiku-5-5", max_usd=max_usd, client=FakeClient(replies, **kw),
                          budget=T.Budget(max_usd))


# ------------------------------------------------------------------ grammar --

def test_grammar_check_follows_the_per_step_grammar():
    g = MA.build_grammar(3, web=False)
    assert T.grammar_error("click(2)", g) == ""
    assert T.grammar_error("# open the mugs\nclick(3)", g) == ""
    assert "not on the page" in T.grammar_error("click(4)", g)
    assert "not offered" in T.grammar_error('fill_enter(1, "mug")', g)          # web verbs off
    assert T.grammar_error('fill_enter(1, "mug")', MA.build_grammar(3, web=True)) == ""
    assert T.grammar_error('goto("https://example.com/a")', MA.build_grammar(3, web=True)) == ""
    assert "not offered" in T.grammar_error("copy_to_secret(1, key)", g)
    gs = MA.build_grammar(3, secrets_in=["pw"], secrets_out=["key"])
    assert T.grammar_error("copy_to_secret(1, key)", gs) == ""
    assert "not offered" in T.grammar_error("copy_to_secret(1, other)", gs)
    assert T.grammar_error("fill(1, {{secret:pw}})", gs) == ""
    assert "not offered" in T.grammar_error("fill(1, {{secret:zz}})", gs)
    assert "200" in T.grammar_error(f'fill(1, "{"x" * 201}")', g)
    assert "reason" in T.grammar_error("# " + "r" * 161 + "\nclick(1)", g)
    assert T.grammar_error("I would click the link", g)                          # prose
    assert T.grammar_error("", g)


# ------------------------------------------------------------------ teacher --

def test_teacher_request_contract_and_cost():
    m = teacher(["Sure.\n# the mugs link\nclick(2)"], usage=(1500, 12, 600, 0))
    out = m.decide(SYSTEM, USER, GRAMMAR)
    assert out == "# the mugs link\nclick(2)"
    req = m._client.requests[0]
    assert req["model"] == "claude-haiku-5-5" and req["max_tokens"] == T.MAX_TOKENS
    assert req["system"][0]["text"].startswith(SYSTEM) and req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"] == [{"role": "user", "content": USER}]
    assert req["extra_body"] == {"output_config": {"effort": "low"}, "thinking": {"type": "disabled"}}
    assert not ({"temperature", "top_p", "top_k"} & set(req))                   # rejected by the API
    u = m.usage
    assert u["calls"] == 1 and u["prompt_tokens"] == 2100 and u["cache_write_tokens"] == 600
    assert u["usd"] == pytest.approx((1500 * 0.10 + 600 * 0.125 + 12 * 0.50) / 1e6)
    assert m.budget.spent == pytest.approx(u["usd"]) and m.budget.reserved == 0


def test_teacher_repairs_once_on_a_grammar_violation():
    m = teacher(["click(9)", "# fixed\nclick(2)"])
    assert m.decide(SYSTEM, USER, MA.build_grammar(2, web=True)) == "# fixed\nclick(2)"
    assert m.usage["calls"] == 2 and m.usage["repairs"] == 1 and m.usage["repaired"] == 1
    msgs = m._client.requests[1]["messages"]
    assert [x["role"] for x in msgs] == ["user", "assistant", "user"]           # no prefill: ends on user
    assert msgs[1]["content"] == "click(9)" and "not on the page" in msgs[2]["content"]


def test_teacher_failed_repair_is_an_invalid_step_not_a_guess():
    m = teacher(["let me think", "still prose"])
    out = m.decide(SYSTEM, USER, GRAMMAR)
    assert out == "" and m.usage["invalid"] == 1 and m.usage["calls"] == 2
    with pytest.raises(MA.ParseError):
        MA.parse_text_action(out)


def test_teacher_hard_cap_never_sends_a_call_that_could_pass_it():
    m = teacher(["click(1)"], max_usd=0.00001)
    with pytest.raises(RuntimeError, match="cloud budget reached"):
        m.decide(SYSTEM, USER, GRAMMAR)
    assert m._client.requests == [] and m.budget.refused == 1
    big = T.worst_case_usd("claude-haiku-5-5", len(SYSTEM) + len(USER))
    m2 = teacher(["click(1)", "click(1)"], max_usd=big * 1.5)
    m2.decide(SYSTEM, USER, GRAMMAR)                                            # first fits
    m2.budget.spent = big                                                         # as if the first had cost that much
    with pytest.raises(RuntimeError, match="cloud budget reached"):
        m2.decide(SYSTEM, USER, GRAMMAR)


def test_teacher_refusal_becomes_needs_user():
    m = teacher(["nope"], stop="refusal")
    assert m.decide(SYSTEM, USER, GRAMMAR).startswith("needs_user(") and m.usage["refusals"] == 1


def test_dry_run_needs_no_key_and_still_accounts(monkeypatch):
    monkeypatch.setenv(T.DRY_ENV, "1")
    monkeypatch.setenv(T.DRY_REPLIES_ENV, json.dumps(["click(1)"]))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = T.TeacherModel("claude-haiku-5-5", max_usd=1.0, budget=T.Budget(1.0))
    assert isinstance(m._client, T.DryClient)
    assert m.decide(SYSTEM, USER, GRAMMAR) == "click(1)" and m.usage["usd"] > 0


def test_runner_make_model_and_cli_accept_anthropic_teachers(monkeypatch):
    from scaffold.rl.web import runner as R
    monkeypatch.setenv(T.MAX_USD_ENV, "4")
    monkeypatch.setenv(T.DRY_ENV, "1")
    T.reset_budget()
    task = TaskSpec(id="m2w-x", source=TRAIN_SOURCE, family="shop", split="train", goal="g")
    m, label = R.make_model("anthropic:claude-haiku-5-5", task, {"workers": 2})
    assert isinstance(m, T.TeacherModel) and label == "claude-haiku-5-5" and m.budget.max_usd == 2.0
    assert R.model_kind("anthropic:claude-haiku-5-5") and R.model_kind("local") == "local"
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        R.model_kind("anthropic:")
    with pytest.raises(ValueError, match="no price"):
        T.teacher_model_id("anthropic:claude-unknown-9")
    assert R.budget_reached({"trajectory": {"error": "cloud budget reached ($1 >= $1)"}})
    T.reset_budget()


def test_recorded_step_carries_the_students_prompt_not_the_teacher_rule():
    from scaffold.rl.harness.models import _FORMAT_RULE, RecordingModel
    rec = RecordingModel(teacher(["click(2)"]))
    rec.decide(SYSTEM, USER, GRAMMAR)
    s = rec.steps[0]
    assert s.system == SYSTEM and _FORMAT_RULE not in s.system and s.valid and s.action == "click(2)"


# ------------------------------------------------------------------ task source --

def _m2w_stream(tasks):
    """A Mind2Web train JSON (key order as the real files), with raw HTML that contains escaped task-like text."""
    objs = []
    for t in tasks:
        head = {k: t[k] for k in ("website", "domain", "subdomain", "annotation_id", "confirmed_task", "action_reprs")}
        body = json.dumps(head)[:-1]
        html = '<div data-x="{\\"website\\": \\"evil\\"}">' + "x" * 5000 + "</div>"
        objs.append(body + ', "actions": [{"action_uid": "u1", "raw_html": "' + html + '"}]}')
    return ("[" + ", ".join(objs) + "]").encode()


def _hdr(i, site, text):
    return {"website": site, "domain": "Shopping", "subdomain": "General", "annotation_id": f"a{i:03d}",
            "confirmed_task": text, "action_reprs": ["[link] A -> CLICK"] * (1 + i % 4)}


def test_headers_stream_skips_the_html_and_survives_chunk_boundaries():
    tasks = [_hdr(i, s, f"task {i}") for i, s in enumerate(["kohls", "sports.yahoo", "travelzoo"])]
    raw = _m2w_stream(tasks)
    for chunk in (7, 64, 1 << 20):
        got = list(MT.iter_headers(io.BytesIO(raw), chunk=chunk))
        assert [g["annotation_id"] for g in got] == ["a000", "a001", "a002"], chunk
        assert "actions" not in got[0] and got[2]["action_reprs"] == tasks[2]["action_reprs"]


def test_start_url_rules():
    assert MT.start_url("united") == ("https://www.united.com/", "com")
    assert MT.start_url("sports.yahoo") == ("https://sports.yahoo.com/", "dotted")
    assert MT.start_url("nps.gov")[0] == "https://www.nps.gov/"
    assert MT.start_url("us.megabus") == ("https://us.megabus.com/", "dotted")
    assert MT.start_url("foo.co.uk") == ("https://foo.co.uk/", "host")
    assert MT.start_url("united", {"united": "https://u.example/"}) == ("https://u.example/", "override")


@pytest.fixture
def om2w(tmp_path):
    """A fake Online_Mind2Web.json (3 tasks) + a dev id file with one of them."""
    ents = [{"task_id": "dev1", "confirmed_task": "Find a red lamp under 30 dollars on the shop", "website":
             "https://www.lampshop.com/", "level": "easy"},
            {"task_id": "oth1", "confirmed_task": "Book a table for two at a sushi place tonight", "website":
             "https://www.resy.com/", "level": "medium"},
            {"task_id": "oth2", "confirmed_task": "Check the status of flight UA 100 from Denver", "website":
             "https://www.united.com/", "level": "hard"}]
    p = tmp_path / "Online_Mind2Web.json"
    p.write_text(json.dumps(ents))
    d = tmp_path / "dev_ids.json"
    d.write_text(json.dumps({"ids": ["dev1"]}))
    return p, d


def _headers():
    hs = [_hdr(0, "united", "Check the status of flight UA 100 from Denver"),       # text = an OM2W task
          _hdr(1, "lampshop", "Show me desk lamps sorted by newest arrivals"),        # a dev website
          _hdr(2, "resy", "List vegan restaurants in Austin with outdoor seating"),   # OM2W (non-dev) website
          _hdr(3, "kohls", "Add a pair of black running shoes size 10 to the cart"),
          _hdr(4, "kohls", "Find the store hours of the nearest location to 10001"),
          _hdr(5, "travelzoo", "Show weekend getaway deals from Boston under 300"),
          _hdr(6, "sports.yahoo", "Open the standings of the eastern conference")]
    return hs


def test_build_tasks_excludes_online_mind2web_and_samples_deterministically(om2w):
    p, dev = om2w
    tasks, st = MT.build_tasks(_headers(), [p], dev_ids_path=dev, n=10)
    ids = [t["task_id"] for t in tasks]
    assert st["excluded_text"] == 1 and st["excluded_website"] == {"lampshop": 1}
    assert "m2w-a000" not in ids and "m2w-a001" not in ids and "m2w-a002" in ids  # resy kept under "dev"
    assert len(ids) == 5 and st["shortfall"] == 5
    again, _ = MT.build_tasks(list(reversed(_headers())), [p], dev_ids_path=dev, n=10)
    assert [t["task_id"] for t in again] == ids                                  # order of the input is irrelevant
    t0 = next(t for t in tasks if t["task_id"] == "m2w-a006")
    assert t0["website"] == "https://sports.yahoo.com/" and t0["source"] == TRAIN_SOURCE and t0["level"] == "m2w"
    strict, st2 = MT.build_tasks(_headers(), [p], dev_ids_path=dev, n=10, exclude_websites="all")
    assert "m2w-a002" not in [t["task_id"] for t in strict] and st2["excluded_website"]["resy"] == 1
    rep, _ = MT.build_tasks(_headers(), [p], dev_ids_path=dev, n=7, repeats=2)
    assert len(rep) == 7 and sum(t["task_id"].endswith("-r1") for t in rep) == 2
    assert all(t["base_task"] == re.sub(r"-r\d+$", "", t["task_id"]) for t in rep)
    two, _ = MT.build_tasks(_headers(), [p], dev_ids_path=dev, n=2)
    assert len({t["m2w_website"] for t in two}) == 2                             # round-robin over websites
    with pytest.raises(ValueError, match="required"):
        MT.build_tasks(_headers(), [], dev_ids_path=dev)


def test_build_cli_writes_a_task_file_the_harness_reads_as_train(om2w, tmp_path):
    p, dev = om2w
    hp = tmp_path / "h.jsonl"
    hp.write_text("".join(json.dumps(h) + "\n" for h in _headers()))
    out = tmp_path / "tasks.json"
    assert MT.main(["build", "--headers", str(hp), "--exclude", str(p), "--dev-ids", str(dev), "--n", "3",
                    "--out", str(out)]) == 0
    stats = json.loads(Path(str(out) + ".stats.json").read_text())
    assert stats["selected"] == 3 and "confirmed_task" not in json.dumps(stats)
    src = OnlineMind2WebSource(str(out), fetch=False, dev_ids_path=str(dev))
    specs = list(src.tasks("train"))
    assert len(specs) == 3 and {s.split for s in specs} == {"train"} and {s.source for s in specs} == {TRAIN_SOURCE}
    assert specs[0].meta["base_task"] == specs[0].id and specs[0].start.startswith("https://")
    with pytest.raises(ValueError, match="teacher task file"):
        list(OnlineMind2WebSource(str(p), fetch=False, dev_ids_path=str(dev)).tasks("train"))
    leak = tmp_path / "leak.json"
    leak.write_text(json.dumps([{"task_id": "dev1", "confirmed_task": "x", "website": "a.com",
                                 "source": TRAIN_SOURCE}]))
    with pytest.raises(ValueError, match="dev ids"):
        list(OnlineMind2WebSource(str(leak), fetch=False, dev_ids_path=str(dev)).tasks("train"))
    assert MT.main(["build", "--headers", str(hp), "--exclude", str(tmp_path / "missing.json"),
                    "--dev-ids", str(dev), "--out", str(tmp_path / "x.json")]) == 2


# ------------------------------------------------------------------ SFT rows --

def _episode(tid, steps, *, error="", blocked=None, goal="Add black running shoes size 10 to the cart",
             source=TRAIN_SOURCE, split="train"):
    task = TaskSpec(id=tid, source=source, family="kohls.com", split=split, goal=goal, start="https://www.kohls.com/",
                    meta={"base_task": re.sub(r"-r\d+$", "", tid), "level": "m2w"})
    ss = []
    for i, (reply, valid) in enumerate(steps, 1):
        act = MA.parse_text_action(reply).show() if valid else ""
        ss.append(Step(i=i, system=SYSTEM, user=f"{USER}\nStep {i} of {tid}", grammar=GRAMMAR, reply=reply,
                       action=act, valid=valid))
    h = {"event": "harness", "blocked": blocked, "stop_by": "done"}
    traj = Trajectory(task_id=tid, model="claude-haiku-5-5", steps=ss, outcome="completed_unverified",
                      events=[h], error=error)
    return record(task, 0, traj, Score(success=False))


def _write_run(tmp_path, recs, verdicts):
    run = tmp_path / "run" / "shard1"
    run.mkdir(parents=True)
    (run / "trajectories.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    res = tmp_path / "results.json"
    res.write_text(json.dumps({"runs": {"teach": {"tasks": verdicts}}}))
    return tmp_path / "run", res


def _v(tid, label, status="judged", hs="ok"):
    return {"task_id": tid, "status": status, "predicted_label": label, "harness_status": hs}


def test_teacher_rows_keep_only_judged_successes(om2w, tmp_path):
    from scaffold.rl.train import web_teacher_data as W
    from scaffold.rl.train.dataset import validate_row
    p, dev = om2w
    recs = [_episode("m2w-a1", [("click(1)", True), ("bad reply", False), ("# typed it\nfill(1, \"shoes\")", True),
                                ('done("added")', True)]),
            _episode("m2w-a1-r1", [("click(1)", True), ('done("added")', True)]),       # repeat: same base task
            _episode("m2w-a2", [("click(2)", True), ("oops", False), ("click(1)", True),
                                ('done("claimed")', True)]),                                     # judge failed
            _episode("m2w-a3", [("click(1)", True), ('needs_user("login wall")', True)]),        # judged ok, no done
            _episode("m2w-a4", [], blocked={"kind": "bot_wall"}),
            _episode("m2w-a5", [("click(1)", True)], error="cloud budget reached ($1)")]
    verdicts = [_v("m2w-a1", 1), _v("m2w-a1-r1", 1), _v("m2w-a2", 0), _v("m2w-a3", 1),
                _v("m2w-a4", 0, status="blocked", hs="blocked")]
    runs, res = _write_run(tmp_path, recs, verdicts)
    rows, st = W.build([runs], [res], [p], dev_ids_path=dev)
    assert st["kept_episodes"] == 2 and st["dropped"]["judge_failed"] == 1 and st["dropped"]["not_done"] == 1
    assert st["dropped"]["blocked"] == 1 and st["dropped"]["infra_error"] == 1
    replies = [r["messages"][2]["content"] for r in rows]
    assert replies.count('done("added")') == 2 and "bad reply" not in replies and 'done("claimed")' not in replies
    assert {r["meta"]["task_id"] for r in rows} == {"m2w-a1"}                    # repeats share the base task
    rec = [r for r in rows if r["meta"]["recovery"]]
    assert len(rec) == 1 and rec[0]["messages"][2]["content"].startswith("# typed it") and st["recovery_rows"] == 1
    for r in rows:
        validate_row(r, "t")
        assert r["meta"]["split"] == "train" and r["completion"].endswith("<|im_end|>")
    more, st2 = W.build([runs], [res], [p], dev_ids_path=dev, recovery_from_failed=True)
    extra = [r for r in more if r["meta"]["judged"] == "failed"]
    assert [r["messages"][2]["content"] for r in extra] == ["click(1)"] and st2["rows_from_failed_episodes"] == 1


def test_teacher_rows_refuse_contamination(om2w, tmp_path):
    from scaffold.rl.train import web_teacher_data as W
    from scaffold.rl.train.dataset import EvalSplitError
    p, dev = om2w
    cases = [_episode("m2w-b1", [('done("x")', True)], goal="Check the status of flight UA 100 from Denver"),
             _episode("dev1", [('done("x")', True)]),
             _episode("oth1", [('done("x")', True)], source="online-mind2web", split="all")]
    for i, rec in enumerate(cases):
        runs, res = _write_run(tmp_path / f"c{i}", [rec], [_v(rec["task"]["id"], 1)])
        with pytest.raises(EvalSplitError):
            W.build([runs], [res], [p], dev_ids_path=dev)
    runs, res = _write_run(tmp_path / "cli", cases[:1], [_v("m2w-b1", 1)])
    out = tmp_path / "rows.jsonl"
    assert W.main(["--runs", str(runs), "--verdicts", str(res), "--exclude", str(p), "--dev-ids", str(dev),
                   "--out", str(out)]) == 2 and not out.exists()


def test_verdict_files_jsonl_and_conflicts(tmp_path):
    from scaffold.rl.train import web_teacher_data as W
    a = tmp_path / "a.verdicts.jsonl"
    a.write_text(json.dumps({"run": "x", "verdict": _v("t1", 1)}) + "\n" + json.dumps({"run": "x", "verdict": _v("t2", 1)})
                 + "\n" + json.dumps({"run": "x", "verdict": _v("t3", None, status="judge_error")}) + "\n")
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"runs": {"y": {"tasks": [_v("t2", 0)]}}}))
    v, conflicts = W.load_verdicts([a, b])
    assert set(v) == {"t1"} and conflicts == 1


# ------------------------------------------------------------------ cost --

def test_cost_profile_and_estimate():
    from scaffold.rl.web import teacher_cost as C
    recs = [_episode("e1", [("click(1)", True)] * 10), _episode("e2", [])]
    prof = C.profile(recs)
    assert prof["reach_rate"] == 0.5 and prof["steps_per_reaching_episode"] == 10
    e = C.episode_usd(prof, "claude-haiku-5-5", chars_per_token=3.2, repair_rate=0.0)
    tin = prof["in_chars_mean"] / 3.2
    assert e["calls"] == 5 and e["usd"] == pytest.approx(5 * (tin * 0.10 + (8 / 3.2 + 10) * 0.50) / 1e6)
    assert C.worst_episode_usd(prof) > e["usd"] * 5
    assert "| e" not in C.table({"x": prof}, [300, 1000], "claude-haiku-5-5", 3.2, 0.1)   # no task ids / text


# ------------------------------------------------------------------ CI --

def test_teach_workflow_is_dry_by_default_capped_and_sealed():
    env = (ROOT / "ci/rl_teach.env").read_text()
    assert re.search(r"^DRY_RUN=1$", env, re.M) and re.search(r'^MAX_USD="\d+(\.\d+)?"$', env, re.M)
    assert not re.search(r"hf_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{10,}|TOKEN=|API_KEY=", env)
    wf = (ROOT / ".github/workflows/rl-teach.yml").read_text()
    assert '"bench-cloud/rl-teach-**"' in wf
    assert wf.count("scripts/ci_seal.sh") == 3 and "ci_unseal.sh" in wf
    assert 'if [ "$DRY_RUN" = "0" ]' in wf and "AWOS_TEACHER_DRY=1" in wf and "AWOS_TEACHER_MAX_USD" in wf
    assert "--split train" in wf and "rl-web-merged-${MODEL_LABEL}-train" in wf
    ladder = (ROOT / ".github/workflows/ladder.yml").read_text()
    assert '"!bench-cloud/rl-**"' in ladder                                   # never starts the paid ladder
    for other in (ROOT / ".github/workflows").glob("*.yml"):
        if other.name != "rl-teach.yml":
            assert "rl-teach" not in other.read_text(), other.name           # no other workflow fires on it


# ------------------------------------------------------------------ e2e (Chromium, dry teacher) --

def _chromium_ok():
    try:
        import playwright  # noqa: F401
        from scaffold.rl.harness.chromium import find_chromium
        return bool(find_chromium())
    except ImportError:
        return False


@pytest.fixture(scope="module")
def shop():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/search"):
                body = "<h1>Results</h1><ul><li><a href='/item'>Blue Mug - $12</a></li></ul>"
            else:
                body = ("<h1>Shop</h1><form action='/search' role='search' aria-label='Site search'>"
                        "<input type='search' name='q' aria-label='Search products'></form><a href='/about'>About</a>")
            raw = f"<!doctype html><html><head><title>Shop</title></head><body>{body}</body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.skipif(not _chromium_ok(), reason="Playwright / Chromium build missing")
def test_dry_teacher_run_end_to_end_with_repair_cap_and_rows(shop, tmp_path, om2w):
    """The CLI as CI runs it (DRY: no key, no network to Anthropic): a repaired step, a judge layout, spend in the
    summary; a tiny cap stops dispatching; the converter turns the (fake-judged) episode into rows."""
    import os
    import subprocess
    import sys
    from scaffold.rl.train import web_teacher_data as W
    tasks = [{"task_id": f"m2w-t{i}", "confirmed_task": "Find the price of the blue mug", "website": shop + "/",
              "level": "m2w", "reference_length": 2, "source": TRAIN_SOURCE, "base_task": f"m2w-t{i}"} for i in (1, 2)]
    tj = tmp_path / "tasks.json"
    tj.write_text(json.dumps(tasks))
    p, dev = om2w

    def run(out, replies, max_usd):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update(PYTHONPATH=str(ROOT), AWOS_TEACHER_DRY="1", AWOS_TEACHER_DRY_REPLIES=json.dumps(replies),
                   AWOS_TEACHER_MAX_USD=str(max_usd))
        return subprocess.run([sys.executable, "-m", "scaffold.rl.web", "run", "--model", "anthropic:claude-haiku-5-5",
                               "--split", "train", "--tasks-json", str(tj), "--dev-ids", str(dev), "--allow-private",
                               "--out", str(out)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    out = tmp_path / "run"
    r = run(out, ["click(99)", 'fill_enter(1, "mug")', 'done("$12")'], 1.0)
    assert r.returncode == 0, r.stderr[-2000:]
    recs = [json.loads(x) for x in (out / "trajectories.jsonl").read_text().splitlines()]
    assert len(recs) == 2
    t1 = next(x for x in recs if x["task"]["id"] == "m2w-t1")["trajectory"]
    assert [s["action"] for s in t1["steps"]][:2] == ['fill_enter(1, "mug")', 'done("$12")'], t1["steps"]
    h = t1["events"][-1]
    assert h["usage"]["repairs"] == 1 and h["usage"]["usd"] > 0 and h["answer"] == "$12"
    assert json.loads((out / "summary.json").read_text())["overall"]["usd"] > 0
    assert json.loads((out / "run.json").read_text())["source"] == "mind2web-train"
    res = tmp_path / "results.json"
    res.write_text(json.dumps({"runs": {"teach": {"tasks": [_v("m2w-t1", 1), _v("m2w-t2", 0)]}}}))
    rows, st = W.build([out], [res], [p], dev_ids_path=dev)
    assert st["kept_episodes"] == 1 and [r["messages"][2]["content"] for r in rows] == ['fill_enter(1, "mug")',
                                                                                        'done("$12")']
    capped = tmp_path / "capped"
    r = run(capped, ['done("x")'], 1e-6)
    assert r.returncode == 0, r.stderr[-2000:]
    recs = [json.loads(x) for x in (capped / "trajectories.jsonl").read_text().splitlines()]
    assert len(recs) == 1 and recs[0]["trajectory"]["error"].startswith("cloud budget reached")
