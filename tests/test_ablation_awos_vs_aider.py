"""Driver for Experiment 1 (AWOS vs Aider on Haiku 5.5): gates, resume, combine."""
import importlib.util
import json
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "scripts" / "ablation_awos_vs_aider.py"
_s = importlib.util.spec_from_file_location("ablation_awos_vs_aider", _P)
ab = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ab)


def _issue(root: Path, name: str):
    (root / name / "jobs" / "01_x").mkdir(parents=True)


def _rows(out: Path, rep: int, issue: str, rows: list[dict]):
    d = out / f"r{rep}" / issue
    d.mkdir(parents=True, exist_ok=True)
    (d / "job_series_20261011T000000.json").write_text(json.dumps({"results": rows}))


def test_gate_requires_ready_and_min_issues(tmp_path):
    for i in range(3):
        _issue(tmp_path, f"fresh_{i}")
    (tmp_path / "_tools").mkdir()
    issues, why = ab.gate(tmp_path, 3)
    assert issues == ["fresh_0", "fresh_1", "fresh_2"] and "READY" in why
    (tmp_path / "READY").write_text("ok")
    assert ab.gate(tmp_path, 3)[1] is None
    assert "< 4" in ab.gate(tmp_path, 4)[1]


def test_run_blocked_returns_4(tmp_path):
    _issue(tmp_path, "fresh_0")
    assert ab.main(["run", "--series-root", str(tmp_path), "--out", str(tmp_path / "o")]) == 4


def test_arm_order_alternates():
    assert ab.arm_order(0) == ["off", "aider"] and ab.arm_order(1) == ["aider", "off"]


def test_done_and_spend_prefer_billed(tmp_path):
    _rows(tmp_path, 1, "a", [{"arm": "off", "cost_usd": 0.01, "billed_usd": 0.02},
                             {"arm": "aider", "cost_usd": 0.03, "billed_usd": None}])
    _rows(tmp_path, 1, "b", [{"arm": "off", "cost_usd": 0.01}])
    assert ab.done(ab.load_rows(ab.result_file(tmp_path, 1, "a")))
    assert not ab.done(ab.load_rows(ab.result_file(tmp_path, 1, "b")))
    total, n = ab.spend(tmp_path, ["a", "b"], 1)
    assert round(total, 4) == 0.06 and n == 2


def test_combine_rekeys_job_by_issue(tmp_path):
    for rep in (1, 2):
        for issue in ("a", "b"):
            _rows(tmp_path, rep, issue, [{"arm": "off", "job": 1, "solved": True},
                                         {"arm": "aider", "job": 1, "solved": False}])
    assert ab.main(["combine", "--out", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "combined.json").read_text())
    keys = {(r["issue"], r["job"], r["repeat"], r["arm"]) for r in data["results"]}
    assert len(keys) == 8 and {(i, j) for i, j, _, _ in keys} == {("a", 1), ("b", 2)}
