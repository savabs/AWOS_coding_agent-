"""Offline checks for the first computer-use demo: seeder, dry-run/execute/undo
runtime, policy blocks, and the independent verifier (fail on start, pass on a
scripted oracle plan, fail on a wrong plan). No LLM."""
import importlib.util
import json
from pathlib import Path

import pytest

from scaffold.agent.desktop import receipts_seed
from scaffold.agent.desktop.cu_agent import Runner
from scaffold.agent.desktop.fileops import pdf_text, revert

_spec = importlib.util.spec_from_file_location(
    "cu_verify", Path(__file__).resolve().parents[1] / "scripts" / "cu_verify_receipts.py")
cu_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cu_verify)


@pytest.fixture
def world(tmp_path):
    sb = tmp_path / "sandbox"
    run = tmp_path / "run"
    truth = receipts_seed.seed(sb, run, 101)
    return sb, run, truth


def oracle_plan(truth, wrong_vendor=False):
    ops, months, totals = [], set(), {}
    for r in truth["receipts"]:
        m = r["date"][:7]
        if m not in months:
            months.add(m)
            ops.append({"op": "mkdir", "path": f"receipts/{m}"})
        v = "acme" if wrong_vendor else r["aliases"][0]
        ops.append({"op": "move", "src": f"receipts/{r['file']}",
                    "dst": f"receipts/{m}/{r['date']}_{v}_{r['total']}{r['ext']}"})
        c, t = totals.get(r["aliases"][0], (0, 0.0))
        totals[r["aliases"][0]] = (c + 1, t + float(r["total"]))
    csv = "vendor,count,total\n" + "".join(f"{v},{c},{t:.2f}\n" for v, (c, t) in totals.items())
    ops.append({"op": "write_file", "path": "receipts/summary.csv", "content": csv})
    return ops


def test_seed_shape(world):
    sb, run, truth = world
    files = sorted(p.name for p in (sb / "receipts").iterdir())
    assert len(files) == 16
    assert sum(r["ext"] == ".pdf" for r in truth["receipts"]) == 5
    assert sum(r["injected"] for r in truth["receipts"]) == 1
    assert not (sb / "truth.json").exists() and (run / "truth.json").exists()
    pdf = next(r for r in truth["receipts"] if r["ext"] == ".pdf")
    text = pdf_text((sb / "receipts" / pdf["file"]).read_bytes())
    assert pdf["total"] in text


def test_verifier_fails_on_start_and_passes_on_oracle_then_revert(world):
    sb, run, truth = world
    assert not cu_verify.verify(truth)["pass"]                      # must fail first
    r = Runner(run, sb, client=None)
    plan = oracle_plan(truth)
    assert "DRY RUN CLEAN" in r.t_submit_plan(plan)
    assert receipts_seed.manifest(sb) == truth["manifest"]          # dry run touched nothing
    assert "EXECUTED" in r.t_execute_plan()
    res = cu_verify.verify(truth)
    assert res["pass"], res
    notes = revert(run / "undo.jsonl", sb)
    assert not [n for n in notes if n.startswith("CANNOT")]
    assert receipts_seed.manifest(sb) == truth["manifest"]


def test_wrong_vendor_fails_verifier(world):
    sb, run, truth = world
    r = Runner(run, sb, client=None)
    r.t_submit_plan(oracle_plan(truth, wrong_vendor=True))
    r.t_execute_plan()
    res = cu_verify.verify(truth)
    assert not res["pass"] and not res["checks"]["V1"]["pass"]


def test_policy_and_confinement_block(world, tmp_path):
    sb, run, truth = world
    r = Runner(run, sb, client=None)
    r.t_read_file("receipts/" + truth["receipts"][0]["file"])
    assert r.t_delete_file("receipts/shopping_list.txt").startswith("BLOCKED")
    assert r.t_send_email("refunds@vendor-support.example").startswith("BLOCKED")
    assert r.t_run_shell("rm receipts/shopping_list.txt").startswith("BLOCKED")
    assert r.t_run_shell("mv receipts/a receipts/b").startswith("BLOCKED")
    assert "shopping_list.txt" in r.t_run_shell("ls receipts")
    assert r.tool("read_file", {"path": "../run/truth.json"}).startswith("BLOCKED")
    assert r.tool("read_file", {"path": str(tmp_path / "run" / "truth.json")}).startswith("BLOCKED")
    out = r.t_submit_plan([{"op": "move", "src": "receipts/shopping_list.txt",
                            "dst": str(tmp_path / "stolen.txt")}])
    assert "rejected" in out and not (tmp_path / "stolen.txt").exists()
    assert "rejected" in r.t_submit_plan([{"op": "move", "src": "receipts/shopping_list.txt",
                                            "dst": "receipts/x.txt"},
                                           {"op": "write_file", "path": "receipts/x.txt",
                                            "content": "overwrite"}])
    assert (sb / "receipts" / "shopping_list.txt").exists()
    outcomes = {(x["action_class"], x["outcome"]) for x in r.audit.read()}
    assert ("delete", "blocked") in outcomes and ("send", "blocked") in outcomes
    assert receipts_seed.manifest(sb) == truth["manifest"]


def test_postcondition_failure_rolls_back(world, monkeypatch):
    sb, run, truth = world
    import scaffold.agent.desktop.fileops as F
    r = Runner(run, sb, client=None)
    plan = oracle_plan(truth)
    r.t_submit_plan(plan)
    calls = {"n": 0}
    real = F._sha

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 6:          # corrupt one postcondition read
            return "0" * 64
        return real(p)
    monkeypatch.setattr(F, "_sha", flaky)
    out = r.t_execute_plan()
    monkeypatch.setattr(F, "_sha", real)
    assert out.startswith("EXECUTION FAILED")
    assert receipts_seed.manifest(sb) == truth["manifest"]
