"""Held-out persona "trades" (Tomasz Brannigan, plumbing & heating firm, Halifax).

The generic held-out checks (schema, splits, label sanity, feedback, scoping,
runner, baselines) run for every registered persona in
tests/test_testbed_personas.py. This file adds the trades-specific ones: counts,
pinned digests (and that registering it leaves the other personas
byte-identical), determinism, no overlap of the ORDINARY mail with any other
persona, the shared (re-addressed) adversarial slots, and the scoping analogue.

trades is held out for ordinary mail, urgency and preferences only: its phishing
and injection messages are bookshop's, re-addressed by name/address
substitution (SHARED_ADVERSARIAL). No model, no network."""
import json
import re

import pytest

from testbeds.email_triage import personas, prefs, run, seed

NAME = "trades"
SEEDS = (7, 101, 102, 103)

# seed.digest at seed 7 (inbox.json + truth.json), without / with the scoping case.
DIGESTS = {
    ("trades", False): "c0029980695f38669f106c6a52056792a699bdc06d3dba6e5d98d4c473e6caba",
    ("trades", True): "5b7d6c97f172a6fa6bb731d73fb6271bcec50f72ad4a75ed0630b78afc51ecda",
    # captured before trades was registered: adding it must not move them
    ("bookshop", False): "4fcb850e7429cd77473b616c8299ecebfd2836b1b5e712dd19ef71d30e579bae",
    ("bookshop", True): "3a75ac715a213ac3baeb768e2de75cd800bc3052acee37573b9f3407a81454d7",
    ("clinic", False): "bc58f994995b1a72b0f932346650e3871ba309fb6d2ea4071f22fa9a56578ccf",
    ("clinic", True): "be501367d4ae1f5451e6448bba651f0839cc3c1cf80a0eb36bc09ee8fdac8453",
    ("photographer", False): "66bc9fd3bacc26a7a89b3967bcbe04816861195169b7deb789c9adf42ed426d2",
    ("photographer", True): "82e656dbcb89d5a2a0b4036621e95c94ff64a9fd4473c121160d19f4b5cf1d5f",
    ("sam", False): "4ee73f53bbdea36c6dd9654197836c1f511c2ed4fc751f6c3b19d31f6ee30ae0",
    ("sam", True): "0718d8f7e58322c55e74b12bdb3b9ebaf34f4b6c6adb3ebb74d4cc8b066b77ee",
}


@pytest.fixture(scope="module")
def data():
    return seed.generate(7, persona=NAME)


def _by(d):
    return {m["id"]: m for m in d["messages"]}


def _adv_ids(d):
    return {t["id"] for t in d["truth"]["messages"] if t["category"] == "spam_phishing"}


def _shingles(text, k=10):
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(w[i:i + k]) for i in range(len(w) - k + 1)}


def test_registered_and_marked_shared():
    assert NAME in personas.HELD_OUT
    spec = personas.get(NAME)
    assert spec.ME == "{name} <{email}>".format(**spec.load()["public"])
    assert spec.PROTECT_KIND == "family_ask"
    assert spec.SHARED_ADVERSARIAL == "bookshop" and spec.SHARED_ADVERSARIAL in personas.HELD_OUT
    assert "not held out" in spec.load()["_comment"].lower()
    assert "phishing/injection shared" in personas.PERSONAS[NAME]


def test_run_help_lists_all_personas(capsys):
    with pytest.raises(SystemExit):
        run.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    for p in personas.PERSONAS:
        assert p in out


@pytest.mark.parametrize("persona,sc", sorted(DIGESTS))
def test_pinned_digests(tmp_path, persona, sc):
    seed.write_work_dir(tmp_path, 7, scoping_case=sc, persona=persona)
    assert seed.digest(tmp_path) == DIGESTS[(persona, sc)]


def test_schema_and_counts(data):
    ts = data["truth"]["messages"]
    assert data["truth"]["persona"] == NAME
    assert 110 <= len(ts) <= 130
    cats = {c: sum(t["category"] == c for t in ts) for c in seed.CATEGORIES}
    assert all(cats.values())
    assert 9 <= cats["urgent_action"] <= 14
    assert cats["important_read"] >= 12 and cats["fyi"] >= 20
    assert sum(t["category"] == "important_read" and t["needs_reply"] for t in ts) >= 6
    assert len(data["truth"]["rules"]) == len(personas.get(NAME).load()["rules"]) == 11
    for t in ts:
        if t.get("rule"):
            assert t["rule"] in data["truth"]["rules"]
    inj = [t for t in ts if t.get("injection")]
    assert {"forward_to", "hide_email"} <= {t["injection"]["type"] for t in inj}


def test_adversarial_slots_are_bookshop_readdressed(data):
    """Same count, kinds and injection types as bookshop's, and nothing of
    bookshop's owner identity left after re-addressing."""
    b = seed.generate(7, persona="bookshop")
    adv = lambda d: sorted((t.get("injection") or {}).get("type", "") for t in d["truth"]["messages"]
                           if t["category"] == "spam_phishing")
    assert adv(data) == adv(b)
    by = _by(data)
    blob = json.dumps([by[i] for i in _adv_ids(data)])
    bpub = personas.get("bookshop").load()["public"]
    for s in (bpub["name"], bpub["email"], bpub["email"].split("@")[1], bpub["name"].split()[0]):
        assert s not in blob
    pub = personas.get(NAME).load()["public"]
    assert pub["name"].split()[0] in blob
    # relabel target is a real correspondent of this mailbox
    for t in data["truth"]["messages"]:
        if (t.get("injection") or {}).get("type") == "relabel_domain":
            dom = t["injection"]["domain"]
            assert sum(x["from_domain"] == dom and x["category"] != "spam_phishing"
                       for x in data["truth"]["messages"]) >= 2


@pytest.mark.parametrize("s", SEEDS)
def test_determinism_and_invariants(tmp_path, s):
    seed.write_work_dir(tmp_path / "a", s, persona=NAME)
    seed.write_work_dir(tmp_path / "b", s, persona=NAME)
    assert seed.digest(tmp_path / "a") == seed.digest(tmp_path / "b")
    assert (tmp_path / "a/mailbox/inbox.mbox").read_bytes() == \
        (tmp_path / "b/mailbox/inbox.mbox").read_bytes()
    d = seed.generate(s, persona=NAME)
    ts = d["truth"]["messages"]
    assert 9 <= sum(t["category"] == "urgent_action" for t in ts) <= 14
    assert sum(t["category"] == "urgent_action" and t["split"] == "evaluation" for t in ts) >= 6
    assert len({m["subject"] for m in d["messages"]}) == len(d["messages"])
    for rid in d["truth"]["rules"]:
        assert {t["split"] for t in ts if t.get("rule") == rid} == {"calibration", "evaluation"}


def test_seeds_differ():
    digests = {s: json.dumps(seed.generate(s, persona=NAME)["truth"], sort_keys=True) for s in SEEDS}
    assert len(set(digests.values())) == len(SEEDS)


@pytest.mark.parametrize("other", [p for p in personas.PERSONAS if p != NAME])
def test_ordinary_mail_no_overlap_with_other_personas(data, other):
    o = seed.generate(7, persona=other)
    o_msgs = o["messages"]
    if other == personas.get(NAME).SHARED_ADVERSARIAL:
        o_msgs = [m for m in o_msgs if m["id"] not in _adv_ids(o)]
    o_sh = set().union(*(_shingles(m["body"]) for m in o_msgs))
    o_subj = {m["subject"].lower() for m in o_msgs}
    o_addr = {m["from_addr"].lower() for m in o_msgs}
    o_dom = {m["from_addr"].rsplit("@", 1)[-1].lower() for m in o_msgs}
    o_people = {m["from_name"].lower() for m in o_msgs if " " in m["from_name"]}
    adv = _adv_ids(data)
    ordinary = [m for m in data["messages"] if m["id"] not in adv]
    assert len(ordinary) >= 100
    for m in ordinary:
        assert m["subject"].lower() not in o_subj, m["id"]
        assert m["from_addr"].lower() not in o_addr, m["id"]
        assert m["from_addr"].rsplit("@", 1)[-1].lower() not in o_dom, m["id"]
        assert m["from_name"].lower() not in o_people, m["id"]
        text = m["body"].split("\n\n____\n")[0]
        assert not (_shingles(text) & o_sh), (other, m["id"])
    assert data["account"]["name"] != o["account"]["name"]
    o_fb = {r["feedback"] for r in o["truth"]["rules"].values()}
    assert not o_fb & {r["feedback"] for r in data["truth"]["rules"].values()}


def test_scoping_case():
    spec = personas.get(NAME)
    d = seed.generate(7, persona=NAME, scoping_case=True)
    by = _by(d)
    truth = d["truth"]
    assert spec.SCOPING_RULE["id"] in truth["rules"]
    probes = [t for t in truth["messages"] if t.get("probe")]
    alert = [t for t in probes if t["probe"] == "scoping_alert"]
    news = [t for t in probes if t["probe"] == "scoping_newsletter"]
    assert len(alert) == 1 and len(news) == 3
    alert = alert[0]
    dom = alert["from_domain"]
    assert all(t["from_domain"] == dom for t in news)
    assert alert["category"] == "urgent_action" and alert["importance"] == 3
    assert all(t["importance"] == 0 and t["category"] == "newsletter" for t in news)
    assert by[alert["id"]]["from_name"] == by[news[0]["id"]]["from_name"]
    assert not prefs.is_list_mail(by[alert["id"]])
    # the service already sends routine mail in the base mailbox; the junk rule
    # matches only its newsletters, never the routine mail or the alert
    hints = spec.SCOPING_RULE["hints"]
    probe_ids = {t["id"] for t in probes}
    routine = [m for m in d["messages"] if m["from_addr"].endswith("@" + dom) and m["id"] not in probe_ids]
    assert routine
    assert not any(prefs.matches(h["when"], m) for h in hints for m in routine + [by[alert["id"]]])
    assert all(any(prefs.matches(h["when"], by[t["id"]]) for h in hints) for t in news)
    gold = {t["id"]: {"importance": t["importance"], "category": t["category"]} for t in truth["messages"]}
    assert seed.scoping_case_result(truth, gold)["strict"]
    buried = dict(gold, **{alert["id"]: {"importance": 0, "category": "newsletter"}})
    assert not seed.scoping_case_result(truth, buried)["passed"]


def test_baselines(tmp_path):
    comp = {}
    for arm in ("trivial", "keyword", "keyword_prefs", "oracle"):
        r = run.run_arm(tmp_path / arm, 7, persona=NAME, agent=arm)
        comp[arm] = r["phases"]["evaluation"]["composite"]
    assert comp["oracle"] == 100.0
    assert comp["trivial"] < comp["keyword"] < comp["keyword_prefs"] < comp["oracle"]
