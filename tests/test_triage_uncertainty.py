"""Tests for scaffold/agent/desktop/triage_uncertainty.py and the probe's
--uncertainty mode. No model calls: ``Fake`` stands in for llama-server."""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.desktop import email_triage_agent as eta
from scaffold.agent.desktop import triage_cascade as tc
from scaffold.agent.desktop import triage_uncertainty as tu


def msg(mid="m1", addr="dana@acme.example", name="Dana", subject="hello", body="hi", headers=None):
    return {"id": mid, "from_addr": addr, "from_name": name, "from": f"{name} <{addr}>",
            "subject": subject, "body": body, "headers": headers or {}, "date": "2026-09-01"}


def P(cat, imp=None):
    imp = {"urgent_action": 3, "important_read": 2, "fyi": 1}.get(cat, 0) if imp is None else imp
    return {"importance": imp, "category": cat, "needs_reply": False, "suspicious": False}


# ── fake llama-server ─────────────────────────────────────────────────────────

def _tokens(content: str, rows: list, key: str, dists: dict, post: bool) -> list:
    """Split generated JSON into tokens; the first token of each ``key`` value is
    value[:4] with alternatives from ``dists[id]`` (default 0.9 on the value)."""
    toks, pos = [], 0
    pat = re.compile(r'"' + key + r'"\s*:\s*"')
    for it in rows:
        mt = pat.search(content, pos)
        if not mt:
            break
        vs = mt.end()
        toks.append((content[pos:vs], [(content[pos:vs], 1.0)]))
        val = it[key]
        d = dists.get(it["id"]) or {val: 1.0}
        alts: dict = {}
        for c, p in d.items():
            alts[c[:4]] = alts.get(c[:4], 0.0) + p
        toks.append((val[:4], sorted(alts.items(), key=lambda kv: -kv[1])))
        pos = vs + len(val[:4])
    toks.append((content[pos:], [(content[pos:], 1.0)]))

    def entry(t, alts):
        p0 = dict(alts).get(t, alts[0][1])
        if post:
            return {"token": t, "prob": p0, "top_probs": [{"token": a, "prob": p} for a, p in alts]}
        return {"token": t, "logprob": math.log(max(p0, 1e-9)),
                "top_logprobs": [{"token": a, "logprob": math.log(max(p, 1e-9))} for a, p in alts]}
    return [entry(t, a) for t, a in toks]


class Fake:
    """first: id -> pred (temperature < 0.5); resample: id -> pred (>= 0.5, else
    first); p_yes: id -> P(urgent within 48h); dists: id -> category dist."""

    def __init__(self, first=None, resample=None, p_yes=None, dists=None, default=None):
        self.first, self.resample = first or {}, resample or {}
        self.p_yes, self.dists = p_yes or {}, dists or {}
        self.default = default or P("fyi")
        self.calls = []

    def __call__(self, url, body, timeout=900):
        assert url.startswith(("http://127.0.0.1", "http://localhost")), url
        self.calls.append(body)
        schema = json.dumps(((body.get("response_format") or {}).get("json_schema") or {}).get("schema"))
        ids = re.findall(r"^id: (\S+)$", body["messages"][-1]["content"], flags=re.MULTILINE)
        post = bool(body.get("post_sampling_probs"))
        if "urgent_48h" in schema:
            rows = [{"id": i, "urgent_48h": "yes" if self.p_yes.get(i, 0.0) >= 0.5 else "no"} for i in ids]
            content = json.dumps({"items": rows})
            d = {i: {"yes": self.p_yes.get(i, 0.0), "no": 1 - self.p_yes.get(i, 0.0)} for i in ids}
            toks = _tokens(content, rows, "urgent_48h", d, post)
        else:
            hot = float(body.get("temperature", 0.2)) >= 0.5
            src = lambda i: (self.resample.get(i) if hot and i in self.resample else self.first.get(i))
            rows = [{"id": i, **(src(i) or self.default)} for i in ids]
            content = json.dumps({"items": rows})
            toks = _tokens(content, rows, "category", self.dists, post)
        data = {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 20 * len(ids)}}
        if body.get("logprobs"):
            data["choices"][0]["logprobs"] = {"content": toks}
        return data


def _fresh():
    for k in eta.USAGE:
        eta.USAGE[k] = 0 if k != "seconds" else 0.0
    eta.LOCAL_PROBS["ok"] = True


# ── pure functions ────────────────────────────────────────────────────────────

def test_review_bucket():
    assert tu.review_bucket(P("urgent_action")) == "urgent"
    assert tu.review_bucket(P("important_read", 3)) == "urgent"
    assert tu.review_bucket(P("important_read")) == "read"
    assert tu.review_bucket(P("fyi")) == "low"
    assert tu.review_bucket(P("promo", 1)) == "bulk"
    assert tu.review_bucket(P("fyi", 0)) == "bulk"
    assert tu.review_bucket(None) is None
    assert tu.truth_bucket("spam_phishing", 0) == "bulk"


def test_temperature_entropy_and_fit():
    d = {"a": 0.9, "b": 0.1}
    assert abs(sum(tu.temperature_scale(d, 2.0).values()) - 1) < 1e-9
    assert tu.temperature_scale(d, 2.0)["a"] < 0.9 < tu.temperature_scale(d, 0.5)["a"]
    assert tu.norm_entropy({"a": 0.5, "b": 0.5}) == 1.0
    assert tu.norm_entropy({"a": 1.0, "b": 0.0}) == 0.0
    assert tu.entropy_score(None) is None
    # overconfident: 0.99 on the top label, right only 70% of the time -> T > 1
    dists = [{"x": 0.99, "y": 0.01}] * 10
    truths = ["x"] * 7 + ["y"] * 3
    assert tu.fit_temperature(dists, truths) > 1.0
    # well separated and always right -> sharpen (T <= 1)
    assert tu.fit_temperature([{"x": 0.7, "y": 0.3}] * 5, ["x"] * 5) <= 1.0
    assert tu.fit_temperature([], []) == 1.0


def test_auroc_and_coverage():
    assert tu.auroc([0.9, 0.8, 0.1, 0.2], [True, True, False, False]) == 1.0
    assert tu.auroc([0.1, 0.2, 0.9, 0.8], [True, True, False, False]) == 0.0
    assert tu.auroc([0.5, 0.5, 0.5], [True, False, False]) == 0.5
    assert tu.auroc([0.5, None], [True, False]) is None
    rows = tu.coverage_table([0.9, 0.1, 0.1, 0.1], [True, False, False, False], [0.5])
    assert rows == [{"threshold": 0.5, "coverage": 0.75, "accuracy_kept": 1.0, "review": 1,
                     "errors_caught": 1, "error_recall": 1.0}]


def test_permuted_changes_order_keeps_set():
    b = [{"id": f"m{i}"} for i in range(5)]
    for j in (1, 2, 3):
        p = tu.permuted(b, j)
        assert sorted(x["id"] for x in p) == [x["id"] for x in b]
        assert [x["id"] for x in p] != [x["id"] for x in b]
    assert tu.permuted(b[:1], 1) == b[:1]


def test_consistency_and_boundaries():
    c = tu.consistency_score(P("important_read"), [P("urgent_action"), P("fyi")])
    assert c["bucket_disagree"] == 1.0 and c["category_disagree"] == 1.0
    c = tu.consistency_score(P("newsletter"), [P("promo")])
    assert c["bucket_disagree"] == 0.0 and c["category_disagree"] == 1.0
    assert tu.consistency_score(P("fyi"), [None])["score"] is None
    assert tu.boundary_reasons("read", ["urgent"]) == ["missed_urgent"]
    assert tu.boundary_reasons("urgent", ["read"]) == ["false_urgent"]
    assert tu.boundary_reasons("low", ["bulk"]) == ["archive_boundary"]
    assert tu.boundary_reasons("low", ["read"]) == ["inbox_disagreement"]
    assert tu.second_opinion_score(P("urgent_action"), 0.2) == 0.8
    assert tu.second_opinion_score(P("fyi"), 0.2) == 0.2


def test_deterministic_conflicts():
    m = msg()
    raise_rule = {"id": "r1", "source": "learned", "status": "active",
                  "when": {"from_domain": "acme.example"}, "set": {"min_importance": 2}}
    prefs = {"version": 2, "notes": [], "rules": [raise_rule]}
    got = tu.deterministic_conflicts(m, P("promo"), {"mentioned": True, "protected": True}, prefs)
    assert {"bulk_vs_mentioned", "bulk_vs_protected"} <= set(got)
    assert "bulk_vs_rule_raise" in tu.deterministic_conflicts(m, P("promo"), {}, prefs)
    assert tu.deterministic_conflicts(m, P("important_read"), {}, prefs) == []
    assert set(tu.deterministic_conflicts(m, P("urgent_action"), {"injection": True, "list": True})) == \
        {"urgent_on_injection", "urgent_on_list"}
    failing = msg(body="The wire transfer failed; please resend.")
    assert tu.deterministic_conflicts(failing, P("fyi"), {}) == ["low_vs_keep_signal"]
    # a machine sender is urgency_cap's job, not ours
    assert tu.deterministic_conflicts(msg(addr="alerts@bank.example", body="payment failed"),
                                      P("fyi"), {}) == []
    # "tomorrow" on a person's chat only counts against an archive verdict
    chat = msg(body="see you tomorrow")
    assert tu.deterministic_conflicts(chat, P("fyi"), {}) == []
    assert tu.deterministic_conflicts(chat, P("promo"), {}) == ["low_vs_keep_signal"]


def test_review_decision_and_apply():
    assert not tu.review_decision(["inbox_disagreement", "bulk_vs_protected"]).review
    v = tu.review_decision(["archive_boundary"])
    assert v.review and v.block_archive and v.severity == 3
    v2 = tu.review_decision(["false_urgent"])
    assert v2.review and not v2.block_archive
    assert tu.apply_verdict(P("promo"), v)["importance"] == 1
    assert tu.apply_verdict(P("promo"), v, demoted_by_rule=True)["importance"] == 0
    assert tu.apply_verdict(P("promo"), v2)["importance"] == 0
    assert set(v.as_trace()) >= {"review", "block_archive", "reasons", "signals"}


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("AWOS_TRIAGE_UNC_T", "2.5")
    monkeypatch.setenv("AWOS_TRIAGE_UNC_ENTROPY", "0.4")
    monkeypatch.setenv("AWOS_TRIAGE_UNC_BUDGET", "bad")
    p = tu.ReviewPolicy.from_env()
    assert p.temperature == 2.5 and p.entropy_max == 0.4 and p.budget_frac == 0.30 and p.k == 2


# ── assess: budget, priority, cap ─────────────────────────────────────────────

def _items(n, pred=None):
    return [tu.L3Item(id=f"m{i}", message=msg(f"m{i}"), pred=dict(pred or P("fyi"))) for i in range(n)]


def test_assess_free_signals_only():
    its = _items(4)
    its[0].dist = {"fyi": 0.34, "important_read": 0.33, "urgent_action": 0.33}  # ~flat
    its[1].pred = P("important_read")
    its[1].dist = {"important_read": 0.4, "promo": 0.3, "newsletter": 0.3}  # bulk mass 0.6
    its[2].pred = None                                          # no answer
    res = tu.assess(its, base_calls=1, policy=tu.ReviewPolicy(entropy_max=0.8, max_review_frac=1.0),
                    mode="none")
    v = res.verdicts
    assert v["m0"].review and "high_entropy" in v["m0"].reasons and v["m0"].block_archive
    assert v["m1"].review and "archive_boundary" in v["m1"].reasons
    assert v["m2"].review and v["m2"].block_archive
    assert not v["m3"].review and res.extra_calls == 0
    # without a fitted threshold the entropy signal stays off
    res = tu.assess(its[:1], base_calls=1, mode="none")
    assert not res.verdicts["m0"].review and res.verdicts["m0"].signals["entropy"] > 0.99


def test_assess_consistency_within_budget():
    its = _items(30)
    its[3].pred = P("urgent_action")     # priority tier 3 -> checked first
    its[7].pred = P("promo")
    calls = []

    def resample(batch, j):
        calls.append([m["id"] for m in batch])
        return {"m3": P("important_read"), "m7": P("fyi")}
    res = tu.assess(its, base_calls=6, mode="consistency", resample=resample)
    assert res.budget_calls == 1 and res.extra_calls == 1 <= 0.3 * 6
    assert set(res.checked) >= {"m3", "m7"} and len(res.checked) == 5
    assert sorted(calls[0]) == sorted(res.checked)
    assert res.verdicts["m3"].reasons == ["false_urgent"] and res.verdicts["m3"].review
    assert res.verdicts["m7"].review and res.verdicts["m7"].block_archive
    assert sum(v.review for v in res.verdicts.values()) == 2
    # k=3 needs two calls per checked batch: a 1-call budget checks nothing
    res = tu.assess(its, base_calls=6, mode="consistency", resample=resample,
                    policy=tu.ReviewPolicy(k=3))
    assert res.extra_calls == 0 and res.checked == []
    # small runs get no paid checks at all (floor(0.3 * 3) = 0)
    assert tu.assess(its[:15], base_calls=3, mode="consistency", resample=resample).extra_calls == 0


def test_assess_skips_items_already_in_review_and_caps_queue():
    its = _items(10, P("promo"))
    for it in its:
        it.guards = {"org_list": True}                          # all already in review (free)
    its[9].guards = {"mentioned": True}                         # severity 3: never capped
    seen = []
    res = tu.assess(its, base_calls=10, mode="second_opinion",
                    second_opinion=lambda b: seen.extend(m["id"] for m in b) or {})
    assert seen == [] and res.extra_calls == 0
    queued = [v for v in res.verdicts.values() if v.review]
    assert len(queued) == 3 and res.verdicts["m9"].review       # max_review_frac 0.30
    assert all(v.block_archive for v in res.verdicts.values())  # the cap never re-allows archive
    assert sum("over_review_cap" in v.reasons for v in res.verdicts.values()) == 7


def test_assess_second_opinion():
    its = _items(20)
    its[0].pred = P("important_read")
    res = tu.assess(its, base_calls=10, mode="second_opinion",
                    second_opinion=lambda b: {m["id"]: (0.9 if m["id"] == "m0" else 0.1) for m in b})
    assert res.extra_calls == 3 and len(res.checked) == 15
    assert res.verdicts["m0"].reasons == ["missed_urgent"]
    assert sum(v.review for v in res.verdicts.values()) == 1


# ── model-call factories (fake server) ────────────────────────────────────────

def test_make_resampler_body_and_parse(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    _fresh()
    fake = Fake(first={"a": P("fyi")}, resample={"a": P("urgent_action")})
    rs = tu.make_resampler("ctx", prefs=None, xml=False, post=fake)
    got = rs([msg("a"), msg("b")], 1)
    body = fake.calls[-1]
    assert body["temperature"] == 0.7 and body["seed"] == 1001 and "logprobs" not in body
    assert got["a"]["category"] == "urgent_action" and got["b"]["category"] == "fyi"
    assert eta.USAGE["calls"] == 1
    monkeypatch.setenv("AWOS_PROVIDER", "anthropic")
    assert rs([msg("a")], 1) == {}


def test_make_second_opinion_reads_raw_probs(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    _fresh()
    fake = Fake(p_yes={"a": 0.8, "b": 0.3})
    so = tu.make_second_opinion("ctx", xml=True, post=fake)
    got = so([msg("a"), msg("b")])
    body = fake.calls[-1]
    assert body["post_sampling_probs"] is False and body["logprobs"] is True
    assert abs(got["a"] - 0.8) < 1e-6 and abs(got["b"] - 0.3) < 1e-6

    def no_probs(url, b, timeout=900):
        b = dict(b)
        b.pop("logprobs")
        return fake(url, b)
    got = tu.make_second_opinion("ctx", xml=False, post=no_probs)([msg("a"), msg("b")])
    assert got == {"a": 1.0, "b": 0.0}

    def broken(url, b, timeout=900):
        raise OSError("down")
    assert tu.make_second_opinion("ctx", post=broken)([msg("a")]) == {}


# ── probe --uncertainty dry runs (fake server, no network) ────────────────────

def _probe_fake():
    from testbeds.email_triage import seed as seed_mod
    d = seed_mod.generate(7)
    truth = {t["id"]: t for t in d["truth"]["messages"]}
    cal = d["splits"]["calibration"]
    wrong = set(cal[::4])  # every 4th calibration item gets a wrong first label
    first, resample, p_yes, dists = {}, {}, {}, {}
    for i, t in truth.items():
        right = P(t["category"], t["importance"])
        if i in wrong:
            bad = P("fyi") if t["category"] != "fyi" and t["importance"] != 1 else P("urgent_action")
            first[i], resample[i] = bad, right
            dists[i] = {c: 1 / 7 for c in tc.CATEGORIES}
            p_yes[i] = 0.9 if tu.review_bucket(bad) != "urgent" else 0.1
        else:
            first[i] = resample[i] = right
            dists[i] = {c: (0.97 if c == t["category"] else 0.005) for c in tc.CATEGORIES}
            p_yes[i] = 0.9 if tu.review_bucket(right) == "urgent" else 0.1
    return Fake(first=first, resample=resample, p_yes=p_yes, dists=dists), wrong


def _probe():
    sys.path.insert(0, str(REPO / "scripts"))
    import triage_calibration_probe as probe
    return probe


def test_probe_uncertainty_raw_dry_run(tmp_path, monkeypatch, capsys):
    probe = _probe()
    fake, wrong = _probe_fake()
    _fresh()
    saved = eta._http_post
    out = tmp_path / "raw.json"
    assert probe.main(["--seeds", "7", "--uncertainty", "raw", "--out", str(out)], post=fake) == 0
    assert eta._http_post is saved  # restored
    assert all(b.get("post_sampling_probs") is False for b in fake.calls)
    assert len(fake.calls) == 6      # 30 items / 5 per batch, no extra calls
    text = capsys.readouterr().out
    assert "uncertainty: raw" in text and "AWOS_TRIAGE_UNC_T=" in text
    rep = json.loads(out.read_text())
    sig = rep["report"]["signals"]
    assert sig["score_entropy"]["auroc_label"] == 1.0 and sig["score_entropy"]["scored"] == 30
    assert rep["report"]["calls"]["temperature"] > 0
    assert sum(r["wrong"] for r in rep["records"]) == len(wrong)
    assert "score_deterministic" in sig


def test_probe_uncertainty_consistency_dry_run(tmp_path, monkeypatch, capsys):
    probe = _probe()
    fake, _ = _probe_fake()
    _fresh()
    out = tmp_path / "cons.json"
    assert probe.main(["--seeds", "7", "--uncertainty", "consistency", "--k", "3",
                       "--out", str(out)], post=fake) == 0
    hot = [b for b in fake.calls if b.get("temperature") == 0.7]
    assert len(hot) == 12 and len(fake.calls) == 18   # 6 base + 2 resamples per batch
    rep = json.loads(out.read_text())["report"]
    assert rep["calls"] == {"base": 6, "extra": 12}
    assert rep["signals"]["score_category_disagree"]["auroc_label"] == 1.0
    assert rep["signals"]["score_bucket_disagree_k2"]["scored"] == 30
    assert "+200%" in capsys.readouterr().out


def test_probe_uncertainty_second_opinion_dry_run(tmp_path, capsys):
    probe = _probe()
    fake, _ = _probe_fake()
    _fresh()
    out = tmp_path / "so.json"
    assert probe.main(["--seeds", "7", "--uncertainty", "second_opinion", "--out", str(out)],
                      post=fake) == 0
    rep = json.loads(out.read_text())["report"]
    assert rep["calls"] == {"base": 6, "extra": 6}
    s = rep["signals"]["score_second_opinion"]
    assert s["scored"] == 30 and s["auroc_label"] == 1.0
    assert s["suggested"] and s["suggested"]["accuracy_kept"] >= 0.9
