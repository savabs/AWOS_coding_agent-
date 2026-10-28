"""job_series fairness fixes for the AWOS-vs-Aider rerun (hermetic, no LLM):
empty Aider responses score INVALID, harness crashes are caught without a
traceback, AWOS component state lands on every row, provenance paths are
exact, and a series budget is a hard stop."""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("job_series_fair", REPO / "scripts" / "job_series.py")
js = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(js)
_rspec = importlib.util.spec_from_file_location("jsr_helpers", REPO / "tests" / "test_job_series_runner.py")
_r = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(_r)
make_series = _r.make_series

_E2E = pytest.mark.timeout(120)

BANNER = ("+00:00 [harness_aider] aider 0.86.2 model=anthropic/claude-haiku-5-5 provider=anthropic "
          "key=set price=known max_reflections=10 add_files=auto:src(9 edit, 0 read-only, "
          "~24967 tokens) cost_cap=$1.0 timeout=1770s project=/p\n")
B2_EMPTY = (BANNER + "+00:03 Warning: it's best to only add files that need changes to the chat.\n"
            "+01:11 Empty response received from LLM. Check your provider account?\n"
            "+01:11 Model anthropic/claude-haiku-5-5 has hit a token limit!\n"
            '+01:12 [aider-final] {"edited": [], "in_chat": ["a.py"]}\n'
            "+01:12 [harness_aider] done exit=0 killed=None turns=0 cost=$0.0000 (unknown)\n")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(js, "key_usage", lambda state: None)
    monkeypatch.setattr(js, "backend_ok", lambda: True)
    monkeypatch.delenv("AWOS_SERIES_BUDGET_USD", raising=False)


# ── empty responses ───────────────────────────────────────────────────────────


def test_b2_empty_response_log_is_invalid_not_unsolved():
    reason = js.detect_invalid(B2_EMPTY, turns=0, dry_run=False)
    assert reason and "empty_model_response" in reason
    # The banner is not an ask: the re-derived label is the empty response.
    assert js.aider_no_edit(B2_EMPTY) == "empty_model_response"


def test_harness_invalid_line_wins_and_a_good_retry_is_valid():
    new = BANNER + "+02:30 [harness_aider] invalid_run reason=empty_model_response attempts=3\n"
    assert "empty_model_response" in js.detect_invalid(new, turns=0, dry_run=False)
    retried = (BANNER + "+00:00 [harness_aider] attempt 1/3\n"
               + "+01:11 Empty response received from LLM. Check your provider account?\n"
               + "+01:30 [harness_aider] attempt 2/3\n+02:00 Applied edit to a.py\n"
               + '+02:00 [aider-final] {"edited": ["a.py"], "in_chat": ["a.py"]}\n'
               + "+02:01 [harness_aider] done exit=0\n")
    assert js.detect_invalid(retried, turns=1, dry_run=False) is None
    assert js.aider_no_edit(retried) is None


# ── crashes ───────────────────────────────────────────────────────────────────


def test_crash_detected_without_a_traceback():
    killed = BANNER + "+00:05 Added a.py to the chat.\n"      # SIGKILL: nothing printed
    assert js.aider_harness_crashed(killed)
    assert "harness_crashed" in js.detect_invalid(killed, turns=0, dry_run=False)
    assert js.aider_no_edit(killed) == "harness_crashed"
    # Killed on purpose at the job limit: not a crash.
    assert not js.aider_harness_crashed(killed + "+30:00 [job_series] hit the 30-minute limit\n")
    assert not js.aider_harness_crashed(B2_EMPTY)


HARNESS = """
import json, os, pathlib, sys
print("[harness_aider] aider 0.0.fake model=m provider=anthropic key=set add_files=auto:src(1 edit, 0 read-only, ~1 tokens)", flush=True)
mode = os.environ.get("FAKE_HARNESS", "ok")
if mode == "die":
    os._exit(9)             # no traceback, no report
if mode == "noreport":
    print("[harness_aider] done exit=0", flush=True)
    sys.exit(0)             # exit 0 but no report.json
pathlib.Path("report.json").write_text(json.dumps({"harness": "aider", "success": True,
    "usage": {"cost_usd": 0.01, "turns": 2, "input_tokens": 9, "output_tokens": 1, "models": ["m"]},
    "retry_cost_usd": 0.004, "attempts": 2, "empty_responses": 0}))
print("[harness_aider] done exit=0", flush=True)
"""

AWOS = """
import json, os, pathlib
mode = os.environ.get("FAKE_AWOS", "report")
comp = {"planner": {"ran": True}, "acceptance": "inactive", "one_shot": False}
rep = {"success": True, "usage": {"cost_usd": float(os.environ.get("FAKE_COST", "0.02")),
       "turns": 4, "input_tokens": 9, "output_tokens": 1, "models": ["m"]}}
if mode == "report":
    rep["components"] = comp
elif mode == "line":
    print("[COMPONENTS] " + json.dumps({**comp, "acceptance": "ran"}), flush=True)
pathlib.Path("report.json").write_text(json.dumps(rep))
"""


def _run(tmp_path, monkeypatch, arms, *extra):
    d = tmp_path / "fakes"
    d.mkdir(exist_ok=True)
    (d / "harness_aider.py").write_text(textwrap.dedent(HARNESS))
    (d / "awos_child.py").write_text(textwrap.dedent(AWOS))
    monkeypatch.setitem(js.ARM_CHILDREN, "fakeaider", {
        "cmd": [sys.executable, str(d / "harness_aider.py"), "{job_dir}", "{project}"], "env": {}})
    monkeypatch.setitem(js.ARM_CHILDREN, "fakeawos", {
        "cmd": [sys.executable, str(d / "awos_child.py"), "{job_dir}", "{project}"], "env": {}})
    make_series(tmp_path, "mini")
    out = tmp_path / "out"
    rc = js.main(["run", "--series", "mini", "--series-root", str(tmp_path), "--out-root",
                  str(out), "--arms", arms, "--skip-preflight", *extra])
    [f] = out.glob("job_series_*.json")
    return rc, json.loads(f.read_text())


@_E2E
@pytest.mark.parametrize("mode,why", [("die", "exit 9, no report.json"),
                                      ("noreport", "exit 0, no report.json")])
def test_harness_crash_by_exit_code_or_missing_report(tmp_path, monkeypatch, mode, why):
    monkeypatch.setenv("FAKE_HARNESS", mode)
    monkeypatch.setenv("AWOS_SERIES_RETRIES", "0")
    _, data = _run(tmp_path, monkeypatch, "fakeaider", "--jobs", "1")
    [r] = data["results"]
    assert r["invalid"] and r["invalid_reason"].startswith("harness_crashed")
    assert why in r["invalid_reason"]


@_E2E
def test_components_copied_per_row_and_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AWOS", "report")
    _, data = _run(tmp_path, monkeypatch, "fakeawos,fakeaider")
    rows = {(r["arm"], r["job"]): r for r in data["results"]}
    assert rows[("fakeawos", 1)]["components"]["planner"] == {"ran": True}
    assert rows[("fakeaider", 1)]["components"] == {"harness": "aider"}
    # Aider's internal retry spend is on the row.
    assert rows[("fakeaider", 1)]["spent_usd"] == pytest.approx(0.014)
    assert rows[("fakeaider", 1)]["aider_attempts"] == 2
    c = data["summary"]["fakeawos"]["components"]
    assert c == {"rows": 2, "with_components": 2, "planner": 2, "acceptance": 0, "one_shot": 0}
    assert data["summary"]["fakeaider"]["components"]["with_components"] == 0


@_E2E
def test_components_from_log_line_or_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AWOS", "line")
    _, data = _run(tmp_path, monkeypatch, "fakeawos", "--jobs", "1")
    assert data["results"][0]["components"]["acceptance"] == "ran"
    assert data["summary"]["fakeawos"]["components"]["acceptance"] == 1
    monkeypatch.setenv("FAKE_AWOS", "none")
    (tmp_path / "b").mkdir()
    _, data = _run(tmp_path / "b", monkeypatch, "fakeawos", "--jobs", "1")
    assert data["results"][0]["components"] == {}


def test_component_ran_shapes():
    yes = [True, 1, "ran", "OK", {"ran": True}, {"status": "used"}, {"calls": 2}]
    no = [False, 0, None, "inactive", "skipped", "failed", {"ran": False},
          {"status": "fell_back"}, {"calls": 0}, {}]
    assert all(js.component_ran(v) for v in yes)
    assert not any(js.component_ran(v) for v in no)


def test_run_child_copies_components_into_report():
    src = (REPO / "scripts" / "job_series.py").read_text()
    assert '"errors", "components")' in src


# ── provenance ────────────────────────────────────────────────────────────────


def test_porcelain_paths_keep_the_first_character():
    z = " M scripts/harness_aider.py\0M  scripts/job_series.py\0R  new.py\0old.py\0?? x\0"
    assert js.porcelain_paths(z) == ["scripts/harness_aider.py", "scripts/job_series.py",
                                     "new.py", "x"]


# ── series budget ─────────────────────────────────────────────────────────────


@_E2E
def test_series_budget_is_a_hard_stop(tmp_path, monkeypatch):
    # $0.05 budget, $0.03 per job: job 1 runs with its cap lowered to $0.05 and
    # spends $0.03 (< 90% of it: valid); job 2 gets the $0.02 left, spends $0.03
    # (>= 90%): INVALID budget_cap, and the run stops.
    monkeypatch.setenv("AWOS_SERIES_BUDGET_USD", "0.05")
    monkeypatch.setenv("FAKE_COST", "0.03")
    _, data = _run(tmp_path, monkeypatch, "fakeawos")
    r1, r2 = data["results"]
    assert not r1["invalid"]
    assert r2["invalid"] and r2["invalid_reason"].startswith("budget_cap")
    assert "budget" in data["stopped"]
    assert data["budget"] == {"limit_usd": 0.05, "spent_usd": pytest.approx(0.06)}


# ── interleaving with repeats ─────────────────────────────────────────────────


@_E2E
def test_two_arms_interleaved_per_issue_with_repeat_2(tmp_path):
    make_series(tmp_path, "mini")
    out = tmp_path / "out"
    assert js.main(["run", "--series", "mini", "--series-root", str(tmp_path), "--dry-run",
                    "--out-root", str(out), "--arms", "off,aider", "--repeat", "2"]) == 0
    [f] = out.glob("job_series_*.json")
    rows = json.loads(f.read_text())["results"]
    assert [(r["repeat"], r["job"], r["arm"]) for r in rows] == [
        (1, 1, "off"), (1, 1, "aider"), (1, 2, "off"), (1, 2, "aider"),
        (2, 1, "off"), (2, 1, "aider"), (2, 2, "off"), (2, 2, "aider")]
    assert all(r["components"] == {"harness": "aider"} for r in rows if r["arm"] == "aider")
    assert all(r["components"] == {} for r in rows if r["arm"] == "off")
