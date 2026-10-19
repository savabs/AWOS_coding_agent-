"""WP5: demo -> skill generaliser (induction.py), deterministic skill runner (skill_runner.py),
halt -> "show me the fix" -> versioned skill. No model calls anywhere: the one bounded agent
step is a pluggable callback, faked here and counted."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from testbeds.teach_once import DEMO_INSTANCE, INSTANCES, TASKS  # noqa: E402
from testbeds.teach_once.baselines import _parse_invoice  # noqa: E402
from testbeds.teach_once.browser import Page  # noqa: E402
from testbeds.teach_once.compiler import compile_trace, induce_skill, render_skill  # noqa: E402
from testbeds.teach_once.demonstrate import demo_t1, record_demo  # noqa: E402
from testbeds.teach_once.induction import (SkillLibrary, apply_param_decisions, induce, match_profile,  # noqa: E402
                                           normalize_value, program_sha, render_program, teach)
from testbeds.teach_once.seed import seed_instance  # noqa: E402
from testbeds.teach_once.skill_runner import (INJECTIONS, evaluate, ground_role_name,  # noqa: E402
                                              halt_teach_live, injector, run_instance)
from testbeds.teach_once.trace import Step, Trace  # noqa: E402

pytestmark = pytest.mark.timeout(120)


@pytest.fixture(scope="module")
def demos(tmp_path_factory):
    run = tmp_path_factory.mktemp("wp5_demos")
    traces = {t: record_demo(run, t) for t in TASKS}
    progs = {}
    for t, tr in traces.items():
        r = induce([tr])
        assert not r.refused, (t, r.record_next)
        progs[t] = r.program
    return run, traces, progs


class CountingAgent:
    """Fake bounded agent step (stands in for a model; nothing is called). Counts invocations."""

    def __init__(self):
        self.calls = 0

    def __call__(self, req: dict) -> dict:
        self.calls += 1
        assert req["kind"] == "extract_item" and req["max_calls"] == 1

        class _P:
            def text(_self):
                return req["text"]
        inv = _parse_invoice(_P())
        if not inv:
            return {"not_an_item": True}
        return {"values": {"vendor": inv["vendor"], "invoice_number": inv["number"],
                           "invoice_date": inv["date"], "amount": inv["amount"]}}


# ============================================================== induction ==

def test_induced_programs_shape(demos):
    _, _, progs = demos
    p1, p2, p3 = progs["t1_statements"], progs["t2_orders"], progs["t3_invoices"]
    L1 = p1["loop"]
    assert L1["source"] == {"kind": "folder", "folder": "Downloads"}
    assert sorted(pr["kind"] for pr in L1["profiles"]) == [".csv", ".pdf"]      # divergent items -> guarded layouts
    assert L1["on_unmatched"] == "skip" and "agent_step" not in L1               # the demo left the photo alone
    csv_prof = next(pr for pr in L1["profiles"] if pr["kind"] == ".csv")
    assert csv_prof["anchors"]["closing_balance"] == [{"kind": "cell", "column": "balance", "row": "last"}]
    assert p2["loop"] is None and {g["relation"] for g in p2["globals"]} == {"monday_of(today)", "sunday_of(today)"}
    assert any(o.get("aggregate") for o in p2["setup"]) and any(o.get("lines") for o in p2["setup"])
    L3 = p3["loop"]
    assert L3["source"]["kind"] == "table_links" and L3["source"]["table"] == "Inbox"
    assert L3["on_unmatched"] == "halt" and L3["agent_step"]["max_calls_per_item"] == 1
    assert L3["skip_rules"][0]["column"] == "Invoice number"
    # the rejected save + retype from the demo collapse into one type + one save
    names = [o["target"].get("name") for o in L3["body"] if o["action"] in ("type", "click")]
    assert names.count("Save bill") == 1 and names.count("Invoice date") == 1
    assert "for each item" in render_program(p3) and p3["provenance"][0]["kind"] == "induced"


def test_layout_profiles_match_new_formats_and_refuse_ambiguity(demos):
    _, _, progs = demos
    p1 = progs["t1_statements"]
    params = {p["name"]: p for p in p1["params"]}
    pdf = next(pr for pr in p1["loop"]["profiles"] if pr["kind"] == ".pdf")
    text = ("HARBOR SAVINGS & LOAN\nMonthly account statement\nAccount ending in 3302\n"
            "Statement period: 01/08/2026 - 31/08/2026\nOpening balance: $1.00\nClosing balance: $48,210.07\nPage 1 of 1")
    r = match_profile(pdf, text, params, ".pdf")
    assert r["status"] == "ok" and r["values"] == {"statement_date": "2026-08-31", "bank": "harbor-savings-loan",
                                                   "account": "3302", "closing_balance": "48210.07"}
    notice = "CITY OF SPRINGFIELD\nProperty tax notice\nParcel ending in 4411\nNotice date: July 1, 2026"
    assert match_profile(pdf, notice, params, ".pdf")["status"] == "nomatch"
    # a date that two formats read differently is never guessed
    assert normalize_value("date", "04/05/2026", {"raw_formats": []}) == (None, "ambiguous")
    assert normalize_value("date", "04/05/2026", {"raw_formats": ["%d/%m/%Y"]}) == ("2026-05-04", "")
    amb = text.replace("Statement period: 01/08/2026 - 31/08/2026", "Statement period: 01/08/2026 - 04/09/2026")
    assert match_profile(pdf, amb, {**params, "statement_date": dict(params["statement_date"], raw_formats=[])},
                         ".pdf")["status"] == "ambiguous"


def test_param_proposals_one_batch_and_decisions(demos, tmp_path):
    _, traces, _ = demos
    calls = []

    def confirm(batch):
        calls.append(batch)
        return {"username": {"action": "input", "name": "portal user"}}
    r = induce([traces["t2_orders"]], confirm=confirm)
    assert len(calls) == 1                                       # ONE batch
    kinds = {(p["name"], p["kind"], p["default"]) for p in calls[0]}
    assert ("from_date", "derived", "confirm") in kinds and ("username", "constant_field", "constant") in kinds
    assert all("*" in p["example"] or len(p["example"]) <= 2 for p in calls[0])   # values masked
    prog = r.program
    op = next(o for o in prog["setup"] if (o.get("target") or {}).get("name") == "Username")
    assert op["value"] == "{portal_user}" and any(p["scope"] == "input" for p in prog["params"])
    # without the run input the runner halts instead of replaying the demo value
    res = run_instance(prog, tmp_path / "a", "t2_orders", "i02")
    assert res["run"]["status"] == "HALTED" and res["run"]["halt"]["reason"] == "missing_input"
    # rename a derived parameter: templates, anchors and loop params follow
    p3 = induce([traces["t3_invoices"]]).program
    p3r = apply_param_decisions(p3, {"invoice_date": {"action": "rename", "name": "bill_date"}})
    assert "{bill_date}" in json.dumps(p3r["loop"]["body"]) and "{invoice_date}" not in json.dumps(p3r)
    assert all("bill_date" in pr["anchors"] for pr in p3r["loop"]["profiles"])
    assert run_instance(p3r, tmp_path / "b", "t3_invoices", "i03")["success"]


def test_induction_refuses_underdetermined(demos):
    _, traces, _ = demos
    # a per-item value that nothing on screen explains, different for each item -> refuse
    tr = copy.deepcopy(traces["t3_invoices"])
    for s in tr.steps:
        if s.action == "type" and s.value == "Cloudnine Hosting":
            s.value = "Zeta Unseen Ltd"
    r = induce([tr])
    assert r.refused and r.program is None
    assert any("Record another example" in x for x in r.record_next), r.record_next
    # two demos that disagree on a consequential step -> refuse with a record-next prompt
    t1 = traces["t1_statements"]
    t1b = copy.deepcopy(t1)
    cut = max(s.i for s in t1b.steps if s.action == "append_rows")
    t1b.steps = [s for s in t1b.steps if s.i != cut]
    r2 = induce([t1, t1b])
    assert r2.refused and any("when to" in x or "Record another example" in x for x in r2.record_next), r2.record_next
    assert induce([]).refused


def test_multi_demo_induction_merges(demos, tmp_path):
    _, traces, _ = demos
    I = seed_instance(tmp_path / "d2", "t1_statements", "i03")
    tr2 = demo_t1(I)
    r = induce([traces["t1_statements"], tr2])
    assert not r.refused, r.record_next
    prog = r.program
    assert len(prog["provenance"][0]["demos"]) == 2
    assert {d["instance"] for d in prog["provenance"][0]["demos"]} == {"i01", "i03"}
    assert len(prog["loop"]["profiles"]) == 2                     # identical layouts deduplicated
    for inst in ("i05", "i08"):
        assert run_instance(prog, tmp_path / "run", "t1_statements", inst)["success"], inst


# ================================================================= runner ==

def _page(html: str) -> Page:
    return Page("http://127.0.0.1:1/x", 200, f"<html><body><main>{html}</main></body></html>")


def test_grounding_ladder_refuses_ambiguity():
    two = _page('<form><button>Save</button></form><form><button>Save</button></form>')
    g = ground_role_name(two, {"role": "AXButton", "name": "Save"})
    assert g.status == "ambiguous" and g.candidates == ["Save", "Save"]
    renamed = _page('<form><button>Cancel</button><button>Save</button></form>')
    g = ground_role_name(renamed, {"role": "AXButton", "name": "Save bill"})
    assert g.status == "ok" and g.rung == "fuzzy" and g.element["name"] == "Save"
    assert ground_role_name(renamed, {"role": "AXButton", "name": "Save bill"}, fuzzy=False).status == "missing"
    fields = _page('<form><label for="a">Invoice number</label><input id="a" name="a">'
                   '<label for="b">Bill date</label><input id="b" name="b"></form>')
    tgt = {"role": "AXTextField", "name": "Invoice date"}
    assert ground_role_name(fields, tgt).status == "ambiguous"                # tie: never pick
    g = ground_role_name(fields, tgt, exclude=["Invoice number"])            # another step's target
    assert g.status == "ok" and g.element["name"] == "Bill date"
    dz = _page('<form><button>Delete bill PS-1</button><button>Help</button></form>')
    assert ground_role_name(dz, {"role": "AXButton", "name": "Save bill PS-1"}).status == "missing"   # never destructive
    alias = _page('<a href="/e">Download CSV</a><a href="/f">CSV help</a>')
    t = {"role": "AXLink", "name": "Export CSV", "aliases": ["Download CSV"]}
    assert ground_role_name(alias, t, fuzzy=False).rung == "alias"


@pytest.mark.timeout(120)
def test_held_out_pass_rates_zero_model_calls(demos, tmp_path):
    """Acceptance: every task x every instance with the v1 skill, no models: pass rate well above
    naive replay (0/24 held out) and ZERO model calls on every run."""
    _, _, progs = demos
    passed = {t: 0 for t in TASKS}
    for t in TASKS:
        for inst in INSTANCES:
            r = run_instance(progs[t], tmp_path, t, inst)
            assert r["run"]["model_calls"] == 0
            assert r["verify"]["violations"] == [], (t, inst, r["verify"]["violations"])
            if inst == DEMO_INSTANCE:
                assert r["success"] and r["run"]["status"] == "VERIFIED", (t, r["run"].get("halt"))
                continue
            if r["success"]:
                passed[t] += 1
                assert r["run"]["status"] == "VERIFIED"
            else:
                assert r["run"]["status"] == "HALTED", (t, inst)          # failures stop; they never guess
    assert passed["t1_statements"] == 8 and passed["t2_orders"] >= 7 and passed["t3_invoices"] >= 4, passed
    assert sum(passed.values()) >= 19


def test_bounded_agent_step_is_counted_and_unused_on_healthy_runs(demos, tmp_path):
    _, _, progs = demos
    p3 = progs["t3_invoices"]
    healthy = CountingAgent()
    r = run_instance(p3, tmp_path, "t3_invoices", "i03", agent=healthy)
    assert r["success"] and r["run"]["model_calls"] == 0 and healthy.calls == 0
    novel = CountingAgent()
    r = run_instance(p3, tmp_path, "t3_invoices", "i02", agent=novel)      # one invoice in an unseen layout
    assert r["success"] and r["run"]["model_calls"] == novel.calls == 1
    # file task: no agent step declared -> never called
    t1 = CountingAgent()
    assert run_instance(progs["t1_statements"], tmp_path, "t1_statements", "i07", agent=t1)["success"] and t1.calls == 0


def test_idempotent_resume_does_not_duplicate_effects(demos, tmp_path):
    _, _, progs = demos
    from testbeds.teach_once.run import verifier
    from testbeds.teach_once.skill_runner import run_program_on
    from testbeds.teach_once.webapps import Servers
    for t in ("t1_statements", "t2_orders", "t3_invoices"):
        I = seed_instance(tmp_path, t, "i03")
        with Servers(I) as urls:
            a, _ = run_program_on(progs[t], I, urls)
            b, _ = run_program_on(progs[t], I, urls)          # second run on the finished state
        assert a["status"] == "VERIFIED" and b["status"] != "HALTED", (t, b.get("halt"))
        assert verifier.verify_instance(I.hidden, I.home)["success"], t


# ============================================================ halt -> teach ==

@pytest.mark.parametrize("task", TASKS)
def test_injected_dialog_halts_then_passes_after_one_fix(demos, tmp_path, task):
    _, _, progs = demos
    inst = INJECTIONS[task]["instance"]
    assert run_instance(progs[task], tmp_path / "plain", task, inst)["success"]     # healthy without the dialog
    out = halt_teach_live(progs[task], tmp_path / "live", task, inst, env_wrap=injector(task),
                          gate_dir=tmp_path / "gates", require_canary_success=True)
    assert out["first"]["status"] == "HALTED" and out["halt"]["reason"] == "unknown_dialog", out.get("halt")
    assert out["halt"]["new_text"], "the halt report names what is new on screen"
    assert len(out["fix_steps"]) == 1                                               # one fix demonstration
    assert out["teach"]["status"] == "promoted" and out["teach"]["change"]["kind"] == "handler", out["teach"]
    gate = out["teach"]["gate"]
    assert gate["demo"]["success"] and gate["demo"]["model_calls"] == 0
    assert gate["canary"]["success"] and gate["canary"]["handlers_fired"] == 1      # fresh copy WITH the dialog
    assert out["resume"]["success"] and out["resume"]["model_calls"] == 0
    v2 = out["program"]
    assert v2["version"] == 2 and v2["parent"]["sha256"] == program_sha(progs[task])
    assert v2["provenance"][-1]["kind"] == "halt_teach" and v2["provenance"][-1]["gate"]["ok"]
    # the dialog is guarded: on a healthy instance the handler never fires and nothing changes
    r = run_instance(v2, tmp_path / "healthy", task, DEMO_INSTANCE)
    assert r["success"] and r["run"]["handlers_fired"] == [] and r["run"]["model_calls"] == 0


def test_native_variants_taught_once_generalise(demos, tmp_path):
    _, _, progs = demos
    # t2 i08: a 'password expires' interstitial after sign-in
    o = halt_teach_live(progs["t2_orders"], tmp_path / "t2", "t2_orders", "i08", gate_dir=tmp_path / "g")
    assert o["halt"]["reason"] == "unknown_dialog" and o["teach"]["change"]["kind"] == "handler"
    assert o["result"] == "pass" and o["teach"]["gate"]["canary_success"]
    # t3 i02: an invoice in a layout no demo showed -> the user enters it once -> new layout
    o = halt_teach_live(progs["t3_invoices"], tmp_path / "t3", "t3_invoices", "i02", gate_dir=tmp_path / "g")
    assert o["halt"]["reason"] == "unmatched_item" and o["teach"]["change"]["kind"] == "item_profile", o["teach"]
    assert o["result"] == "pass"
    v2 = o["program"]
    for inst in ("i05", "i08"):                    # same unseen layout elsewhere: no further teaching needed
        assert run_instance(v2, tmp_path / "v2", "t3_invoices", inst)["success"], inst
    # t3 i06: a shipping notice (not an invoice) -> the user leaves it -> taught skip guard
    o = halt_teach_live(v2, tmp_path / "t3b", "t3_invoices", "i06", gate_dir=tmp_path / "g")
    assert o["teach"]["change"]["kind"] == "skip_guard" and o["result"] == "pass", o.get("teach")
    v3 = o["program"]
    assert v3["version"] == 3 and [p["kind"] for p in v3["provenance"]] == ["induced", "halt_teach", "halt_teach"]
    lib = SkillLibrary(tmp_path / "lib")
    for p in (progs["t3_invoices"], v2, v3):
        lib.save(p)
    assert lib.versions("t3_invoices") == [1, 2, 3] and lib.load("t3_invoices")["version"] == 3
    assert lib.load("t3_invoices", 1) == progs["t3_invoices"]


def _fix(app: str, *steps) -> Trace:
    return Trace(task="t3_invoices", instance="fix", steps=[Step(i=k + 1, app=app, **s) for k, s in enumerate(steps)])


def test_teach_refuses_unsafe_or_unexplained_fixes(demos, tmp_path):
    _, _, progs = demos
    p3 = progs["t3_invoices"]
    halt = {"status": "HALTED", "reason": "unknown_dialog", "op_id": p3["loop"]["body"][2]["id"],
            "observed": {"app": "accounting", "controls": [{"role": "button", "name": "Delete all bills"},
                                                           {"role": "button", "name": "OK"}]},
            "new_text": ["Something happened"], "item": None}
    bad = teach(p3, halt, _fix("accounting", {"action": "click", "target": {"role": "AXButton", "name": "Delete all bills"}}))
    assert bad["status"] == "refused" and "destructive" in bad["reason"]
    quiet = dict(halt, new_text=[])
    r = teach(p3, quiet, _fix("accounting", {"action": "click", "target": {"role": "AXButton", "name": "OK"}}))
    assert r["status"] == "refused" and "new" in r["reason"]
    unmatched = {"status": "HALTED", "reason": "unmatched_item", "op_id": p3["loop"]["body"][0]["id"],
                 "observed": {"app": "inbox", "text": "Your X invoice AB-12 (2026-03-01) is ready.\nAmount: $5.00"},
                 "item": {"ref": "x", "kind": "/message/#"}}
    typed = [{"action": "type", "target": {"role": "AXTextField", "name": n}, "value": v}
             for n, v in (("Vendor", "Somebody Else"), ("Invoice number", "AB-12"), ("Invoice date", "2026-03-01"),
                          ("Amount", "5.00"))]
    r = teach(p3, unmatched, _fix("accounting", *typed))
    assert r["status"] == "refused" and "comes from" in r["reason"]           # vendor not on the item
    ok_fix = _fix("accounting", {"action": "click", "target": {"role": "AXButton", "name": "OK"}})
    never = teach(p3, halt, ok_fix, gate=lambda cand, h: {"ok": False, "why": "demo regressed"})
    assert never["status"] == "refused" and never["program"] is None and never["candidate"]["version"] == 2
    cand = teach(p3, halt, ok_fix)                                              # no gate: never promoted
    assert cand["status"] == "candidate"


@pytest.mark.timeout(120)
def test_full_wp5_evaluation(tmp_path):
    """Acceptance (run.py plumbing): 3 tasks x held-out; v1 >= current with 0 model calls; after one
    scripted fix demo per halt kind every held-out instance passes; injected dialogs HALT then pass."""
    out = evaluate(tmp_path, quiet=True)
    v1 = {t: int(r.split("/")[0]) for t, r in out["v1"]["held_out"].items()}
    assert sum(v1.values()) >= 19 and out["v1"]["model_calls"] == 0
    assert out["vN"]["held_out"] == {t: "8/8" for t in TASKS} and out["vN"]["model_calls"] == 0
    assert all(r["teach"]["status"] == "promoted" for r in out["teach_rounds"])
    assert len(out["teach_rounds"]) <= 3
    for r in out["injected_dialog"]:
        assert r["first"]["status"] == "HALTED" and r["resume"]["success"], r
    assert (tmp_path / "wp5_report.json").exists() and (tmp_path / "skills" / "t3_invoices" / "active.json").exists()


def test_compiler_integration(demos):
    _, traces, _ = demos
    sk = compile_trace(traces["t3_invoices"])
    assert all("step" in g for g in sk["procedure"]["for_each_item"])
    assert "'step'" not in render_skill(sk)
    assert sk["items"]["steps"] and sk["items"]["skipped_steps"]
    r = induce_skill([traces["t3_invoices"]])
    assert not r.refused and r.program["schema"] == "awos.skill_program/1"
