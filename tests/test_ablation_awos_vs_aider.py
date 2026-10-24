"""Driver for Experiment 1 (AWOS vs Aider on Haiku 5.5): gates, resume, combine."""
import importlib.util
import json
from pathlib import Path

import pytest

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


def test_spend_counts_every_attempt_and_every_results_file(tmp_path):
    # spent_usd (all attempts) wins over cost_usd; old rows add failed attempts.
    _rows(tmp_path, 1, "a", [{"arm": "off", "cost_usd": 0.01, "spent_usd": 0.03},
                             {"arm": "aider", "cost_usd": 0.02,
                              "failed_attempts": [{"cost_usd": 0.005}]}])
    # An interrupted earlier run of the same issue still cost money.
    d = tmp_path / "r1" / "a"
    (d / "job_series_20261010T000000.json").write_text(json.dumps(
        {"results": [{"arm": "off", "cost_usd": 0.04}]}))
    assert round(ab.issue_spend(tmp_path, 1, "a"), 4) == 0.095
    total, n = ab.spend(tmp_path, ["a"], 1)
    assert round(total, 4) == 0.095 and n == 1


def test_projection_uses_the_costliest_issue(tmp_path):
    _rows(tmp_path, 1, "a", [{"arm": "off", "cost_usd": 0.01}, {"arm": "aider", "cost_usd": 0.01}])
    _rows(tmp_path, 1, "b", [{"arm": "off", "cost_usd": 0.20}, {"arm": "aider", "cost_usd": 0.10}])
    total, projected, n = ab.projected_next(tmp_path, ["a", "b", "c"], 1)
    assert n == 2 and round(total, 2) == 0.32 and round(projected, 2) == 0.62
    assert ab.projected_next(tmp_path / "empty", ["a"], 1)[1] == ab.FIRST_ISSUE_GUESS_USD


def test_driver_passes_budget_and_stops_on_budget(tmp_path, monkeypatch):
    root = tmp_path / "series"
    for i in range(3):
        _issue(root, f"fresh_{i}")
    (root / "READY").write_text("ok")
    out = tmp_path / "out"
    seen = []

    def fake_run(cmd, cwd=None, check=False, env=None):
        issue = cmd[cmd.index("--series") + 1]
        seen.append((issue, float(env["AWOS_SERIES_BUDGET_USD"])))
        stopped = "series budget exhausted" if issue == "fresh_1" else None
        d = out / "r1" / issue
        d.mkdir(parents=True, exist_ok=True)
        (d / "job_series_20261012T000000.json").write_text(json.dumps({"stopped": stopped,
            "results": [{"arm": "off", "spent_usd": 0.1}, {"arm": "aider", "spent_usd": 0.1}]}))

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    monkeypatch.setenv("AWOS_SERIES_MODEL", "claude-haiku-5-5")
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "1.0",
                    "--min-issues", "3"]) == 0
    assert [i for i, _ in seen] == ["fresh_0", "fresh_1"]          # stopped after fresh_1
    assert seen[0][1] == 1.0 and round(seen[1][1], 4) == 0.8


# ── --preflight-cache and --parallel ──────────────────────────────────────────

def _series(tmp_path, n=3, cache=False):
    root = tmp_path / "series"
    for i in range(n):
        _issue(root, f"fresh_{i}")
    (root / "READY").write_text("ok")
    if cache:
        (root / "_tools").mkdir()
        (root / "_tools" / "validate.json").write_text("{}")
    return root


def _write_result(out, issue, spent=0.1, stopped=None):
    d = out / "r1" / issue
    d.mkdir(parents=True, exist_ok=True)
    (d / "job_series_20261012T000000.json").write_text(json.dumps({"stopped": stopped,
        "results": [{"arm": "off", "spent_usd": spent}, {"arm": "aider", "spent_usd": spent}]}))


def test_preflight_cache_defaults_to_series_tools_and_passes_through(tmp_path, monkeypatch):
    root = _series(tmp_path, 1, cache=True)
    out = tmp_path / "out"
    cmds = []

    def fake_run(cmd, cwd=None, check=False, env=None):
        cmds.append(cmd)
        _write_result(out, cmd[cmd.index("--series") + 1])

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    base = ["run", "--series-root", str(root), "--out", str(out), "--min-issues", "1", "--dry-run"]
    assert ab.main(base) == 0
    cmd = cmds[-1]
    assert cmd[cmd.index("--preflight-cache") + 1] == str((root / "_tools" / "validate.json").resolve())
    explicit = tmp_path / "other.json"
    out = tmp_path / "out2"
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--min-issues", "1",
                    "--dry-run", "--preflight-cache", str(explicit)]) == 0
    assert cmds[-1][cmds[-1].index("--preflight-cache") + 1] == str(explicit.resolve())


def test_no_cache_file_means_no_flag(tmp_path, monkeypatch):
    root = _series(tmp_path, 1)
    out = tmp_path / "out"
    cmds = []
    monkeypatch.setattr(ab.subprocess, "run",
                        lambda cmd, **k: cmds.append(cmd) or _write_result(out, "fresh_0"))
    assert ab.main(["run", "--series-root", str(root), "--out", str(out),
                    "--min-issues", "1", "--dry-run"]) == 0
    assert "--preflight-cache" not in cmds[0]


class _FakePopen:
    """Each child 'runs' for `ticks` polls, then writes its results file."""
    live: list = []
    log: list = []
    max_live = 0
    out: Path = None
    stop_issue = None
    ticks = 2

    def __init__(self, cmd, cwd=None, env=None, stdout=None, stderr=None):
        self.issue = cmd[cmd.index("--series") + 1]
        self.arms = cmd[cmd.index("--arms") + 1]
        self.env, self.left, self.returncode = env, self.ticks, None
        assert stdout is not None and Path(stdout.name).name == "driver.log"
        _FakePopen.live.append(self)
        _FakePopen.log.append(("start", self.issue, float(env["AWOS_SERIES_BUDGET_USD"]),
                               env.get("AWOS_SERIES_CONCURRENT"), self.arms))
        _FakePopen.max_live = max(_FakePopen.max_live, len(_FakePopen.live))

    def poll(self):
        if self.returncode is None:
            self.left -= 1
            if self.left <= 0:
                stopped = "series budget exhausted" if self.issue == _FakePopen.stop_issue else None
                _write_result(_FakePopen.out, self.issue, 0.1, stopped)
                self.returncode = 0
                _FakePopen.live.remove(self)
                _FakePopen.log.append(("end", self.issue))
        return self.returncode


def _fake_popen(monkeypatch, out, stop_issue=None, ticks=2):
    _FakePopen.live, _FakePopen.log, _FakePopen.max_live = [], [], 0
    _FakePopen.out, _FakePopen.stop_issue, _FakePopen.ticks = out, stop_issue, ticks
    monkeypatch.setattr(ab.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(ab.subprocess, "run", lambda *a, **k: pytest.fail("sequential path"))
    monkeypatch.setattr(ab.time, "sleep", lambda s: None)





def test_parallel_respects_limit_splits_budget_and_keeps_arm_order(tmp_path, monkeypatch):
    root = _series(tmp_path, 5)
    out = tmp_path / "out"
    _fake_popen(monkeypatch, out)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "10",
                    "--min-issues", "5", "--dry-run", "--parallel", "2"]) == 0
    starts = [e for e in _FakePopen.log if e[0] == "start"]
    assert [s[1] for s in starts] == [f"fresh_{i}" for i in range(5)]
    assert _FakePopen.max_live == 2
    assert starts[0][2] == 5.0 and starts[1][2] == 5.0      # (cap - spent) / N at launch
    assert all(s[3] == "1" for s in starts)
    assert [s[4] for s in starts] == [",".join(ab.arm_order(i)) for i in range(5)]
    assert all((out / "r1" / f"fresh_{i}" / "driver.log").exists() for i in range(5))
    assert ab.done(ab.load_rows(ab.result_file(out, 1, "fresh_4")))


def test_parallel_launch_needs_room_for_every_in_flight_issue(tmp_path, monkeypatch):
    # cap 0.12: spent 0 + 1*0.05 fits, spent 0 + 2*0.05 fits, 3*0.05 doesn't.
    root = _series(tmp_path, 4)
    out = tmp_path / "out"
    _fake_popen(monkeypatch, out, ticks=50)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "0.12",
                    "--min-issues", "4", "--dry-run", "--parallel", "3"]) == 0
    assert _FakePopen.max_live == 2
    # once those two cost $0.4, the next projection (0.4 + 0.2) passes the cap: stop.
    assert [e[1] for e in _FakePopen.log if e[0] == "start"] == ["fresh_0", "fresh_1"]


def test_parallel_stop_on_budget_waits_for_in_flight(tmp_path, monkeypatch):
    root = _series(tmp_path, 5)
    out = tmp_path / "out"
    _fake_popen(monkeypatch, out, stop_issue="fresh_0")
    # fresh_1 takes longer, so it is still in flight when fresh_0 stops on the budget.
    orig_init = _FakePopen.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        if self.issue == "fresh_1":
            self.left = 6

    monkeypatch.setattr(_FakePopen, "__init__", init)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "10",
                    "--min-issues", "5", "--dry-run", "--parallel", "2"]) == 0
    events = [(e[0], e[1]) for e in _FakePopen.log]
    assert events == [("start", "fresh_0"), ("start", "fresh_1"),
                      ("end", "fresh_0"), ("end", "fresh_1")]      # nothing new, fresh_1 awaited
    assert not _FakePopen.live


def test_parallel_resume_skips_done_issues(tmp_path, monkeypatch):
    root = _series(tmp_path, 3)
    out = tmp_path / "out"
    _write_result(out, "fresh_1")
    _fake_popen(monkeypatch, out)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "10",
                    "--min-issues", "3", "--dry-run", "--parallel", "2"]) == 0
    assert [e[1] for e in _FakePopen.log if e[0] == "start"] == ["fresh_0", "fresh_2"]


# ── --shard I/N and --limit K (CI sharding) ───────────────────────────────────

def test_select_shards_by_global_index_then_limits():
    issues = [f"fresh_{i:02d}" for i in range(10)]
    assert ab.select(issues) == list(enumerate(issues))
    assert ab.select(issues, (1, 4)) == [(1, "fresh_01"), (5, "fresh_05"), (9, "fresh_09")]
    assert ab.select(issues, (1, 4), 2) == [(1, "fresh_01"), (5, "fresh_05")]
    assert ab.select(issues, None, 3) == [(0, "fresh_00"), (1, "fresh_01"), (2, "fresh_02")]
    # every issue lands in exactly one shard
    shards = [x for k in range(8) for x in ab.select(issues, (k, 8))]
    assert sorted(shards) == list(enumerate(issues))


def test_parse_shard():
    assert ab.parse_shard(None) is None and ab.parse_shard("3/8") == (3, 8)
    for bad in ("8/8", "-1/8", "1/0", "x/8", "3"):
        with pytest.raises(ValueError):
            ab.parse_shard(bad)


def test_shard_run_keeps_global_arm_order(tmp_path, monkeypatch):
    root = _series(tmp_path, 6)
    out = tmp_path / "out"
    cmds = []

    def fake_run(cmd, cwd=None, check=False, env=None):
        cmds.append(cmd)
        _write_result(out, cmd[cmd.index("--series") + 1])

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "10",
                    "--min-issues", "6", "--dry-run", "--shard", "1/2"]) == 0
    got = [(c[c.index("--series") + 1], c[c.index("--arms") + 1]) for c in cmds]
    # global indices 1, 3, 5 are all odd: aider first, as in the unsharded run
    assert got == [(f"fresh_{i}", ",".join(ab.arm_order(i))) for i in (1, 3, 5)]
    assert all(arms == "aider,off" for _, arms in got)


def test_shard_with_limit_and_parallel(tmp_path, monkeypatch):
    root = _series(tmp_path, 6)
    out = tmp_path / "out"
    _fake_popen(monkeypatch, out)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--cap", "10",
                    "--min-issues", "6", "--dry-run", "--parallel", "2",
                    "--shard", "0/3", "--limit", "1"]) == 0
    starts = [e for e in _FakePopen.log if e[0] == "start"]
    assert [(s[1], s[4]) for s in starts] == [("fresh_0", "off,aider")]


def test_bad_shard_or_limit_blocks(tmp_path):
    root = _series(tmp_path, 2)
    base = ["run", "--series-root", str(root), "--out", str(tmp_path / "o"),
            "--min-issues", "2", "--dry-run"]
    assert ab.main(base + ["--shard", "2/2"]) == 4
    assert ab.main(base + ["--limit", "0"]) == 4


def test_min_issues_gate_counts_all_issues_not_the_shard(tmp_path, monkeypatch):
    root = _series(tmp_path, 4)
    out = tmp_path / "out"
    monkeypatch.setattr(ab.subprocess, "run",
                        lambda cmd, **k: _write_result(out, cmd[cmd.index("--series") + 1]))
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--min-issues", "4",
                    "--dry-run", "--shard", "0/4"]) == 0
    assert sorted(p.name for p in (out / "r1").iterdir()) == ["fresh_0"]
