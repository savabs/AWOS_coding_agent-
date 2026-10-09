"""Record format, params schema + taint, fingerprint, store, Beta math (step 2)."""

import json

import pytest

from scaffold.agent.compiled import beta
from scaffold.agent.compiled import record as R
from scaffold.agent.compiled.admit import build_record
from scaffold.agent.compiled.examples import bump_version as tool


@pytest.fixture
def golden(tmp_path):
    return tool.make_demo_repo(tmp_path / "golden")


def test_record_roundtrip_and_schema(golden):
    rec = build_record(tool, golden)
    d = rec.to_dict()
    assert R.validate_record(d) == []
    assert d["id"].startswith("rt_") and len(d["id"]) == 15
    back = R.Record.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    assert rec.taint() == {"changelog_note": "data"}


def test_record_schema_rejects_bad(golden):
    d = build_record(tool, golden).to_dict()
    bad = dict(d, state="live")
    assert any("enum" in e for e in R.validate_record(bad))
    open_schema = json.loads(json.dumps(d))
    open_schema["params_schema"]["additionalProperties"] = True
    assert R.validate_record(open_schema)
    routine = json.loads(json.dumps(d))
    routine["kind"] = "routine"
    assert any("routine" in e for e in R.validate_record(routine))


def test_validate_params():
    s = tool.PARAMS_SCHEMA
    assert R.validate_params(s, {"new_version": "1.2.3"}) == []
    assert R.validate_params(s, {"new_version": "1.2"})
    assert R.validate_params(s, {"new_version": "1.2.3", "x": 1})
    assert R.validate_params(s, {"changelog_note": "n"})
    assert R.validate_params(s, {"new_version": "1.2.3", "changelog_note": "x" * 201})
    assert R.validate_params({"type": "object", "properties": {}}, {})  # must be closed


def test_fingerprint_detects_drift(golden):
    fp = R.compute_fingerprint(golden, tool.READ_SET)
    assert R.check_fingerprint(golden, fp) == []
    assert list(fp["lockfile"]) == ["requirements.txt"]
    (golden / "pyproject.toml").write_text("x")
    assert R.check_fingerprint(golden, fp) == ["pyproject.toml: changed"]
    (golden / "requirements.txt").write_text("y")
    assert "lockfile requirements.txt: changed" in R.check_fingerprint(golden, fp)


def test_store_one_live_record_per_family(tmp_path, golden):
    rec = build_record(tool, golden)
    p1 = R.save_record(tmp_path / "store", rec)
    rec2 = R.Record.from_dict(rec.to_dict())
    rec2.version = 2
    p2 = R.save_record(tmp_path / "store", rec2)
    idx = json.loads((tmp_path / "store" / "INDEX.json").read_text())
    assert idx == {"bump_version": p2.name} and p1.exists()
    assert R.load_record(p2).version == 2
    assert not list((tmp_path / "store").glob(".*.tmp"))


# ── Beta math ─────────────────────────────────────────────────────────────────

def test_lb_table_matches_spec():
    assert [beta.successes_needed(f) for f in (0, 1, 2, 3)] == [28, 44, 58, 72]
    assert round(beta.lower_bound(28, 0), 3) == 0.902
    for s, lb in ((3, 0.47), (5, 0.61), (10, 0.76), (12, 0.79), (20, 0.87)):
        assert round(beta.lower_bound(s, 0), 2) == lb


def test_bisection_agrees_with_closed_form():
    for s in (0, 5, 27):
        q = beta.lower_bound(s, 0)
        assert abs(beta.beta_cdf(q, 1 + s, 1) - 0.05) < 1e-9


def _ev(**kw):
    return {"id": "rt_x", "state": kw.pop("state", "admitted"),
            "evidence": dict({"s": 0, "f": 0, "s_live": 0, "f_live": 0, "s_indep": 0}, **kw)}


def test_promotion_needs_all_three():
    d = _ev(s=27, s_live=3, s_indep=5)
    beta.apply_event(d, "ok")
    assert d["state"] == "promoted"
    d = _ev(s=40, s_live=2, s_indep=10)
    beta.apply_event(d, "ok")
    assert d["state"] == "admitted"
    assert beta.promotion_check(d["evidence"])["blocked_by"] == ["s_live 2 < 3"]


def test_one_fail_demotes_second_retires():
    d = _ev(state="promoted", s=40, s_live=3, s_indep=5)
    beta.apply_event(d, "fail", ts=1000.0)
    assert d["state"] == "demoted" and d["evidence"]["f"] == 1
    beta.apply_event(d, "fail", ts=1000.0 + 86400)
    assert d["state"] == "retired"


def test_demoted_reusable_after_ten_passes():
    d = _ev(state="promoted", s=20, s_live=3, s_indep=5)
    beta.apply_event(d, "fail", ts=0.0)
    for _ in range(9):
        beta.apply_event(d, "ok", ts=1.0)
    assert d["state"] == "demoted"
    beta.apply_event(d, "ok", ts=1.0)
    assert d["state"] == "admitted"        # s=30, f=1: below the 44 needed to re-promote
    assert d["evidence"]["s"] == 30


def test_mismatch_suspends_without_failure():
    d = _ev(state="promoted", s=40)
    beta.apply_event(d, "mismatch")
    assert d["state"] == "suspended" and d["evidence"]["f"] == 0


def test_idle_demotes_promoted():
    d = _ev(state="promoted", s=40, last_used_ts=0.0)
    beta.apply_event(d, "idle", ts=59 * 86400)
    assert d["state"] == "promoted"
    beta.apply_event(d, "idle", ts=61 * 86400)
    assert d["state"] == "admitted"


def test_fold_executions_counts():
    log = [{"record_id": "a", "source": "gen", "probe_result": {"ok": True}},
           {"record_id": "a", "source": "indep", "probe_result": {"ok": True}},
           {"record_id": "a", "source": "live", "probe_result": {"ok": False}},
           {"record_id": "a", "source": "live", "outcome": "precondition_refused",
            "probe_result": {"ok": None}},
           {"record_id": "b", "source": "gen", "probe_result": {"ok": True}}]
    ev = beta.fold_executions(log, "a")
    assert (ev["s"], ev["f"], ev["s_indep"], ev["f_live"], ev["s_live"]) == (2, 1, 1, 1, 0)
