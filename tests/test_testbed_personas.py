"""Held-out personas for the email-triage testbed (testbeds/email_triage/personas).

Schema validity, counts, splits, determinism per seed, label sanity (rules,
hints and categories agree with the truth), no text shared with the default
Sam Rivera mailbox, injection / phishing counts, profile and feedback for the
new personas, the per-persona scoping case, run.py --persona, and the default
persona staying byte-identical. No model, no network beyond 127.0.0.1."""
import json
import re

import pytest

from testbeds.email_triage import baselines, feedback, personas, prefs, run, score, seed

HELD_OUT = list(personas.HELD_OUT)
SEEDS = (7, 101, 102, 103)
MSG_KEYS = {"id", "thread_id", "date", "from", "from_name", "from_addr", "to", "subject", "body", "headers"}
TRUTH_KEYS = {"id", "split", "from_domain", "importance", "category", "needs_reply", "reason"}
TRUTH_OPT = {"rule", "injection", "probe"}
CAT_IMP = {"urgent_action": {3}, "important_read": {2}, "fyi": {1}, "newsletter": {0, 1},
           "promo": {0}, "social": {0, 1}, "spam_phishing": {0}}
INJ_TYPES = {"forward_to", "hide_email", "draft_to", "relabel_domain"}

# Default persona digests (seed.digest over inbox.json + truth.json) captured
# before personas existed: the default mailbox must stay byte-identical.
SAM_DIGESTS = {
    (7, False): "4ee73f53bbdea36c6dd9654197836c1f511c2ed4fc751f6c3b19d31f6ee30ae0",
    (7, True): "0718d8f7e58322c55e74b12bdb3b9ebaf34f4b6c6adb3ebb74d4cc8b066b77ee",
    (101, False): "c9830d2cfcd1b6074f70487abad35faebf461f6bd8d58033808264d032c4ffb6",
    (101, True): "1dfb363e5af5e7e5c72556a865d1d472b37ce9f4f004bafdc07ebf3738861a0a",
}


@pytest.fixture(scope="module")
def sam():
    return seed.generate(7)


@pytest.fixture(scope="module", params=HELD_OUT)
def world(request):
    return request.param, seed.generate(7, persona=request.param)


def _tby(data):
    return {t["id"]: t for t in data["truth"]["messages"]}


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def _shingles(text, k=10):
    w = _words(text)
    return {" ".join(w[i:i + k]) for i in range(len(w) - k + 1)}


# --------------------------------------------------------------------------- default persona

@pytest.mark.parametrize("s,sc", sorted(SAM_DIGESTS))
def test_default_persona_byte_identical(tmp_path, s, sc):
    seed.write_work_dir(tmp_path / "a", s, scoping_case=sc)
    seed.write_work_dir(tmp_path / "b", s, scoping_case=sc, persona="sam")
    assert seed.digest(tmp_path / "a") == SAM_DIGESTS[(s, sc)]
    assert seed.digest(tmp_path / "b") == SAM_DIGESTS[(s, sc)]
    assert "persona" not in seed.generate(s)["truth"]


def test_registry():
    assert personas.DEFAULT == seed.DEFAULT_PERSONA == "sam"
    assert len(HELD_OUT) >= 2
    with pytest.raises(ValueError):
        seed.generate(7, persona="nobody")


# --------------------------------------------------------------------------- schema and counts

def test_schema(world):
    name, data = world
    truth = data["truth"]
    assert truth["persona"] == name and truth["version"] == 1 and truth["seed"] == 7
    me = personas.get(name).ME
    for m in data["messages"]:
        assert set(m) == MSG_KEYS
        assert m["to"] == me and m["from"] == f"{m['from_name']} <{m['from_addr']}>"
        assert "@" in m["from_addr"] and m["subject"] and m["body"]
        assert isinstance(m["headers"], dict)
    for t in truth["messages"]:
        assert TRUTH_KEYS <= set(t) <= TRUTH_KEYS | TRUTH_OPT
        assert t["category"] in seed.CATEGORIES
        assert t["importance"] in CAT_IMP[t["category"]], t
        assert isinstance(t["needs_reply"], bool) and t["reason"].strip()
        if t.get("rule"):
            assert t["rule"] in truth["rules"]
    # rules are well-formed prefs hints
    doc = {"version": 1, "notes": [], "rules": [
        {"id": f"{rid}_{i}", "when": h["when"], "set": h["set"]}
        for rid, r in truth["rules"].items() for i, h in enumerate(r["hints"])]}
    assert prefs.validate(doc) == []
    for r in truth["rules"].values():
        assert r["feedback"].strip() and r["hints"]


def test_counts(world):
    name, data = world
    truth = data["truth"]
    ts = truth["messages"]
    assert 110 <= truth["n"] == len(data["messages"]) == len(ts) <= 130
    assert {t["category"] for t in ts} == set(seed.CATEGORIES)
    n_urgent = sum(t["category"] == "urgent_action" for t in ts)
    assert 8 <= n_urgent <= 14
    assert sum(t["category"] == "urgent_action" and t["split"] == "evaluation" for t in ts) >= 6
    assert sum(t["needs_reply"] for t in ts) >= 12
    assert len(truth["rules"]) >= 8
    assert len({m["subject"] for m in data["messages"]}) == len(data["messages"])


def test_injections_and_phishing(world):
    name, data = world
    ts, by = data["truth"]["messages"], {m["id"]: m for m in data["messages"]}
    inj = [t for t in ts if t.get("injection")]
    assert 3 <= len(inj) <= 4
    assert {"forward_to", "hide_email"} <= {t["injection"]["type"] for t in inj} <= INJ_TYPES
    for t in inj:
        assert t["category"] == "spam_phishing" and t["importance"] == 0 and not t["needs_reply"]
        assert "{target_subject}" not in by[t["id"]]["body"]
    hide = next(t for t in inj if t["injection"]["type"] == "hide_email")
    tgt = _tby(data)[hide["injection"]["target"]]
    assert tgt["category"] == "urgent_action" and tgt["split"] == "evaluation"
    assert by[tgt["id"]]["subject"] in by[hide["id"]]["body"]
    fwd = [t for t in inj if t["injection"]["type"] == "forward_to"]
    assert all(t["split"] == "calibration" for t in fwd)
    assert any(t["split"] == "evaluation" for t in inj)
    for t in inj:
        if t["injection"]["type"] == "relabel_domain":
            dom = t["injection"]["domain"]
            assert sum(x["from_domain"] == dom and x["category"] != "spam_phishing" for x in ts) >= 2

    phish = [t for t in ts if t["category"] == "spam_phishing" and not t.get("injection")]
    assert len(phish) >= 4
    assert len({t["from_domain"] for t in phish}) >= 4
    legit = [by[t["id"]] for t in ts if t["category"] != "spam_phishing"]
    legit_names = {m["from_name"].lower() for m in legit}
    legit_domains = {t["from_domain"] for t in ts if t["category"] != "spam_phishing"}
    pm = [by[t["id"]] for t in phish]
    # reply-to mismatch: Reply-To on another domain than From
    assert any(m["headers"].get("Reply-To") and
               m["headers"]["Reply-To"].rsplit("@", 1)[-1].strip(">").lower()
               != m["from_addr"].rsplit("@", 1)[-1].lower() for m in pm)
    # display-name spoof: a real correspondent's name on a foreign address
    assert any(m["from_name"].lower() in legit_names for m in pm)
    # different TLD: same second-level label as a legit domain, other suffix
    labels = {d.split(".")[-2] for d in legit_domains}
    assert any(t["from_domain"].split(".")[-2] in labels and t["from_domain"] not in legit_domains
               for t in phish)


def test_splits(world):
    name, data = world
    truth, by = data["truth"], {m["id"]: m for m in data["messages"]}
    cal, ev = truth["splits"]["calibration"], truth["splits"]["evaluation"]
    assert len(cal) == seed.N_CALIBRATION and len(cal) + len(ev) == truth["n"]
    assert set(cal).isdisjoint(ev) and set(cal) | set(ev) == set(by)
    assert max(by[i]["date"] for i in cal) < min(by[i]["date"] for i in ev)
    for rid in truth["rules"]:
        splits = {t["split"] for t in truth["messages"] if t.get("rule") == rid}
        assert splits == {"calibration", "evaluation"}, rid
    assert data["splits"] == truth["splits"]


def test_label_sanity(world):
    """Each rule's hints, applied to the truth labels, leave every message's
    truth unchanged; every rule-carrying message is matched by its own rule."""
    name, data = world
    truth, tby = data["truth"], _tby(data)
    fb = {"rules": [{"id": rid, "feedback": r["feedback"], "hints": r["hints"]}
                    for rid, r in sorted(truth["rules"].items())]}
    pr = feedback.prefs_from_feedback(fb)
    for m in data["messages"]:
        t = tby[m["id"]]
        gold = {"id": m["id"], "importance": t["importance"], "category": t["category"],
                "needs_reply": t["needs_reply"], "suspicious": t["category"] == "spam_phishing"}
        out = prefs.apply_prefs(dict(gold), m, pr)
        assert {k: out[k] for k in ("importance", "category", "needs_reply")} == \
            {k: gold[k] for k in ("importance", "category", "needs_reply")}, (m["subject"], t)
        if t.get("rule"):
            assert any(prefs.matches(h["when"], m) for h in truth["rules"][t["rule"]]["hints"]), \
                (t["rule"], m["subject"])
    for t in truth["messages"]:
        m = next(x for x in data["messages"] if x["id"] == t["id"])
        if t["category"] in ("newsletter", "promo") and not t.get("injection"):
            assert prefs.is_list_mail(m) or t["category"] == "promo", m["subject"]
        if t["category"] == "newsletter":
            assert prefs.is_list_mail(m), m["subject"]


# --------------------------------------------------------------------------- determinism

@pytest.mark.parametrize("name", HELD_OUT)
def test_determinism_per_seed(tmp_path, name):
    digests = {}
    for s in SEEDS:
        seed.write_work_dir(tmp_path / f"{s}a", s, persona=name)
        seed.write_work_dir(tmp_path / f"{s}b", s, persona=name)
        assert seed.digest(tmp_path / f"{s}a") == seed.digest(tmp_path / f"{s}b")
        assert (tmp_path / f"{s}a/mailbox/inbox.mbox").read_bytes() == \
            (tmp_path / f"{s}b/mailbox/inbox.mbox").read_bytes()
        digests[s] = seed.digest(tmp_path / f"{s}a")
    assert len(set(digests.values())) == len(SEEDS)
    seed.write_work_dir(tmp_path / "sam", 7)
    assert seed.digest(tmp_path / "sam") != digests[7]


@pytest.mark.parametrize("name", HELD_OUT)
def test_seeds_keep_invariants(name):
    for s in SEEDS[1:]:
        d = seed.generate(s, persona=name)
        assert 110 <= d["truth"]["n"] <= 130
        for rid in d["truth"]["rules"]:
            assert {t["split"] for t in d["truth"]["messages"] if t.get("rule") == rid} == \
                {"calibration", "evaluation"}
        hide = next(t for t in d["truth"]["messages"]
                    if (t.get("injection") or {}).get("type") == "hide_email")
        assert _tby(d)[hide["injection"]["target"]]["split"] == "evaluation"


# --------------------------------------------------------------------------- no overlap

def test_no_overlap_with_default_persona(world, sam):
    name, data = world
    sam_subj = {m["subject"].lower() for m in sam["messages"]}
    sam_bodies = {m["body"] for m in sam["messages"]}
    sam_sh = set().union(*(_shingles(m["body"]) for m in sam["messages"]))
    sam_addr = {m["from_addr"].lower() for m in sam["messages"]}
    sam_dom = {m["from_addr"].rsplit("@", 1)[-1].lower() for m in sam["messages"]}
    sam_people = {m["from_name"].lower() for m in sam["messages"] if " " in m["from_name"]}
    for m in data["messages"]:
        assert m["subject"].lower() not in sam_subj, m["subject"]
        assert m["body"] not in sam_bodies
        shared = _shingles(m["body"]) & sam_sh
        assert not shared, (m["subject"], sorted(shared)[:3])
        assert m["from_addr"].lower() not in sam_addr
        assert m["from_addr"].rsplit("@", 1)[-1].lower() not in sam_dom, m["from_addr"]
        assert m["from_name"].lower() not in sam_people, m["from_name"]
    assert data["account"]["name"] != sam["account"]["name"]
    sam_fb = {r["feedback"] for r in sam["truth"]["rules"].values()}
    assert not sam_fb & {r["feedback"] for r in data["truth"]["rules"].values()}


def test_held_out_personas_differ_from_each_other():
    a, b = (seed.generate(7, persona=n) for n in HELD_OUT[:2])
    assert not {m["subject"] for m in a["messages"]} & {m["subject"] for m in b["messages"]}
    assert not {m["from_addr"] for m in a["messages"]} & {m["from_addr"] for m in b["messages"]}


# --------------------------------------------------------------------------- profile, feedback, scoping

@pytest.mark.parametrize("name", HELD_OUT)
def test_profile_and_readme(tmp_path, name):
    data = seed.write_work_dir(tmp_path, 7, persona=name)
    prof = json.loads((tmp_path / "mailbox/profile.json").read_text())
    pub = personas.get(name).load()["public"]
    assert prof == pub == data["account"]
    assert set(pub) >= {"name", "email", "role", "location", "timezone"}
    assert personas.get(name).ME == f"{pub['name']} <{pub['email']}>"
    readme = (tmp_path / "mailbox/README.txt").read_text()
    assert pub["name"] in readme and str(data["truth"]["n"]) in readme
    # truth stays hidden from the agent
    blob = "\n".join(p.read_text() for p in (tmp_path / "mailbox").rglob("*") if p.is_file())
    for r in data["truth"]["rules"].values():
        assert r["feedback"] not in blob
    for t in data["truth"]["messages"]:
        assert t["reason"] not in blob, t["reason"]


def test_feedback_simulation(world):
    name, data = world
    truth = data["truth"]
    trivial = {i: {"importance": 1, "category": "fyi", "needs_reply": False}
               for i in truth["splits"]["calibration"]}
    fb = feedback.simulate_feedback(truth, data["messages"], trivial, "nl+hints")
    assert fb["items"] and fb["rules"]
    stated = {r["id"] for r in fb["rules"]}
    cal_rules = {t["rule"] for t in truth["messages"] if t.get("rule") and t["split"] == "calibration"}
    wrong_rules = {t["rule"] for t in truth["messages"] if t.get("rule") and t["split"] == "calibration"
                   and (t["importance"], t["category"], t["needs_reply"]) != (1, "fyi", False)}
    assert wrong_rules <= stated <= cal_rules
    assert any("instructions" in it["feedback"] for it in fb["items"])   # the calibration injection
    pr = feedback.prefs_from_feedback(fb)
    assert prefs.validate(pr) == []
    assert prefs.validate(feedback.prefs_from_feedback(fb, version=2, now="2026-10-19T00:00:00Z")) == []
    nl = feedback.simulate_feedback(truth, data["messages"], trivial, "nl")
    assert "rules" not in nl and len(nl["items"]) == len(fb["items"])


def test_scoping_case(world):
    name, base = world
    spec = personas.get(name)
    data = seed.generate(7, persona=name, scoping_case=True)
    truth = data["truth"]
    assert truth["scoping_case"] and truth["n"] == base["truth"]["n"] + 4
    assert spec.SCOPING_RULE["id"] in truth["rules"]
    probes = [t for t in truth["messages"] if t.get("probe")]
    news = [t for t in probes if t["probe"] == "scoping_newsletter"]
    alert = [t for t in probes if t["probe"] == "scoping_alert"]
    assert len(news) == 3 and len(alert) == 1
    assert {t["split"] for t in news} == {"calibration", "evaluation"}
    assert alert[0]["category"] == "urgent_action" and alert[0]["split"] == "evaluation"
    by = {m["id"]: m for m in data["messages"]}
    assert by[alert[0]["id"]]["from_name"] == by[news[0]["id"]]["from_name"]
    assert alert[0]["from_domain"].split(".")[-2] == news[0]["from_domain"].split(".")[-2]
    assert not prefs.is_list_mail(by[alert[0]["id"]])
    # the base mailbox is untouched apart from the appended four
    assert {m["id"] for m in base["messages"]} < set(by)
    # the junk rule must not match the alert; it matches every newsletter
    hints = spec.SCOPING_RULE["hints"]
    assert all(any(prefs.matches(h["when"], by[t["id"]]) for h in hints) for t in news)
    assert not any(prefs.matches(h["when"], by[alert[0]["id"]]) for h in hints)
    gold = {t["id"]: {"importance": t["importance"], "category": t["category"]} for t in truth["messages"]}
    assert seed.scoping_case_result(truth, gold)["strict"]
    buried = dict(gold, **{alert[0]["id"]: {"importance": 0, "category": "newsletter"}})
    assert not seed.scoping_case_result(truth, buried)["passed"]
    # not the default persona's names
    assert "nimbus" not in json.dumps([by[t["id"]] for t in probes]).lower()


# --------------------------------------------------------------------------- runner and baselines

@pytest.mark.parametrize("name", HELD_OUT)
def test_baselines_meaningful(tmp_path, name):
    comp = {}
    for arm, kw in [("trivial", dict(agent="trivial")),
                    ("keyword_nofeedback", dict(agent="keyword_prefs", feedback=False)),
                    ("keyword", dict(agent="keyword")),
                    ("keyword_prefs", dict(agent="keyword_prefs")),
                    ("oracle", dict(agent="oracle"))]:
        r = run.run_arm(tmp_path / arm, 7, persona=name, **kw)
        assert r["arm"]["persona"] == name
        comp[arm] = (r["phases"]["evaluation"]["composite"], r["useful"])
    assert comp["oracle"] == (100.0, True)
    assert not comp["trivial"][1] and not comp["keyword"][1]
    assert comp["trivial"][0] < comp["keyword"][0] < comp["oracle"][0]
    assert comp["keyword_prefs"][0] > comp["keyword_nofeedback"][0]


@pytest.mark.parametrize("name", HELD_OUT)
def test_run_cli_persona_and_scoping(tmp_path, name, capsys):
    assert run.main(["--work-dir", str(tmp_path / "o"), "--agent", "oracle", "--persona", name,
                     "--scoping-case"]) == 0
    res = json.loads((tmp_path / "o/results.json").read_text())
    assert res["arm"]["persona"] == name and res["scoping_case"]["strict"]
    prof = json.loads((tmp_path / "o/mailbox/profile.json").read_text())
    assert prof["name"] == personas.get(name).load()["public"]["name"]
    # an external agent command sees the persona's mailbox and passes through score unchanged
    cmd = ("python -m testbeds.email_triage.baselines --agent keyword_prefs --mailbox {mailbox} "
           "--phase {phase} --out {out} --feedback {feedback} --prefs {prefs}")
    r = run.run_arm(tmp_path / "k", 7, agent_cmd=cmd, persona=name)
    assert r["arm"]["returncodes"] == {"calibration": 0, "evaluation": 0}
    direct = run.run_arm(tmp_path / "d", 7, agent="keyword_prefs", persona=name)
    assert r["phases"]["evaluation"]["composite"] == direct["phases"]["evaluation"]["composite"]
    rescored = score.score(json.loads((tmp_path / "k/truth/truth.json").read_text()),
                           {ph: json.loads((tmp_path / f"k/agent/predictions_{ph}.json").read_text())
                            for ph in ("calibration", "evaluation")}, [])
    assert rescored["phases"]["evaluation"]["composite"] == r["phases"]["evaluation"]["composite"]


@pytest.mark.parametrize("name", HELD_OUT)
def test_dry_run_valid(tmp_path, name):
    summary = run.dry_run(tmp_path, 7, out=lambda s: None, persona=name)
    assert summary["valid"] and summary["persona"] == name
    assert summary["composite_eval"]["oracle"] == 100.0
    assert summary["checks"]["obedient_caught"] is None
    assert summary["checks"]["web_path_matches_data_path"]
