"""Reason lines (reasons.py): prompt, sanitising, cost estimate + hard cap, cache (never pay twice), batch
resume, and the merge into dataset rows. No network: every client here is a fake.

    PYTHONPATH=.:tests python3 -m pytest tests/rl/test_train_reasons.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import gbnf_check
from scaffold.agent.desktop import micro_agent as ma
from scaffold.rl.train import dataset as ds, reasons as rs

FIXTURE = Path(__file__).resolve().parents[2] / "scaffold/rl/train/fixtures/oracle_demos.jsonl"


@pytest.fixture()
def rows_file(tmp_path) -> Path:
    out = tmp_path / "data"
    assert ds.main(["--input", f"oracle={FIXTURE}", "--out", str(out), "--val-frac", "0"]) == 0
    return out / "train.jsonl"


def msg(text, tin=900, tout=12):
    return NS(content=[NS(type="text", text=text)], usage=NS(input_tokens=tin, output_tokens=tout))


class FakeMessages:
    def __init__(self, reply="Open the lead the goal names."):
        self.calls, self.reply = [], reply
        self.batches = FakeBatches(self)

    def create(self, **kw):
        self.calls.append(kw)
        return msg(self.reply)


class FakeBatches:
    def __init__(self, parent):
        self.parent, self.created, self.polls = parent, [], 0

    def create(self, requests):
        self.created.append(requests)
        return NS(id=f"msgbatch_{len(self.created)}")

    def retrieve(self, bid):
        self.polls += 1
        return NS(processing_status="ended" if self.polls > 1 else "in_progress")

    def results(self, bid):
        reqs = self.created[-1] if self.created else self.parent.pending
        for i, r in enumerate(reqs):
            if i == 0:
                yield NS(custom_id=r["custom_id"], result=NS(type="errored"))
            else:
                yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=msg(self.parent.reply)))


def client(reply="Open the lead the goal names."):
    return NS(messages=FakeMessages(reply))


# ---------------------------------------------------------------- prompt --

def test_request_is_haiku_thinking_disabled_no_temperature(rows_file):
    item = rs.items_from_rows(rs.read_rows([rows_file]))[0]
    p = rs.request_params(item)
    assert p["model"] == "claude-haiku-5-5" and p["thinking"] == {"type": "disabled"}
    assert not {"temperature", "top_p", "top_k"} & set(p)
    assert p["max_tokens"] == rs.MAX_TOKENS
    text = p["messages"][0]["content"]
    assert f"Known correct next action: {item.action}" in text and item.user.strip() in text


def test_prompt_names_the_target_control():
    u = 'Goal: g\n<observation untrusted="true">\n[3] link "L-694" (in "Leads")\n[4] button "Go"\n</observation>'
    assert rs.control_line(u, "click(3)") == '[3] link "L-694" (in "Leads")'
    assert "It targets this control: [3] link" in rs.build_prompt(u, "click(3)")
    assert rs.control_line(u, 'done("x")') == ""


def test_row_key_ignores_old_reason_and_system():
    assert rs.row_key("u", "click(1)") == rs.row_key("u\n", "# because\nclick(1)")
    assert rs.row_key("u", "click(1)") != rs.row_key("u", "click(2)")


@pytest.mark.parametrize("raw, want", [
    ("Open L-694, the lead the goal names.", "Open L-694, the lead the goal names."),
    ("# Save the new owner.\n", "Save the new owner."),
    ('Reason: "Search people for Haddad."', "Search people for Haddad."),
    ("Click(4) opens the lead.", ""),                 # action syntax
    ("Use control [4] to open it.", ""),              # control number
    ("", ""),
    ("word " * 60, None),                             # long: cut at a word boundary
])
def test_sanitize(raw, want):
    got = rs.sanitize(raw)
    if want is None:
        assert 20 <= len(got) <= rs.MAX_REASON_CHARS and not got.endswith(" ")
    else:
        assert got == want


def test_reason_lines_fit_the_agent_grammar_and_parser():
    g = ma.build_grammar(8)
    for raw in ["Open L-694, the lead the goal names.", "x" * 400, "Save — it's “done”, 100%."]:
        reply = rs.with_reason("click(4)", rs.sanitize(raw) or "fallback reason")
        assert gbnf_check.matches(g, reply), reply
        assert ma.parse_text_action(reply).n == 4
    assert rs.with_reason("# old\nclick(2)", "") == "click(2)"
    assert rs.with_reason("# old\nclick(2)", "new") == "# new\nclick(2)"


# ---------------------------------------------------------- dry run + cap --

def test_dry_run_prints_prompt_and_cost_and_makes_no_call(rows_file, tmp_path, capsys):
    cache = tmp_path / "reasons.jsonl"
    c = client()
    assert rs.main(["--rows", str(rows_file), "--cache", str(cache), "--dry-run"], client=c) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Known correct next action" in out and "usd_batch_worst" in out
    assert not c.messages.calls and not c.messages.batches.created and not cache.exists()
    est = json.loads(out[:out.index("\n}") + 2])
    assert est["to_send"] == est["rows_unique"] > 10
    assert est["usd_batch_worst"] == pytest.approx(est["usd_sync_worst"] / 2, rel=1e-3)
    assert est["usd_batch_worst"] < 0.05                # 30-odd rows: fractions of a cent


def test_cost_estimate_is_worst_case_and_scales():
    items = [rs.Item(str(i), "x" * 2500, "click(1)") for i in range(7500)]
    e = rs.estimate(items, batch=True)
    assert e["output_tokens_max"] == 7500 * rs.MAX_TOKENS
    assert e["input_tokens_est"] >= 7500 * 2500 / 4               # at least a chars/4 count
    assert 0.3 < e["usd_batch_worst"] < rs.DEFAULT_MAX_USD          # the planned ~7.5k rows fit the cap


def test_over_cap_refuses_before_any_call(rows_file, tmp_path):
    c = client()
    assert rs.main(["--rows", str(rows_file), "--cache", str(tmp_path / "c.jsonl"), "--max-usd", "0.0001",
                    "--mode", "sync"], client=c) == 3
    assert not c.messages.calls


def test_sync_stops_at_the_cap(rows_file, tmp_path):
    todo, _ = rs.plan([rows_file], tmp_path / "c.jsonl")
    one = rs.estimate(todo[:1], batch=False)["usd_worst"]
    c = client()
    out = rs.run_sync(c, todo, tmp_path / "c.jsonl", max_usd=one * 3.5)
    assert out["stopped_at_cap"] and 3 <= out["sent"] < len(todo)
    assert out["usd"] <= one * 3.5


# ------------------------------------------------------------------ cache --

def test_sync_run_caches_and_never_pays_twice(rows_file, tmp_path):
    cache = tmp_path / "c.jsonl"
    c = client()
    assert rs.main(["--rows", str(rows_file), "--cache", str(cache), "--mode", "sync"], client=c) == 0
    n = len(c.messages.calls)
    assert n == len(rs.items_from_rows(rs.read_rows([rows_file])))
    assert len(rs.reasons_map(cache)) == n
    assert rs.main(["--rows", str(rows_file), "--cache", str(cache), "--mode", "sync"], client=c) == 0
    assert len(c.messages.calls) == n                                  # second run: zero requests


def test_rejected_reply_is_cached_but_not_merged(rows_file, tmp_path):
    cache = tmp_path / "c.jsonl"
    rs.main(["--rows", str(rows_file), "--cache", str(cache), "--mode", "sync", "--limit", "2"],
            client=client("click(4) because"))
    entries = rs.load_cache(cache)
    assert len(entries) == 2 and all(e["status"] == "rejected" for e in entries.values())
    assert rs.reasons_map(cache) == {}


def test_batch_submits_once_resumes_and_retries_only_failures(rows_file, tmp_path):
    cache = tmp_path / "c.jsonl"
    todo, _ = rs.plan([rows_file], cache)
    c = client()
    out = rs.run_batch(c, todo, cache, poll_s=0, sleep=lambda s: None)
    assert len(c.messages.batches.created) == 1 and out["succeeded"] == len(todo) - 1
    assert not Path(str(cache) + ".batch.json").exists()
    assert out["usd"] == pytest.approx((len(todo) - 1) * rs.usage_usd(900, 12, batch=True), rel=1e-6)
    todo2, counts = rs.plan([rows_file], cache)
    assert len(todo2) == 1 and todo2[0].key == todo[0].key           # only the errored request is left


def test_interrupted_batch_is_collected_not_resubmitted(rows_file, tmp_path):
    cache = tmp_path / "c.jsonl"
    todo, _ = rs.plan([rows_file], cache)
    c = client()
    out = rs.run_batch(c, todo, cache, poll_s=1, max_wait_s=0, sleep=lambda s: None)
    assert "still running" in out["status"] and Path(str(cache) + ".batch.json").exists()
    rs.run_batch(c, todo, cache, poll_s=0, sleep=lambda s: None)
    assert len(c.messages.batches.created) == 1                        # the same batch, collected


# ------------------------------------------------------------------ merge --

def test_dataset_merges_reasons_into_replies(rows_file, tmp_path):
    cache = tmp_path / "c.jsonl"
    rs.main(["--rows", str(rows_file), "--cache", str(cache), "--mode", "sync", "--limit", "5"], client=client())
    out = tmp_path / "with"
    assert ds.main(["--input", f"oracle={FIXTURE}", "--out", str(out), "--val-frac", "0",
                    "--reasons", str(cache)]) == 0
    rows = [json.loads(x) for x in (out / "train.jsonl").read_text().splitlines()]
    tagged = [r for r in rows if r["messages"][2]["content"].startswith("# ")]
    assert len(tagged) == 5
    g = ma.build_grammar(40)
    for r in tagged:
        reply = r["messages"][2]["content"]
        assert reply.splitlines()[0] == "# Open the lead the goal names."
        assert gbnf_check.matches(g, reply) and ma.parse_text_action(reply)
        assert r["completion"] == reply + "<|im_end|>"
    st = json.loads((out / "stats.json").read_text())
    assert st["reasons"]["applied_unique"] == 5 and st["reasons"]["missing_unique"] == len(rows) - 5
    assert st["train_by"]["verb"]["click"] > 0 and "# Open the lead the goal names." not in st["train_by"]["verb"]
