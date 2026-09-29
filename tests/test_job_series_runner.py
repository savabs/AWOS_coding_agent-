"""Hermetic tests for scripts/job_series.py (no network, no LLM).

A tiny synthetic 2-job series is built in tmp_path; `run --dry-run` exercises
staging, interleaving, streaming, judging and the results file with a no-op
child in place of the orchestrator.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("job_series", REPO / "scripts" / "job_series.py")
js = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(js)


@pytest.fixture(autouse=True)
def _no_billing_lookup(monkeypatch):
    # key_usage asks OpenRouter for the key's spend; tests stay off the network.
    monkeypatch.setattr(js, "key_usage", lambda state: None)


def test_billed_since_waits_for_a_settled_figure(monkeypatch):
    # The figure lags: a late charge (1.05 -> 1.08) must not be missed.
    readings = iter([1.00, 1.05, 1.05, 1.08, 1.08, 1.08])
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(js, "key_usage", lambda state: next(readings))
    monkeypatch.setattr(js.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(js.time, "sleep", lambda s: None)
    assert js.billed_since(Path("."), 0.98) == pytest.approx(0.10)
    assert js.billed_since(Path("."), None) is None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


CALC_V0 = "def add(a, b):\n    return a + b\n"
CALC_V1 = CALC_V0 + "\ndef sub(a, b):\n    return a - b\n"
CALC_V2 = CALC_V1 + "\ndef mul(a, b):\n    return a * b\n"


def make_series(root: Path, name: str = "mini", broken: bool = False) -> Path:
    s = root / name
    _write(s / "base" / "calc" / "__init__.py", CALC_V0)
    _write(s / "base" / "tests" / "test_base.py", """
        from calc import add
        def test_add():
            assert add(1, 2) == 3
    """)
    _write(s / "base" / "conftest.py", "")  # puts the project root on sys.path
    jobs = [("01_sub", "sub", CALC_V1, "assert sub(5, 3) == 2"),
            ("02_mul", "mul", CALC_V2, "assert mul(2, 3) == 6")]
    for slug, fn, ref, check in jobs:
        j = s / "jobs" / slug
        _write(j / "task.json", json.dumps({
            "id": slug, "goal": f"Add {fn}", "max_turns": 5, "max_cost_usd": 0.1, "timeout_min": 1}))
        _write(j / "reference" / "calc" / "__init__.py", CALC_V0 if broken else ref)
        _write(j / "hidden_tests" / f"test_{fn}.py", f"""
            from calc import {fn}
            def test_{fn}():
                {check}
        """)
    return s


def test_parse_jobs():
    assert js.parse_jobs("1-3,7", list(range(1, 13))) == [1, 2, 3, 7]
    assert js.parse_jobs(None, [1, 2]) == [1, 2]
    assert js.parse_jobs("5-20", [1, 2, 5, 6]) == [5, 6]


def test_materialize_is_cumulative_and_keeps_git(tmp_path):
    s = make_series(tmp_path)
    jobs = js.discover_jobs(s)
    assert [n for n, _ in jobs] == [1, 2]
    dest = tmp_path / "proj"
    (dest / ".git").mkdir(parents=True)
    (dest / "stray.txt").parent.mkdir(exist_ok=True)
    (dest / "stray.txt").write_text("left by the agent")
    js.materialize(s, jobs, 2, dest)
    assert (dest / ".git").is_dir()
    assert not (dest / "stray.txt").exists()
    assert "def sub" in (dest / "calc" / "__init__.py").read_text()
    assert "def mul" not in (dest / "calc" / "__init__.py").read_text()


def test_judge_records_failing_test_names(tmp_path):
    s = make_series(tmp_path)
    jobs = js.discover_jobs(s)
    start = js.materialize(s, jobs, 2, tmp_path / "start")
    hidden, visible = js.judge(jobs[1][1], start)
    assert visible["ok"] and not hidden["ok"]
    assert any("test_mul" in t for t in hidden["failing"])

    solved = js.materialize(s, jobs, 2, tmp_path / "solved", with_own_reference=True)
    hidden, visible = js.judge(jobs[1][1], solved)
    assert hidden["ok"] and visible["ok"] and hidden["passed"] == 1 and hidden["failing"] == []


def test_validate_ok_and_invalid(tmp_path, capsys):
    make_series(tmp_path, "good")
    assert js.main(["validate", "--series", "good", "--series-root", str(tmp_path)]) == 0
    make_series(tmp_path, "bad", broken=True)
    assert js.main(["validate", "--series", "bad", "--series-root", str(tmp_path)]) == 1
    assert "hidden tests fail with the reference" in capsys.readouterr().out


def test_ledger_metrics_and_notebook_chars(tmp_path):
    entries = [
        {"request_type": "agent_loop", "model": "deepseek/deepseek-v4-flash",
         "input_tokens": 100, "output_tokens": 10, "cost": 0.01},
        {"request_type": "notebook", "model": "deepseek/deepseek-v4-flash",
         "input_tokens": 50, "output_tokens": 5, "cost": 0.002},
    ]
    (tmp_path / ".awos" / "projects" / "abc").mkdir(parents=True)
    (tmp_path / ".awos" / "budget.json").write_text(json.dumps(entries))
    (tmp_path / ".awos" / "projects" / "abc" / "notebook.md").write_text("x" * 42)
    m = js.ledger_metrics(js.read_ledger(tmp_path)[0:])
    assert m["llm_calls"] == 2 and m["input_tokens"] == 150 and m["output_tokens"] == 15
    assert m["cost_usd"] == pytest.approx(0.012) and m["notebook_cost_usd"] == pytest.approx(0.002)
    assert m["models"] == ["deepseek/deepseek-v4-flash"]
    assert js.notebook_chars(tmp_path) == 42
    assert js.read_ledger(tmp_path / "missing") == []


def test_summarize_splits_early_and_late_jobs():
    rows = [{"arm": a, "job": n, "solved": n > 6, "cost_usd": 1.0, "turns": 4, "minutes": 1.0}
            for a in ("off", "on") for n in range(1, 13)]
    s = js.summarize(rows, ["off", "on"])
    assert s["on"]["jobs_1_6"]["solve_rate"] == 0.0
    assert s["on"]["jobs_7_12"]["solve_rate"] == 1.0
    assert s["off"]["all"]["mean_turns"] == 4.0


def test_dry_run_end_to_end(tmp_path, capsys):
    make_series(tmp_path, "mini")
    out_root = tmp_path / "out"
    env_file = tmp_path / "fake.env"
    env_file.write_text("FAKE_KEY=not-a-real-key\n")
    rc = js.main(["run", "--series", "mini", "--series-root", str(tmp_path), "--dry-run",
                  "--out-root", str(out_root), "--env-file", str(env_file)])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "not-a-real-key" not in stdout            # key values are never printed

    [result_file] = out_root.glob("job_series_*.json")
    data = json.loads(result_file.read_text())
    order = [(r["arm"], r["job"]) for r in data["results"]]
    assert order == [("off", 1), ("on", 1), ("off", 2), ("on", 2)]   # interleaved
    for r in data["results"]:
        assert r["solved"] is False and r["visible_tests_ok"] is True
        assert r["failing_tests"] and r["llm_calls"] == 0 and r["cost_usd"] == 0
        assert r["notebook_chars"] == 0
    assert data["model_pin"]["model"] == "deepseek/deepseek-v4-flash"
    assert data["model_pin"]["env"]["AWOS_GOAL_CHECK"] == "0"
    assert data["summary"]["on"]["all"]["jobs"] == 2

    run_dir = Path(data["run_dir"])
    for arm, flag in (("off", "0"), ("on", "1")):
        state = run_dir / arm / "state"
        assert (state / ".env").read_text() == env_file.read_text()
        log = (run_dir / arm / "run.log").read_text()
        assert f"notebook={flag}" in log and "goal_check=0" in log
        assert f"cwd={state}" in log or f"cwd={state.resolve()}" in log
        assert f"tail -f {run_dir / arm / 'run.log'}" in stdout
        assert f"[{arm} j01] +00:00 [job_series] dry-run child" in stdout   # streamed with prefix
        project = run_dir / arm / "project"
        subjects = subprocess.run(["git", "-C", str(project), "log", "--format=%s"],
                                  capture_output=True, text=True).stdout.split("\n")
        assert subjects[:2] == ["job 2 start", "job 1 start"]
        job2_calc = subprocess.run(["git", "-C", str(project), "show", "HEAD:calc/__init__.py"],
                                   capture_output=True, text=True).stdout
        assert "def sub" in job2_calc and "def mul" not in job2_calc


def test_resume_keeps_earlier_jobs_and_state(tmp_path, capsys):
    # A key dying mid-run must not throw away the arms' notebooks: resume the
    # same run, rerun the selected jobs, keep the rest, pick up a fresh .env.
    make_series(tmp_path, "mini")
    out_root = tmp_path / "out"
    env_file = tmp_path / "fake.env"
    env_file.write_text("FAKE_KEY=old\n")
    base = ["run", "--series", "mini", "--series-root", str(tmp_path), "--dry-run",
            "--out-root", str(out_root), "--env-file", str(env_file)]
    assert js.main(base) == 0
    [result_file] = out_root.glob("job_series_*.json")
    ts = json.loads(result_file.read_text())["timestamp"]
    marker = out_root / "job_series" / ts / "on" / "state" / "kept.txt"
    marker.write_text("notebook stand-in")

    env_file.write_text("FAKE_KEY=new\n")
    assert js.main(base + ["--resume", ts, "--jobs", "2"]) == 0
    assert list(out_root.glob("job_series_*.json")) == [result_file]
    data = json.loads(result_file.read_text())
    assert [(r["arm"], r["job"]) for r in data["results"]] == [
        ("off", 1), ("on", 1), ("off", 2), ("on", 2)]
    assert data["resumed"] is True and data["jobs"] == [1, 2]
    assert marker.read_text() == "notebook stand-in"
    assert (out_root / "job_series" / ts / "off" / "state" / ".env").read_text() == "FAKE_KEY=new\n"
    assert js.main(base + ["--resume", "19990101T000000"]) == 1


def test_stream_child_times_out_and_logs(tmp_path, capsys):
    log = tmp_path / "x.log"
    timed_out = js.stream_child(
        [sys.executable, "-u", "-c", "import time; print('hello', flush=True); time.sleep(30)"],
        cwd=tmp_path, env=None, log_path=log, prefix="[t]", timeout_s=1.5)
    assert timed_out
    assert "hello" in log.read_text() and "minute limit" in log.read_text()
    assert "[t] +00:00 hello" in capsys.readouterr().out


def test_pin_model_leaves_only_flash(tmp_path):
    """In a fresh interpreter (the pin mutates module state): every route is Flash."""
    code = textwrap.dedent(f"""
        import sys, importlib.util
        sys.path[:0] = [{str(REPO)!r}, {str(REPO / 'scaffold')!r}, {str(REPO / 'scaffold' / 'agent')!r}]
        spec = importlib.util.spec_from_file_location("js", {str(REPO / 'scripts' / 'job_series.py')!r})
        js = importlib.util.module_from_spec(spec); spec.loader.exec_module(js)
        print("ALLOWED", js._pin_model())
        from scaffold.agent.escalation_engine import EscalationEngine
        d = EscalationEngine().decide({{"complexity": "high", "action": "refactor the system"}}, failure_count=3)
        print("CHOSEN", d.spec.model_id)
    """)
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "ALLOWED ['deepseek-v4-flash']" in proc.stdout
    assert "CHOSEN deepseek-v4-flash" in proc.stdout


# ── invalid jobs (infrastructure failures) ────────────────────────────────────


@pytest.mark.parametrize("line", [
    "openai.APIConnectionError: Connection error.",
    "litellm.AuthenticationError: invalid key",
    "Error: API key expired",
    "stop=model_error (Connection error.)",
    "Error code: 401 - {'error': 'unauthorized'}",
])
def test_detect_invalid_markers(line):
    assert js.detect_invalid(f"+00:03 {line}\n", turns=7, dry_run=False)


def test_detect_invalid_zero_turns_and_clean_runs():
    assert "0 agent turns" in js.detect_invalid("ran fine\n", turns=0, dry_run=False)
    assert js.detect_invalid("ran fine\n", turns=0, dry_run=True) is None
    assert js.detect_invalid("tests failed: AssertionError\n", turns=12, dry_run=False) is None
    assert set(js.INVALID_MARKERS) >= {"APIConnectionError", "AuthenticationError",
                                       "API key expired", "Connection error"}


def _dry_run(tmp_path, *extra):
    make_series(tmp_path, "mini")
    out_root = tmp_path / "out"
    rc = js.main(["run", "--series", "mini", "--series-root", str(tmp_path), "--dry-run",
                  "--out-root", str(out_root), *extra])
    [result_file] = out_root.glob("job_series_*.json")
    return rc, json.loads(result_file.read_text())


def test_invalid_job_restores_notebook_and_retries(tmp_path, monkeypatch, capsys):
    # on j2 dies with a connection error once, after scribbling on the notebook.
    monkeypatch.setenv("AWOS_SERIES_DRY_FAIL", "on:2:1")
    rc, data = _dry_run(tmp_path)
    assert rc == 0
    rows = {(r["arm"], r["job"]): r for r in data["results"]}
    assert len(data["results"]) == 4
    r = rows[("on", 2)]
    assert r["invalid"] is False and r["invalid_reason"] is None and r["attempts"] == 2
    assert "APIConnectionError" in r["failed_attempts"][0]["reason"]
    assert r["notebook_chars"] == 0                      # the broken job's notebook edit is gone
    state = Path(data["run_dir"]) / "on" / "state"
    assert not (state / ".awos" / "projects").exists()
    assert all(rows[k]["attempts"] == 1 and not rows[k]["invalid"]
               for k in rows if k != ("on", 2))
    assert data["summary"]["dropped_jobs"] == {}
    assert data["summary"]["on"]["all"]["jobs"] == 2
    assert "notebook restored; retry 1/2" in capsys.readouterr().out


def test_notebook_restored_to_prior_content(tmp_path):
    state = tmp_path / "state"
    nb = state / ".awos" / "projects" / "p1" / "notebook.md"
    nb.parent.mkdir(parents=True)
    nb.write_text("learned in job 1\n")
    snap = js.snapshot_notebook(state)
    nb.write_text("learned in job 1\nhalf-written by a broken job\n")
    (state / ".awos" / "projects" / "p2").mkdir()
    js.restore_notebook(state, snap)
    js.drop_snapshot(snap)
    assert nb.read_text() == "learned in job 1\n"
    assert not (state / ".awos" / "projects" / "p2").exists()
    assert not snap.parent.exists()


def test_still_invalid_is_recorded_and_dropped_pairwise(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_SERIES_DRY_FAIL", "off:1:99")
    monkeypatch.setenv("AWOS_SERIES_RETRIES", "1")
    rc, data = _dry_run(tmp_path)
    assert rc == 0                                        # the run keeps going
    rows = {(r["arm"], r["job"]): r for r in data["results"]}
    assert rows[("off", 1)]["invalid"] is True and rows[("off", 1)]["attempts"] == 2
    assert "APIConnectionError" in rows[("off", 1)]["invalid_reason"]
    assert rows[("on", 1)]["invalid"] is False
    # Job 1 leaves the comparison in both arms, job 2 stays.
    assert list(data["summary"]["dropped_jobs"]) == ["1"]
    assert data["summary"]["off"]["all"]["jobs"] == 1
    assert data["summary"]["on"]["all"]["jobs"] == 1
    out = capsys.readouterr().out
    assert "dropped job 1 from both arms" in out and "off: log shows" in out


def test_summarize_pairwise_exclusion():
    rows = [{"arm": a, "job": n, "solved": True, "cost_usd": 1.0, "turns": 3, "minutes": 1.0,
             "invalid": (a == "on" and n == 2), "invalid_reason": "log shows 'Connection error'"}
            for a in ("off", "on") for n in (1, 2, 3)]
    s = js.summarize(rows, ["off", "on"])
    assert s["off"]["all"]["jobs"] == 2 and s["on"]["all"]["jobs"] == 2
    assert s["dropped_jobs"] == {"2": "on: log shows 'Connection error'"}
    # Rows from before this field existed (resume of an old file) count as valid.
    old = [{k: v for k, v in r.items() if k not in ("invalid", "invalid_reason")} for r in rows]
    assert js.summarize(old, ["off", "on"])["off"]["all"]["jobs"] == 3


def test_report_usage_overrides_ledger(tmp_path, monkeypatch):
    usage = {"cost_usd": 0.25, "turns": 9, "input_tokens": 1000, "output_tokens": 200,
             "models": ["openrouter/some-model"]}
    monkeypatch.setenv("AWOS_SERIES_DRY_USAGE", json.dumps(usage))
    rc, data = _dry_run(tmp_path, "--arms", "on", "--jobs", "1")
    [r] = data["results"]
    assert r["usage_source"] == "report"
    assert r["cost_usd"] == 0.25 and r["turns"] == 9
    assert r["input_tokens"] == 1000 and r["output_tokens"] == 200
    assert r["models"] == ["openrouter/some-model"]
    # Incomplete usage dicts are ignored in favour of the ledger.
    monkeypatch.setenv("AWOS_SERIES_DRY_USAGE", json.dumps({"cost_usd": 1.0}))
    rc, data = _dry_run(tmp_path / "b", "--arms", "on", "--jobs", "1")
    assert data["results"][0]["usage_source"] == "ledger" and data["results"][0]["cost_usd"] == 0


def test_arms_mapping_drives_cli_and_child(tmp_path, monkeypatch):
    assert set(js.ARM_CHILDREN) == {"off", "on", "aider", "aider-frontier"}
    for arm, child in js.ARM_CHILDREN.items():
        assert "{job_dir}" in child["cmd"] and "{project}" in child["cmd"]
        if arm.startswith("aider"):
            # The market harness gets no project memory; only its model differs.
            model = js.FRONTIER_MODEL if arm == "aider-frontier" else js.PINNED_MODEL
            assert child["cmd"][child["cmd"].index("--model") + 1] == model
            continue
        assert child["env"]["AWOS_NOTEBOOK"] == ("1" if arm == "on" else "0")
        assert child["env"]["AWOS_AGENT_MODEL"] == js.PINNED_MODEL
    with pytest.raises(SystemExit):
        js.main(["run", "--arms", "nope", "--dry-run"])
    # A new arm is just a new key: --arms accepts it and its env reaches the child.
    monkeypatch.setitem(js.ARM_CHILDREN, "extra", {
        "cmd": [sys.executable, "-c", "print('unused under dry-run')"],
        "env": {"AWOS_NOTEBOOK": "extra-arm"}})
    rc, data = _dry_run(tmp_path, "--arms", "extra", "--jobs", "1")
    assert rc == 0 and [r["arm"] for r in data["results"]] == ["extra"]
    assert "notebook=extra-arm" in (Path(data["run_dir"]) / "extra" / "run.log").read_text()


def test_real_child_command_is_filled_from_mapping(tmp_path, monkeypatch):
    # Non-dry path: the arm's cmd template runs with placeholders filled; a child
    # that writes report.json usage gives a valid, non-zero-turn result.
    script = tmp_path / "fake_harness.py"
    script.write_text(textwrap.dedent("""
        import json, sys, pathlib
        job_dir, project = sys.argv[1], sys.argv[2]
        print("fake harness", pathlib.Path(job_dir).name, pathlib.Path(project).name, flush=True)
        pathlib.Path("report.json").write_text(json.dumps({"success": True, "usage": {
            "cost_usd": 0.01, "turns": 3, "input_tokens": 10, "output_tokens": 2,
            "models": ["m"]}}))
    """))
    monkeypatch.setitem(js.ARM_CHILDREN, "fake", {
        "cmd": [sys.executable, str(script), "{job_dir}", "{project}"], "env": {}})
    monkeypatch.setattr(js, "backend_ok", lambda: True)
    make_series(tmp_path, "mini")
    out_root = tmp_path / "out"
    assert js.main(["run", "--series", "mini", "--series-root", str(tmp_path),
                    "--out-root", str(out_root), "--arms", "fake", "--jobs", "1"]) == 0
    [f] = out_root.glob("job_series_*.json")
    data = json.loads(f.read_text())
    [r] = data["results"]
    assert r["invalid"] is False and r["turns"] == 3 and r["orchestrator_success"] is True
    log = Path(data["run_dir"]) / "fake" / "run.log"
    assert "fake harness 01_sub project" in log.read_text()


def test_wait_for_backend_backs_off_then_gives_up(capsys):
    naps: list[float] = []
    answers = iter([False, False, True])
    assert js.wait_for_backend(max_wait_s=900, check=lambda: next(answers), sleep=naps.append)
    assert naps == [15.0, 30.0]
    naps.clear()
    assert not js.wait_for_backend(max_wait_s=100, check=lambda: False, sleep=naps.append)
    assert sum(naps) == 100 and naps[:3] == [15.0, 30.0, 55.0]
    assert "waiting 15s for the backend" in capsys.readouterr().out


def test_backend_down_after_invalid_job_stops_cleanly(tmp_path, monkeypatch):
    # Real (non-dry) path with a child that always shows a dead key: after the
    # invalid attempt the backend never comes back, so the run stops, resumable.
    script = tmp_path / "dead_key.py"
    script.write_text("print('litellm.AuthenticationError: API key expired', flush=True)\n")
    monkeypatch.setitem(js.ARM_CHILDREN, "dead", {
        "cmd": [sys.executable, str(script)], "env": {}})
    checks = iter([True])
    monkeypatch.setattr(js, "backend_ok", lambda: next(checks, False))
    monkeypatch.setenv("AWOS_SERIES_BACKEND_WAIT_S", "0")
    make_series(tmp_path, "mini")
    out_root = tmp_path / "out"
    assert js.main(["run", "--series", "mini", "--series-root", str(tmp_path),
                    "--out-root", str(out_root), "--arms", "dead"]) == 0
    [f] = out_root.glob("job_series_*.json")
    data = json.loads(f.read_text())
    assert "backend down after invalid dead job 1" in data["stopped"]
    [r] = data["results"]
    assert r["invalid"] is True and r["attempts"] == 1 and r["job"] == 1
