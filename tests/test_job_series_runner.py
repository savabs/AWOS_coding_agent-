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
