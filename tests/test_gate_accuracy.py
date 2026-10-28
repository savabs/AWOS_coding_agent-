"""Hermetic tests for scripts/gate_accuracy.py. No network, no LLM."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("gate_accuracy",
                                                  REPO / "scripts" / "gate_accuracy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gate_accuracy"] = mod  # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


ga = _load()

LOG_CLEAN = """
===== [off j01] 2026-10-09 =====
+00:07 [ACCEPTANCE] generated 5 tests, kept 3 (failed on start), $0.0006; dropped: 2 passed on start
+00:18 [ACCEPTANCE] check: 3 kept, 3 passed
+00:18 [ONE-SHOT] solved in 1 call - 1 file(s) changed, tests 70 passed
"""

LOG_ARBITER = """
+00:44 [ACCEPTANCE] generated 6 tests, kept 4 (failed on start), $0.0007
+04:37 [agent] stop: completed after 37 turns, 41 tool calls (3 failed), nudges 0
+04:43 [ACCEPTANCE] check: 4 kept, 2 passed
+04:45 [ACCEPTANCE] arbitration: 2 failing -> 2 wrong_test, 0 code_incomplete, $0.0002
+04:45 [ACCEPTANCE] no code_incomplete test left; the visible-green result stands
"""

LOG_REPAIR_FAILED = """
+00:06 [ACCEPTANCE] generated 4 tests, kept 3 (failed on start), $0.0003
+01:00 [ACCEPTANCE] check: 3 kept, 2 passed
+01:01 [ACCEPTANCE] arbitration: 1 failing -> 0 wrong_test, 1 code_incomplete, $0.0001
+01:01 [TASK 1] [ACCEPTANCE] bounded repair: 1 code_incomplete test(s), at most 6 turns
+02:00 [agent] stop: max_turns after 6 turns, 6 tool calls (0 failed), nudges 0
+02:01 [ACCEPTANCE] check: 3 kept, 2 passed
+02:01 [ACCEPTANCE] repair failed; restored the green edit (max_turns, 6 turn(s); 1 path(s) restored)
"""

LOG_REPAIR_PASSED = LOG_REPAIR_FAILED.replace(
    "repair failed; restored the green edit", "repair passed (completed, 2 turn(s))")

LOG_INACTIVE = """
+00:04 [ACCEPTANCE] gate inactive: no OpenAI-shaped client configured
+02:28 [agent] t17 stopped after green: 7 turns without a change
+02:28 [agent] stop: completed after 17 turns, 24 tool calls (0 failed), nudges 0
"""

LOG_NONE_KEPT = ("+00:09 [ACCEPTANCE] generated 0 tests, kept 0 (failed on start), $0.0002; "
                 "dropped: 1 collection error; gate inactive\n")


def _result(**kw):
    base = {"arm": "off", "job": 1, "id": "real_x_1", "hidden_passed": 10, "hidden_failed": 0,
            "visible_tests_ok": True, "orchestrator_success": True, "invalid": False,
            "models": ["m"]}
    base.update(kw)
    return base


def test_wilson_known_values():
    lo, hi = ga.wilson(13, 25)
    assert lo == pytest.approx(0.335, abs=1e-3)
    assert hi == pytest.approx(0.700, abs=1e-3)
    assert ga.wilson(0, 0) == (0.0, 1.0)
    lo, hi = ga.wilson(0, 10)
    assert lo == 0.0 and 0.27 < hi < 0.28


def test_parse_clean_pass():
    f = ga.parse_log(LOG_CLEAN)
    assert f.acceptance == "active" and f.checks == [(3, 3)]
    assert f.last_check_ok and f.acceptance_final_ok and f.strict_ok
    assert f.one_shot_solved and not f.unverified_reasons


def test_parse_arbiter_override_passes_own_but_not_strict():
    f = ga.parse_log(LOG_ARBITER)
    assert f.resolution == "arbiter_overrode"
    assert f.arbitration == (2, 2, 0)
    assert f.acceptance_final_ok is True
    assert f.strict_ok is False
    assert f.unverified_reasons == ["arbiter_overrode"]


def test_parse_repair_failed_and_passed():
    failed = ga.parse_log(LOG_REPAIR_FAILED)
    assert failed.resolution == "repair_failed_restored"
    assert failed.acceptance_final_ok is False and failed.strict_ok is False
    assert failed.stops == ["max_turns"]
    passed = ga.parse_log(LOG_REPAIR_PASSED)
    assert passed.resolution == "repair_passed"
    assert passed.acceptance_final_ok and passed.strict_ok
    assert not passed.unverified_reasons


def test_parse_inactive_and_none_kept():
    f = ga.parse_log(LOG_INACTIVE)
    assert f.acceptance == "inactive" and f.post_green_stop
    assert f.acceptance_final_ok  # an inactive gate passes, as the code treats it
    assert set(f.unverified_reasons) == {"acceptance_inactive", "post_green_stop"}
    n = ga.parse_log(LOG_NONE_KEPT)
    assert n.acceptance == "none_kept"


def test_parse_missing_log():
    f = ga.parse_log(None)
    assert not f.present and f.unverified_reasons[0] == "no_log"


def test_job_section_picks_last_matching_job():
    text = ("===== [off j01] a =====\nold [ACCEPTANCE] check: 2 kept, 0 passed\n"
            "===== [off j02] b =====\nother\n"
            "===== [off j01] c =====\nnew [ACCEPTANCE] check: 2 kept, 2 passed\n")
    sec = ga.job_section(text, 1)
    assert "new" in sec and "old" not in sec and "other" not in sec
    assert ga.job_section("no headers", 1) == "no headers"


def test_build_row_gates_and_modes():
    doc = {"model_pin": {"model": "flash"}}
    fa = ga.build_row("f", doc, _result(hidden_failed=2), LOG_ARBITER)
    assert fa.model == "flash" and not fa.hidden_ok
    assert fa.gates == {"declared": True, "visible": True, "own_tests": True,
                        "strict_acceptance": False, "verified_tier": False}
    assert ga.fa_mode(fa) == "arbiter_overrode"
    fr = ga.build_row("f", doc, _result(orchestrator_success=False, visible_tests_ok=False),
                      LOG_CLEAN)
    assert fr.hidden_ok and ga.fr_mode(fr) == "visible_red"


def test_task_id_normalises_single_issue_series():
    assert ga.task_id({"series": "real_md_1"}, {"id": "01_smarty"}) == "real_md_1"
    assert ga.task_id({"series": "E1"}, {"id": "real_md_1"}) == "real_md_1"


def test_score_counts():
    doc = {}
    rows = [
        ga.build_row("f", doc, _result(), LOG_CLEAN),                       # TA
        ga.build_row("f", doc, _result(hidden_failed=1), LOG_ARBITER),      # FA
        ga.build_row("f", doc, _result(hidden_failed=1, orchestrator_success=False),
                     LOG_CLEAN),                                            # TR
        ga.build_row("f", doc, _result(orchestrator_success=False), LOG_CLEAN),  # FR
    ]
    s = ga.score(rows, "declared")
    assert s["fa_rate"]["k"] == 1 and s["fa_rate"]["n"] == 2
    assert s["fdr"]["k"] == 1 and s["fdr"]["n"] == 2
    assert s["fr_rate"]["k"] == 1 and s["fr_rate"]["n"] == 2
    assert s["fa_modes"] == {"arbiter_overrode": 1}
    audit = ga.arbiter_audit(rows)
    assert audit["overrides"]["hidden_fail"] == 1
    sig = ga.signal_audit(rows)
    assert sig["arbiter_overrode"] == {"n": 1, "hidden_pass": 0, "hidden_fail": 1,
                                       "fail_tasks": ["real_x_1"]}


def test_load_rows_reads_run_log_and_skips_invalid(tmp_path):
    arm = tmp_path / "run" / "off"
    (arm / "project").mkdir(parents=True)
    (arm / "run.log").write_text(LOG_ARBITER, encoding="utf-8")
    good = _result(hidden_failed=1, inputs={"cmd": ["py", "x", str(arm / "project")]})
    bad = _result(invalid=True)
    empty = _result(hidden_passed=0, hidden_failed=0)
    f = tmp_path / "job_series_x.json"
    f.write_text(json.dumps({"model_pin": {"model": "m"}, "results": [good, bad, empty]}))
    dry = tmp_path / "job_series_dry.json"
    dry.write_text(json.dumps({"dry_run": True, "results": [good]}))
    rows = ga.load_rows([f, dry])
    assert len(rows) == 1
    assert rows[0].facts.resolution == "arbiter_overrode"


def test_main_writes_json(tmp_path, capsys):
    f = tmp_path / "job_series_x.json"
    f.write_text(json.dumps({"results": [_result()]}))
    out = tmp_path / "r.json"
    assert ga.main([str(f), "--json", str(out), "--no-logs"]) == 0
    data = json.loads(out.read_text())
    assert data["rows"] == 1 and "overall" in data["report"]
    assert "| overall | declared |" in capsys.readouterr().out
