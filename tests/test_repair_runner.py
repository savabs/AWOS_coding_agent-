"""repair/ wired into pack/runner.py (opt-in ``LiveRunner(repair=SkillRepair(...))``) on the fake Safari portal
of test_pack_runner: enrol on a clean run, repair a renamed control (rule tier / model tier), record it in the
skill so the next replay is model-free, refuse a risky repair, abstain on a deleted control. No real UI."""
import json

import test_pack_runner as TP
from scaffold.agent.desktop.pack import RUN_FAILED, RUN_OK
from scaffold.agent.desktop.pack import teach as T
from scaffold.agent.desktop.repair import Pick, Repairer, SkillRepair, greedy_picker

NAV = TP.NAV


def dash(nav):
    return TP.page("Dashboard", nav + "<h1>Workspace</h1><p>Team plan</p>")


def run(tmp_path, hook, pages=None):
    rt, saf, dl = TP.mk_rt(tmp_path, pages=pages or {})
    for f in dl.iterdir():
        f.unlink()
    src = TP.source()
    return TP.live(rt, tmp_path, repair=hook)(src, TP.SEP, TP.ctx_for(src, dl)), saf


def billing_target(tmp_path):
    prog = T.load_skill(TP.source(), tmp_path / "skills")
    return prog["version"], next(op["target"] for op in prog["setup"] if op["target"]["name"] == "Billing")


def test_without_the_hook_a_renamed_link_halts(tmp_path):
    TP.teach_acme(tmp_path)
    rt, saf, dl = TP.mk_rt(tmp_path, pages={TP.BASE + "/": dash(NAV.replace(">Billing<", ">Your billing ›<"))})
    src = TP.source()
    res = TP.live(rt, tmp_path)(src, TP.SEP, TP.ctx_for(src, dl))
    assert res.status == RUN_FAILED and "step s4" in res.reason and "repairs" not in res.detail


def test_enrol_then_rule_repair_then_model_free_replay(tmp_path):
    TP.teach_acme(tmp_path)
    res, _ = run(tmp_path, SkillRepair())                                  # clean run: fingerprint enrolled
    assert res.status == RUN_OK and [w["kind"] for w in res.detail["repairs"]["written"]] == ["enroll"]
    v, tgt = billing_target(tmp_path)
    assert v == 2 and tgt["fingerprint"]["role"] == "link" and tgt["fingerprint"]["siblings"]

    moved = {TP.BASE + "/": dash(NAV.replace(">Billing<", ">Your billing ›<"))}
    res, saf = run(tmp_path, SkillRepair(), moved)
    assert res.status == RUN_OK, res.reason
    rep = res.detail["repairs"]
    assert rep["model_calls"] == 0 and rep["written"][0]["tier"] == "rule"
    assert res.detail["downloads"] == ["ACME-000912.pdf"]
    v, tgt = billing_target(tmp_path)
    assert v == 3 and "Your billing ›" in tgt["aliases"]
    r = tgt["repairs"][-1]
    assert r["from"]["name"] == "Billing" and r["to"]["name"] == "Your billing ›" and r["reason"] and r["at"]

    res, _ = run(tmp_path, SkillRepair(picker=lambda f, s: 1 / 0), moved)  # next replay: strict alias, no repair
    assert res.status == RUN_OK and res.detail["repairs"]["events"] == [] and res.detail["repairs"]["model_calls"] == 0
    assert billing_target(tmp_path)[0] == 3                               # nothing new to write


def test_semantic_rename_needs_the_model_tier(tmp_path):
    TP.teach_acme(tmp_path)
    run(tmp_path, SkillRepair())
    renamed = {TP.BASE + "/": dash(NAV.replace(">Billing<", ">Invoices &amp; payments history<"))}
    res, _ = run(tmp_path, SkillRepair(), renamed)                         # rule-only: unsure -> halt
    assert res.status == RUN_FAILED and res.detail["repairs"]["written"] == []
    asked = []

    def picker(fp, short):
        asked.append([c.name for c in short])
        return Pick(next(i for i, c in enumerate(short) if c.name.startswith("Invoices")), "same link", calls=1)
    res, _ = run(tmp_path, SkillRepair(picker=picker), renamed)
    assert res.status == RUN_OK, res.reason
    assert asked and len(asked[0]) <= 5 and res.detail["repairs"]["model_calls"] == 1
    assert res.detail["repairs"]["written"][0]["tier"] == "model"
    assert "Invoices & payments history" in billing_target(tmp_path)[1]["aliases"]


def test_never_repairs_onto_pay_without_approval(tmp_path):
    TP.teach_acme(tmp_path)
    run(tmp_path, SkillRepair())
    v0 = billing_target(tmp_path)[0]
    risky = {TP.BASE + "/": dash(NAV.replace('<a href="/billing">Billing</a>', '<a href="/billing">Pay billing</a>'))}
    res, saf = run(tmp_path, SkillRepair(Repairer(greedy_picker())), risky)
    assert res.status == RUN_FAILED and "risk_escalation" in res.reason
    assert not any(u.startswith(TP.BASE + "/billing") for u in saf.loads) and billing_target(tmp_path)[0] == v0
    ev = res.detail["repairs"]["events"][0]
    assert ev["status"] == "needs_approval" and "destructive" in ev["reason"]


def test_deleted_control_abstains_and_nothing_is_written(tmp_path):
    TP.teach_acme(tmp_path)
    run(tmp_path, SkillRepair())
    v0 = billing_target(tmp_path)[0]
    gone = {TP.BASE + "/": dash(NAV.replace('<a href="/billing">Billing</a> ', ""))}
    res, _ = run(tmp_path, SkillRepair(picker=lambda f, s: Pick(None, "deleted", calls=1)), gone)
    assert res.status == RUN_FAILED and "step s" in res.reason
    assert billing_target(tmp_path)[0] == v0 and res.detail["repairs"]["written"] == []


def test_period_template_steps_are_never_repaired(tmp_path):
    TP.teach_acme(tmp_path)
    other = TP.invoices().replace("(September 2026)", "(Sept 2026)")
    res, _ = run(tmp_path, SkillRepair(Repairer(greedy_picker())), {TP.BASE + "/billing": other})
    assert res.status == RUN_FAILED and "step s" in res.reason
    assert not any(e.get("target", "").startswith("link 'Download PDF") for e in res.detail["repairs"]["events"])


def test_a_failed_run_drops_its_repairs(tmp_path):
    TP.teach_acme(tmp_path)
    run(tmp_path, SkillRepair())
    v0 = billing_target(tmp_path)[0]
    pages = {TP.BASE + "/": dash(NAV.replace(">Billing<", ">Your billing ›<")), TP.BASE + "/billing": TP.TERMS}
    res, _ = run(tmp_path, SkillRepair(), pages)                           # repaired, then an unknown dialog
    assert res.status == RUN_FAILED and res.detail["repairs"]["dropped"] == 1
    assert billing_target(tmp_path)[0] == v0
    json.dumps(res.detail["repairs"])                                      # report is JSON-safe
