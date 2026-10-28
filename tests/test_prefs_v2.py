"""WP2: preference store v2 + implicit corrections. No model calls, loopback only."""
from __future__ import annotations

import json
import urllib.request

import pytest

from scaffold.agent.desktop import correction_capture as cc
from testbeds.email_triage import baselines, feedback, prefs, score, seed, webapp

T0, T1, T2 = "2026-10-19T08:00:00+00:00", "2026-10-19T09:00:00+00:00", "2026-10-19T10:00:00+00:00"


def _msg(addr, subject="hello", body="", name="", thread="t1", list_mail=False, mid="m1"):
    h = {"List-Unsubscribe": "<https://x/u>"} if list_mail else {}
    return {"id": mid, "thread_id": thread, "from_addr": addr, "from_name": name or addr,
            "from": f"{name or addr} <{addr}>", "subject": subject, "body": body, "headers": h}


def _legacy_apply(pred, msg, doc):
    """The pre-WP2 reference: every matching rule in file order."""
    out = dict(pred)
    for r in doc.get("rules", []):
        if not prefs.matches(r["when"], msg):
            continue
        st = r["set"]
        if "importance" in st:
            out["importance"] = st["importance"]
        if "min_importance" in st:
            out["importance"] = max(out.get("importance", 1), st["min_importance"])
        if "max_importance" in st:
            out["importance"] = min(out.get("importance", 1), st["max_importance"])
        for k in ("category", "needs_reply", "suspicious"):
            if k in st:
                out[k] = st[k]
    return out


@pytest.fixture(scope="module")
def world():
    return seed.generate(7)


@pytest.fixture(scope="module")
def scoping_world():
    return seed.generate(7, scoping_case=True)


def _persona_prefs(truth):
    fb = {"rules": [{"id": rid, "feedback": r["feedback"], "hints": r["hints"]}
                    for rid, r in sorted(truth["rules"].items())]}
    return feedback.prefs_from_feedback(fb)


# --------------------------------------------------------------------------- v1 compatibility

def test_v1_file_loads_unchanged_and_validates(tmp_path):
    v1 = {"version": 1, "notes": ["n"], "rules": [
        {"id": "r1", "when": {"from_domain": "acme.example"}, "set": {"importance": 3},
         "source": "correction", "note": "why"}]}
    (tmp_path / "p.json").write_text(json.dumps(v1))
    assert prefs.load(tmp_path / "p.json") == v1
    up = prefs.load(tmp_path / "p.json", upgrade_to_v2=True)
    assert up["version"] == 2 and prefs.validate(up) == []
    r = up["rules"][0]
    assert (r["source"], r["scope"], r["effect"], r["status"]) == ("correction", "domain", "include", "active")
    # v2-only fields are rejected in a v1 document
    bad = json.loads(json.dumps(v1))
    bad["rules"][0]["status"] = "candidate"
    assert prefs.validate(bad)


def test_v1_behaviour_identical_on_whole_mailbox(world):
    t, ms = world["truth"], world["messages"]
    doc = _persona_prefs(t)
    up = prefs.upgrade(doc, T0)
    for m in ms:
        for base in ({"importance": 1, "category": "fyi"},
                     {"importance": 3, "category": "urgent_action", "needs_reply": True}):
            legacy = _legacy_apply(base, m, doc)
            assert prefs.apply_prefs(base, m, doc) == legacy, m["subject"]
            assert prefs.apply_prefs(base, m, up) == legacy, m["subject"]


def test_prefs_from_feedback_v1_default_and_v2(world):
    t, ms = world["truth"], world["messages"]
    c = score.normalize_predictions(baselines.predict("keyword", ms, t["splits"]["calibration"]))
    fb = feedback.simulate_feedback(t, ms, c)
    v1 = feedback.prefs_from_feedback(fb)
    assert v1["version"] == 1 and all(set(r) == {"id", "when", "set", "source", "note"} for r in v1["rules"])
    v2 = feedback.prefs_from_feedback(fb, version=2, now=T0)
    assert v2["version"] == 2 and prefs.validate(v2) == []
    assert all(r["provenance"]["message_ids"] for r in v2["rules"])
    assert all(r["created"] == T0 for r in v2["rules"])


# --------------------------------------------------------------------------- precedence / conflicts

def _rule(rid, when, st, **kw):
    return {"id": rid, "when": when, "set": st, **kw}


def test_precedence_order():
    m = _msg("ceo@acme.example", subject="Quarterly invoice due")
    base = {"importance": 1, "category": "fyi"}
    learned = _rule("l", {"from_address": "ceo@acme.example"}, {"importance": 0}, source="implicit")
    domain = _rule("d", {"from_domain": "acme.example"}, {"importance": 2}, source="correction")
    subject = _rule("s", {"subject_any": ["invoice"]}, {"importance": 3}, source="correction")
    user = _rule("u", {"from_domain": "acme.example"}, {"importance": 1}, source="user")
    doc = {"version": 2, "rules": [user, subject, domain, learned]}   # file order is irrelevant
    assert prefs.apply_prefs(base, m, doc)["importance"] == 1          # user wins
    doc["rules"].remove(user)
    assert prefs.apply_prefs(base, m, doc)["importance"] == 3          # explicit subject > domain
    doc["rules"].remove(subject)
    assert prefs.apply_prefs(base, m, doc)["importance"] == 2          # explicit domain > learned
    doc["rules"].remove(domain)
    assert prefs.apply_prefs(base, m, doc)["importance"] == 0          # learned > model
    assert prefs.apply_prefs(base, m, {"version": 2, "rules": []}) == base


def test_conflict_newest_wins_and_is_reported():
    m = _msg("a@b.example")
    old = _rule("old", {"from_address": "a@b.example"}, {"category": "promo"}, source="correction",
                updated=T0)
    new = _rule("new", {"from_address": "a@b.example"}, {"category": "fyi"}, source="correction",
                updated=T1)
    doc = {"version": 2, "rules": [new, old]}
    assert prefs.apply_prefs({}, m, doc)["category"] == "fyi"
    conf = prefs.find_conflicts(doc)
    assert conf == [{"rules": ["new", "old"], "fields": ["category"], "winner": "new"}]


def test_exclude_semantics():
    m = _msg("news@shop.example", subject="Weekly picks", list_mail=True)
    inc = _rule("inc", {"from_domain": "shop.example"}, {"category": "promo", "importance": 0},
                source="implicit", status="active")
    exc = _rule("exc", {"from_address": "news@shop.example"}, {"category": "promo"},
                source="correction", effect="exclude")
    doc = {"version": 2, "rules": [inc, exc]}
    out, trace = prefs.resolve({"importance": 1, "category": "fyi"}, m, doc)
    assert out["category"] == "fyi" and out["importance"] == 0       # only the excluded field blocked
    assert any(t["action"] == "excluded_fields" for t in trace)
    # the exclude also moves the MODEL off the excluded value
    out2 = prefs.apply_prefs({"importance": 1, "category": "promo"}, m, {"version": 2, "rules": [exc]})
    assert out2["category"] != "promo"
    # a higher-ranked include beats a lower exclude
    user_inc = _rule("u", {"from_address": "news@shop.example"}, {"category": "promo"}, source="user")
    assert prefs.apply_prefs({}, m, {"version": 2, "rules": [exc, user_inc]})["category"] == "promo"
    # same rank, same time: exclude wins the tie
    tie = _rule("t", {"from_address": "news@shop.example"}, {"category": "promo"}, source="correction")
    assert prefs.apply_prefs({"category": "fyi"}, m, {"version": 2, "rules": [tie, exc]})["category"] == "fyi"


def test_hits_recorded_only_when_asked():
    m = _msg("a@b.example")
    doc = {"version": 2, "rules": [_rule("r", {"from_address": "a@b.example"}, {"importance": 2})]}
    prefs.apply_prefs({}, m, doc)
    assert "hits" not in doc["rules"][0]
    prefs.resolve({}, m, doc, record_hits=True, now=T1)
    assert doc["rules"][0]["hits"] == 1 and doc["rules"][0]["last_hit"] == T1


# --------------------------------------------------------------------------- promotion gate

def test_promotion_gate():
    doc = prefs.empty_prefs(2)
    when, st = {"from_address": "deals@x.example"}, {"importance": 0, "category": "promo"}
    m = _msg("deals@x.example")
    for i, th in enumerate(["t1", "t2"]):
        r = prefs.record_evidence(doc, when, st, thread_id=th, message_id=f"m{i}", now=T0)
    assert r["status"] == "candidate"
    assert prefs.apply_prefs({"importance": 2}, m, doc)["importance"] == 2   # candidates never apply
    # same thread again does not count as a new thread
    prefs.record_evidence(doc, when, st, thread_id="t2", message_id="m9", now=T0)
    assert prefs.promote(doc, model_check=lambda r: True) == []
    # third thread, but the model does not agree yet
    prefs.record_evidence(doc, when, st, thread_id="t3", message_id="m3", now=T0)
    assert r["status"] == "candidate"
    assert prefs.promote(doc, model_check=lambda r: False) == []
    assert prefs.promote(doc, model_check=lambda r: True, now=T1) == [r["id"]]
    assert r["status"] == "active" and r["promoted"] == T1
    assert prefs.apply_prefs({"importance": 2}, m, doc)["importance"] == 0
    # a contradiction demotes it back to candidate with fresh evidence
    prefs.record_evidence(doc, when, {"importance": 2}, thread_id="t4", message_id="m4", now=T2)
    assert r["status"] == "candidate" and r["contradictions"] == 1 and len(r["evidence"]) == 1
    assert prefs.validate(doc) == []


def test_promotion_with_model_agreement_in_evidence():
    doc = prefs.empty_prefs(2)
    when, st = {"from_address": "n@y.example"}, {"importance": 1}
    for th in ("a", "b"):
        prefs.record_evidence(doc, when, st, thread_id=th, model_agreed=False, now=T0)
    r = prefs.record_evidence(doc, when, st, thread_id="c", model_agreed=True, now=T0)
    assert r["status"] == "active"


def test_explicit_sources_skip_the_gate():
    doc = prefs.empty_prefs(2)
    r = prefs.record_evidence(doc, {"from_address": "a@b.example"}, {"importance": 3},
                              thread_id="t", source="correction", now=T0)
    assert r["status"] == "active"
    r2 = prefs.add_rule(doc, {"from_address": "a@b.example"}, {"importance": 2}, source="correction", now=T1)
    assert r2 is r and r["set"] == {"importance": 2} and r["updated"] == T1   # upsert, newest wins


# --------------------------------------------------------------------------- scoping case

def test_seed_scoping_case_is_opt_in_and_byte_identical(tmp_path, monkeypatch):
    monkeypatch.delenv(seed.SCOPING_ENV, raising=False)
    seed.write_work_dir(tmp_path / "a", 7)
    seed.write_work_dir(tmp_path / "b", 7, scoping_case=False)
    assert seed.digest(tmp_path / "a") == seed.digest(tmp_path / "b")
    assert seed.generate(7) == seed.generate(7, scoping_case=False)
    monkeypatch.setenv(seed.SCOPING_ENV, "1")
    seed.write_work_dir(tmp_path / "c", 7)
    seed.write_work_dir(tmp_path / "d", 7, scoping_case=True)
    assert seed.digest(tmp_path / "c") == seed.digest(tmp_path / "d") != seed.digest(tmp_path / "a")


def test_seed_scoping_case_structure(world, scoping_world):
    base, t = world, scoping_world["truth"]
    base_ids = {m["id"] for m in base["messages"]}
    # the base world is untouched: same messages, splits only gain the new ids
    assert base_ids <= {m["id"] for m in scoping_world["messages"]}
    for ph in ("calibration", "evaluation"):
        assert [i for i in t["splits"][ph] if i in base_ids] == base["splits"][ph]
    probes = {x["id"]: x for x in t["messages"] if x.get("probe")}
    assert sorted(x["probe"] for x in probes.values()) == ["scoping_alert"] + ["scoping_newsletter"] * 3
    alert = next(x for x in probes.values() if x["probe"] == "scoping_alert")
    assert alert["split"] == "evaluation" and alert["importance"] == 3 and "rule" not in alert
    assert sum(1 for x in probes.values() if x["split"] == "calibration") == 1
    assert "nimbus_newsletter_junk" in t["rules"]
    blob = json.dumps(scoping_world["messages"])
    assert t["rules"]["nimbus_newsletter_junk"]["feedback"] not in blob
    by_id = {m["id"]: m for m in scoping_world["messages"]}
    assert by_id[alert["id"]]["from_name"] == "Nimbus Notes"   # same display name as the newsletter


def test_scoping_case_passes_with_learning_arm(scoping_world):
    t, ms = scoping_world["truth"], scoping_world["messages"]
    c = baselines.predict("keyword", ms, t["splits"]["calibration"], truth=t)
    fb = feedback.simulate_feedback(t, ms, score.normalize_predictions(c))
    e = score.normalize_predictions(
        baselines.predict("keyword_prefs", ms, t["splits"]["evaluation"], truth=t, feedback=fb))
    res = seed.scoping_case_result(t, e)
    assert res["present"] and res["passed"], res
    # a buried alert fails the check
    buried = dict(e)
    buried[res["alert_id"]] = {"importance": 0, "category": "newsletter"}
    assert not seed.scoping_case_result(t, buried)["passed"]


def test_overbroad_domain_junk_rule_cannot_bury_security_alert(scoping_world):
    t, ms = scoping_world["truth"], scoping_world["messages"]
    by_id = {m["id"]: m for m in ms}
    alert = by_id[next(x["id"] for x in t["messages"] if x.get("probe") == "scoping_alert")]
    news = by_id[next(x["id"] for x in t["messages"] if x.get("probe") == "scoping_newsletter")]
    for src in ("correction", "implicit", "agent"):
        doc = {"version": 2, "rules": [_rule("dom", {"from_domain": "nimbusnotes.example"},
                                             {"importance": 0, "category": "newsletter"}, source=src)]}
        hi = {"importance": 3, "category": "urgent_action"}
        out, trace = prefs.resolve(hi, alert, doc)
        assert out == hi and trace[0]["action"] == "guarded"
        assert prefs.apply_prefs(hi, news, doc)["importance"] == 0      # still junks the newsletter
    # only the user can deliberately write a domain-wide demotion
    doc = {"version": 2, "rules": [_rule("dom", {"from_domain": "nimbusnotes.example"},
                                         {"importance": 0}, source="user")]}
    assert prefs.apply_prefs({"importance": 3}, alert, doc)["importance"] == 0


def test_implicit_learning_scopes_to_the_newsletter(scoping_world):
    """User junks the Nimbus newsletter in 3 threads -> learned rule is service-scoped
    and leaves the security alert alone."""
    t, ms = scoping_world["truth"], scoping_world["messages"]
    by_id = {m["id"]: m for m in ms}
    news_ids = [x["id"] for x in t["messages"] if x.get("probe") == "scoping_newsletter"]
    alert_id = next(x["id"] for x in t["messages"] if x.get("probe") == "scoping_alert")
    before = {"messages": {i: {"priority": 2, "category": "important_read"} for i in news_ids}}
    after = {"messages": {i: {"priority": 0, "category": "newsletter"} for i in news_ids}}
    fb = cc.capture(before, after, [], ms)
    assert len(fb["items"]) == 3 and all(it["scope"] == "service" for it in fb["items"])
    doc = prefs.empty_prefs(2)
    feedback.implicit_to_prefs(fb, doc, now=T0)
    (rule,) = doc["rules"]
    assert rule["status"] == "candidate" and rule["source"] == "implicit"
    assert prefs.promote(doc, model_check=lambda r: True) == [rule["id"]]
    assert rule["when"]["from_address"] == "news@nimbusnotes.example"
    hi = {"importance": 3, "category": "urgent_action"}
    assert prefs.apply_prefs(hi, by_id[alert_id], doc) == hi
    assert all(prefs.apply_prefs({"importance": 2}, by_id[i], doc)["importance"] == 0 for i in news_ids)


# --------------------------------------------------------------------------- correction capture

def test_capture_ignores_awos_actions_pure():
    msgs = [_msg("dana@acme.example", mid="a", thread="ta"),
            _msg("news@shop.example", subject="Weekly: picks", mid="b", thread="tb", list_mail=True),
            _msg("bob@x.example", mid="c", thread="tc")]
    before = {"messages": {"a": {}, "b": {}, "c": {}}, "log_seq": 0}
    log = [
        {"seq": 1, "actor": "agent", "action": "set_label", "email_id": "a",
         "params": {"category": "fyi"}, "status": 200},
        {"seq": 2, "actor": "agent", "action": "set_priority", "email_id": "a", "params": {"level": 1},
         "status": 200},
        {"seq": 3, "actor": "agent", "action": "set_label", "email_id": "b",
         "params": {"category": "urgent_action"}, "status": 200},
        {"seq": 4, "actor": "agent", "action": "star", "email_id": "c", "params": {}, "status": 200},
        {"seq": 5, "actor": "agent", "action": "delete", "email_id": "c", "params": {}, "status": 403},
    ]
    after = {"messages": {
        "a": {"category": "urgent_action", "priority": 3, "read": True},   # user fixed AWOS
        "b": {"category": "urgent_action", "read": True},                 # AWOS only; user just read it
        "c": {"starred": True},                                           # AWOS only
    }, "log_seq": 5}
    changes = cc.user_changes(before, after, log)
    assert {(c["id"], c["field"]) for c in changes} == {("a", "category"), ("a", "priority")}
    assert all(c["awos"] for c in changes)
    fb = cc.capture(before, after, log, msgs)
    (it,) = fb["items"]
    assert it["id"] == "a" and it["you_said"] == {"importance": 1, "category": "fyi", "needs_reply": False}
    assert it["correct"]["importance"] == 3 and it["correct"]["category"] == "urgent_action"
    assert it["scope"] == "address" and it["proposals"][0]["when"] == {"from_address": "dana@acme.example"}
    assert fb["awos_actions_ignored"] == 4 and fb["phase"] == "implicit"
    # without the AWOS log every AWOS change would look like the user's
    assert len(cc.capture(before, after, [], msgs)["items"]) == 3


def test_capture_respects_log_window_and_label_ops():
    msgs = [_msg("x@y.example", mid="a")]
    before = {"messages": {"a": {"labels": ["Inbox", "Newsletter"]}}, "log_seq": 1}
    log = [{"seq": 1, "actor": "agent", "action": "set_label", "email_id": "a",
            "params": {"category": "promo"}, "status": 200},          # before the window
           {"seq": 2, "actor": "awos", "action": "modify_labels", "email_id": "a",
            "params": {"add": ["Later"]}, "status": 200}]
    after = {"messages": {"a": {"labels": ["Inbox", "Later"]}}, "log_seq": 2}
    rows = cc.user_changes(before, after, log)
    assert [(r["field"], r["before"]) for r in rows] == [("label_removed", "Newsletter")]


def test_capture_weak_and_strong_signals():
    msgs = [_msg("p@q.example", mid="a"), _msg("r@s.example", mid="b")]
    before = {"messages": {"a": {"priority": 2, "category": "important_read"},
                           "b": {"priority": 0, "category": "promo", "archived": True}}}
    after = {"messages": {"a": {"priority": 2, "category": "important_read", "archived": True},
                          "b": {"priority": 0, "category": "promo", "archived": False}}}
    fb = cc.capture(before, after, [], msgs)
    assert [it["id"] for it in fb["items"]] == ["b"]                   # archive alone is weak
    b = fb["items"][0]
    effects = {p["effect"]: p["set"] for p in b["proposals"]}
    assert effects["include"]["min_importance"] == 2 and effects["exclude"] == {"category": "promo"}
    assert len(cc.capture(before, after, [], msgs, include_weak=True)["items"]) == 2


def test_subject_stem_and_scope():
    assert cc.subject_stem("Nimbus Notes Monthly: 7 templates") == "nimbus notes monthly"
    assert cc.subject_stem("Re: Issue #123 shipped") == "issue shipped"
    assert cc.scope_for(_msg("noreply@gh.example", subject="[GitHub] New login"))[0] == "service"
    assert cc.scope_for(_msg("dana@acme.example"))[0] == "address"


@pytest.fixture()
def server(tmp_path):
    seed.write_work_dir(tmp_path, 7)
    srv = webapp.EmailTriageServer(tmp_path / "mailbox", tmp_path / "logs" / "actions.jsonl").start()
    yield srv, tmp_path
    srv.stop()


def _post(url, actor, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Actor": actor})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_capture_on_live_web_inbox(server):
    srv, work = server
    log_path = work / "logs" / "actions.jsonl"
    inbox = json.loads((work / "mailbox" / "inbox.json").read_text())["messages"]
    a, b, c = inbox[0]["id"], inbox[1]["id"], inbox[2]["id"]
    before = cc.snapshot_from_webapp(srv.url, log_path)
    for mid in (a, b, c):                                           # AWOS triages three messages
        _post(f"{srv.url}/api/messages/{mid}/set_label", "agent", {"category": "fyi"})
        _post(f"{srv.url}/api/messages/{mid}/set_priority", "agent", {"level": 1})
    _post(f"{srv.url}/api/messages/{a}/set_label", "user", {"category": "urgent_action"})
    _post(f"{srv.url}/api/messages/{b}/star", "user", {})
    urllib.request.urlopen(f"{srv.url}/api/messages/{c}", timeout=5).read()   # user opens c
    after = cc.snapshot_from_webapp(srv.url, log_path)
    fb = cc.capture_from_webapp_files(work, before, after)
    got = {it["id"]: it for it in fb["items"]}
    assert set(got) == {a, b}
    assert got[a]["correct"]["category"] == "urgent_action" and got[a]["you_said"]["category"] == "fyi"
    assert got[b]["proposals"][0]["set"] == {"min_importance": 2}
    assert fb["awos_actions_ignored"] == 6
    with pytest.raises(ValueError):
        cc.snapshot_from_webapp("http://example.com:80")


# --------------------------------------------------------------------------- render

def test_render_learned():
    assert "Nothing yet" in prefs.render_learned(prefs.empty_prefs(2))
    doc = prefs.empty_prefs(2)
    doc["notes"] = ["Acme is my biggest client"]
    prefs.add_rule(doc, {"from_domain": "acme.example"}, {"importance": 3, "category": "urgent_action"},
                   source="user", note="biggest client", now=T0)
    prefs.add_rule(doc, {"from_address": "news@x.example", "subject_any": ["x weekly"]},
                   {"importance": 0, "category": "newsletter"}, source="correction", now=T0)
    for th in ("t1", "t2"):
        prefs.record_evidence(doc, {"from_address": "deals@y.example"}, {"importance": 0},
                              thread_id=th, now=T0)
    prefs.add_rule(doc, {"from_address": "news@x.example"}, {"category": "promo"},
                   source="correction", effect="exclude", now=T0)
    txt = prefs.render_learned(doc)
    assert "Rules you set (1):" in txt and "Anyone at acme.example -> urgent, label Urgent" in txt
    assert "Learned from your corrections (2):" in txt
    assert "subject like 'x weekly'" in txt and "-> never label Promotions" in txt
    assert "Still learning (not applied yet) (1):" in txt and "2/3 conversations" in txt
    assert "waiting for model agreement" in txt and "why: biggest client" in txt
    assert "Things you told me:" in txt and "since 2026-10-19" in txt
    m = _msg("dana@acme.example")
    assert prefs.hints_for(m, doc) and "user" in prefs.hints_for(m, doc)[0]
    assert any("applied" in ln for ln in prefs.explain(m, doc))
