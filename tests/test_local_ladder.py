"""Hermetic tests for scripts/local_ladder.py and the amendment-6 local arms in
scripts/job_series.py (no model, no llama-server, no power reads)."""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


js = _load("job_series")
ll = _load("local_ladder")
sa = _load("stats_audit")

LOCAL_ARMS = ("loop-local", "loop-local-nogrammar", "pipeline-local")


# ── arm wiring (job_series) ───────────────────────────────────────────────────


def test_local_arms_env_and_executor():
    want = {"loop-local": ("loop", "1"), "loop-local-nogrammar": ("loop", "0"),
            "pipeline-local": ("agent_loop", None)}
    for arm, (executor, grammar) in want.items():
        child = js.ARM_CHILDREN[arm]
        env = child["env"]
        assert child["cmd"][1:3] == ["{runner}", "_child"]          # the AWOS child
        assert env["AWOS_PROVIDER"] == "local"
        assert env["AWOS_NOTEBOOK"] == "0"
        assert js.child_executor(arm) == executor
        assert env.get("AWOS_LOOP_GRAMMAR") == grammar
        # Every model knob names the local model: no cloud id reaches the child.
        for k, v in env.items():
            if k.endswith("_MODEL"):
                assert v == js.LOCAL_MINI_MODEL, (arm, k, v)
        pins = js.arm_pin_env(arm)
        assert set(pins) == set(js.PIN_ENV)
        assert pins["AWOS_AGENT_MODEL"] == pins["AWOS_PLANNER_MODEL"] == js.LOCAL_MINI_MODEL
    # Non-local arms keep the cloud pin unchanged.
    assert js.arm_pin_env("off") == js.PIN_ENV
    assert js.arm_pin_env("loop") == js.PIN_ENV
    assert js.arm_pin_env("") == js.PIN_ENV
    assert set(js.LOCAL_ARMS) == {"mini-local", *LOCAL_ARMS}
    assert js._harness_name(js.ARM_CHILDREN["pipeline-local"]["cmd"]) is None
    assert "AWOS_LOOP_GRAMMAR" in js.ROUTING_ENV


def test_input_manifest_records_local_env(tmp_path):
    (tmp_path / "job").mkdir()
    (tmp_path / "job" / "task.json").write_text("{}")
    (tmp_path / "proj").mkdir()
    for arm in LOCAL_ARMS:
        env = {**js.ARM_CHILDREN[arm]["env"], "AWOS_SERIES_ARM": arm}
        m = js.input_manifest(arm, tmp_path / "job", tmp_path / "proj", ["x"], env)
        assert m["env"]["AWOS_PROVIDER"] == "local"
        assert m["env"]["AWOS_EXECUTOR"] == js.child_executor(arm)
        assert m["env"]["AWOS_LOOP_GRAMMAR"] == js.ARM_CHILDREN[arm]["env"].get("AWOS_LOOP_GRAMMAR")
        assert m["env"]["AWOS_AGENT_MODEL"] == js.LOCAL_MINI_MODEL


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _tiny_series(root: Path, name: str) -> None:
    s = root / name
    _write(s / "base" / "calc" / "__init__.py", "def add(a, b):\n    return a + b\n")
    _write(s / "base" / "tests" / "test_base.py",
           "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n")
    _write(s / "base" / "conftest.py", "")
    j = s / "jobs" / "01_sub"
    _write(j / "task.json", json.dumps({"id": "01_sub", "goal": "Add sub", "max_turns": 5,
                                        "max_cost_usd": 0.1, "timeout_min": 1}))
    _write(j / "reference" / "calc" / "__init__.py",
           "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    _write(j / "hidden_tests" / "test_sub.py",
           "from calc import sub\ndef test_sub():\n    assert sub(5, 3) == 2\n")


@pytest.mark.timeout(180)
def test_dry_run_all_local_arms_manifests(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(js, "key_usage", lambda state: None)
    monkeypatch.delenv("AWOS_PROVIDER", raising=False)
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "0")      # a leaked shell value must not win
    _tiny_series(tmp_path, "s")
    out = tmp_path / "out"
    arms = "loop-local,mini-local,loop-local-nogrammar,pipeline-local"
    rc = js.main(["run", "--series", "s", "--series-root", str(tmp_path), "--dry-run",
                  "--out-root", str(out), "--arms", arms])
    assert rc == 0
    [f] = out.glob("job_series_*.json")
    data = json.loads(f.read_text())
    rows = {r["arm"]: r for r in data["results"]}
    assert set(rows) == set(arms.split(","))
    # all-local run: the run itself is marked local (backend check, eval_health)
    assert data["model_pin"]["routing"]["AWOS_PROVIDER"] == "local"
    for arm in LOCAL_ARMS:
        env = rows[arm]["inputs"]["env"]
        assert env["AWOS_PROVIDER"] == "local"
        assert env["AWOS_EXECUTOR"] == js.child_executor(arm)
    assert rows["loop-local"]["inputs"]["env"]["AWOS_LOOP_GRAMMAR"] == "1"
    assert rows["loop-local-nogrammar"]["inputs"]["env"]["AWOS_LOOP_GRAMMAR"] == "0"
    assert not [v for v in data["violations"] if v["severity"] == "fatal"]
    log = capsys.readouterr().out
    assert "executor=loop model=local/" in log and "provider=local grammar=1" in log
    assert "executor=agent_loop model=local/" in log


# ── issue selection ───────────────────────────────────────────────────────────


def _issue(root: Path, name: str, files: list[str]) -> None:
    ref = root / name / "jobs" / "01_x" / "reference"
    for f in files:
        _write(ref / f, "x = 1\n")


def _series_root(tmp_path: Path, n_single: int = 5) -> Path:
    root = tmp_path / "series"
    root.mkdir()
    (root / "READY").write_text("ok")
    _issue(root, "fresh_b_two", ["pkg/a.py", "pkg/b.py"])
    _issue(root, "fresh_a_tests_only_extra", ["pkg/a.py", "tests/test_a.py", "pkg/test_x.py"])
    _issue(root, "fresh_c_pyc", ["pkg/a.py", "pkg/__pycache__/a.cpython-312.pyc"])
    for k in range(n_single):
        _issue(root, f"fresh_z{k:02d}", ["mod.py"])
    _issue(root, "_tools", ["x.py"])          # never an issue
    return root


def test_selector_is_deterministic_and_single_file(tmp_path):
    root = _series_root(tmp_path, 5)
    picked = ll.single_file_issues(root, 4)
    assert picked == ["fresh_a_tests_only_extra", "fresh_c_pyc", "fresh_z00", "fresh_z01"]
    assert ll.single_file_issues(root, 4) == picked
    assert ll.issue_source_files(root, "fresh_b_two") == ["pkg/a.py", "pkg/b.py"]
    assert ll.single_file_issues(root, 99)[-1] == "fresh_z04"


# ── arm order ─────────────────────────────────────────────────────────────────


def test_arm_order_williams_square():
    arms = ll.ARMS
    rows = [ll.arm_order(i, arms) for i in range(4)]
    assert all(sorted(r) == sorted(arms) for r in rows)
    for pos in range(4):       # every arm in every position once per 4 issues
        assert sorted(r[pos] for r in rows) == sorted(arms)
    pairs = [(r[k], r[k + 1]) for r in rows for k in range(3)]
    assert len(set(pairs)) == 12          # each ordered neighbour pair exactly once
    assert ll.arm_order(4, arms) == rows[0]
    assert ll.arm_order(0, arms)[0] == "loop-local"


# ── the driver with fakes ─────────────────────────────────────────────────────


class Fake:
    """job_series + server + power, all fake. run() writes a results file."""

    def __init__(self, crash_on: int | None = None, stopped: set | None = None,
                 health: list | None = None, power: list | None = None):
        self.jobs: list[tuple[str, str]] = []
        self.shell: list[list[str]] = []
        self.crash_on = crash_on
        self.stopped = set(stopped or ())
        self.health = list(health or [])
        self.power_seq = list(power or [])
        self.slept = 0.0
        self.prompt = 0.0
        self.logs: list[str] = []

    def run(self, cmd, cwd=None, env=None, stdout=None, stderr=None, check=False, **kw):
        if cmd[0] == "bash":
            self.shell.append(cmd[1:])
            return SimpleNamespace(returncode=0)
        assert env["AWOS_PROVIDER"] == "local"
        issue = cmd[cmd.index("--series") + 1]
        arm = cmd[cmd.index("--arms") + 1]
        out_root = Path(cmd[cmd.index("--out-root") + 1])
        self.jobs.append((issue, arm))
        if self.crash_on is not None and len(self.jobs) == self.crash_on:
            out_root.mkdir(parents=True, exist_ok=True)
            (out_root / "job_series_1.json").write_text(json.dumps({"results": []}))
            raise KeyboardInterrupt("simulated crash")
        stop = (issue, arm) in self.stopped
        if stop:
            self.stopped.discard((issue, arm))
        solved = arm == "loop-local" or (arm == "mini-local" and issue.endswith("0"))
        row = {"arm": arm, "job": 1, "solved": solved, "invalid": False, "cost_usd": 0.0,
               "turns": 10, "input_tokens": 1000, "timed_out": False,
               "inputs": {"project_sha256": "p-" + issue, "task_sha256": "t-" + issue},
               "components": ({"loop": {"stop_reason": "submitted" if solved else "max_steps",
                                        "steps": 10, "format_errors": 1,
                                        "cached_fraction": 0.9, "tokens_per_s": 20.0}}
                              if arm.startswith("loop") else {})}
        if arm == "mini-local":
            row["mini_exit_status"] = "Submitted" if solved else "LimitsExceeded"
        out_root.mkdir(parents=True, exist_ok=True)
        n = len(list(out_root.glob("job_series_*.json")))
        (out_root / f"job_series_{n + 2}.json").write_text(json.dumps(
            {"results": [] if stop else [row], "stopped": "backend check failed" if stop else None}))
        self.prompt += 100
        return SimpleNamespace(returncode=0)

    def get(self, url, timeout=5.0):
        if url.endswith("/health"):
            return (self.health.pop(0) if self.health else 200), ""
        return 200, (f"llamacpp:prompt_tokens_total {self.prompt}\n"
                     f"llamacpp:prompt_seconds_total {self.prompt / 100}\n"
                     f"llamacpp:tokens_predicted_total {self.prompt / 2}\n"
                     f"llamacpp:tokens_predicted_seconds_total {self.prompt / 40}\n")

    def sleep(self, s):
        self.slept += s

    def power(self):
        return self.power_seq.pop(0) if self.power_seq else {"source": "ac", "percent": 100,
                                                            "energy_wh": None}

    def log(self, *a, **kw):
        self.logs.append(" ".join(str(x) for x in a))

    def hooks(self):
        return ll.Hooks(run=self.run, get=self.get, sleep=self.sleep, power=self.power,
                        log=self.log)


def _args(root, out, **kw):
    ns = dict(series_root=str(root), out=str(out), arms=",".join(ll.ARMS), n_issues=4,
              limit=None, dry_run=False, no_ready_check=False, preflight_cache=None,
              health_wait=10.0, max_restarts=2, max_attempts=3, on_battery="wait")
    ns.update(kw)
    return SimpleNamespace(**ns)


@pytest.fixture(autouse=True)
def _no_server_cmd(monkeypatch):
    monkeypatch.setattr(ll, "server_cmd_line", lambda: "llama-server -m x -np 1 -c 24576")
    monkeypatch.setattr(ll, "git_sha", lambda: "deadbeef")


def test_driver_rotation_resume_and_crash(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    fake = Fake(crash_on=6)
    with pytest.raises(KeyboardInterrupt):
        ll.cmd_run(_args(root, out), fake.hooks())
    issues = ll.read_issues(out)
    assert issues == ll.single_file_issues(root, 4)
    assert fake.jobs[:4] == [(issues[0], a) for a in ll.arm_order(0)]
    # restart: the 4 done jobs of issue 0 and the 1 done of issue 1 are skipped; the
    # crashed one (partial file, no row) reruns.
    fake2 = Fake()
    assert ll.cmd_run(_args(root, out), fake2.hooks()) == 0
    order1 = ll.arm_order(1)
    assert fake2.jobs[:3] == [(issues[1], a) for a in order1[1:]]
    assert len(fake.jobs) - 1 + len(fake2.jobs) == 4 * 4
    for i, issue in enumerate(issues[2:], start=2):
        assert [a for x, a in fake2.jobs if x == issue] == ll.arm_order(i)
    # everything done: a third run calls nothing
    fake3 = Fake()
    assert ll.cmd_run(_args(root, out), fake3.hooks()) == 0
    assert fake3.jobs == []
    meta = json.loads((out / "ladder_meta.json").read_text())
    assert len(meta["sessions"]) == 3 and meta["sessions"][0]["server_slots"] == 1
    side = json.loads((out / "r1" / issues[0] / "loop-local" / "ladder.json").read_text())
    att = side["attempts"][-1]
    assert att["server"]["prompt_tokens_total"] == 100
    assert att["server"]["gen_tokens_per_s"] == 20.0
    assert att["power_before"]["source"] == "ac" and att["energy_wh"] is None


def test_driver_reruns_a_stopped_job(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    first = ll.single_file_issues(root, 4)[0]
    fake = Fake(stopped={(first, "mini-local")})
    assert ll.cmd_run(_args(root, out, limit=1), fake.hooks()) == 0
    assert fake.jobs.count((first, "mini-local")) == 2
    assert ll.status(out, first, "mini-local")[0] == "done"


def test_issue_list_mismatch_blocks(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    out.mkdir()
    (out / "issues.txt").write_text("fresh_other\n")
    fake = Fake()
    assert ll.cmd_run(_args(root, out), fake.hooks()) == 4
    assert fake.jobs == []


def test_slots_other_than_one_block(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "server_cmd_line", lambda: "llama-server -np 2")
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    fake = Fake()
    assert ll.cmd_run(_args(root, out), fake.hooks()) == 4
    assert fake.jobs == []


# ── server health / restart, power ────────────────────────────────────────────


def test_ensure_server_restarts_when_down():
    fake = Fake(health=[0, 0, 0, 503, 200])
    ok, restarts = ll.ensure_server(10.0, 3, fake.get, fake.run, fake.sleep, fake.log)
    assert ok and restarts == 1
    assert fake.shell[0][-1] == "stop" and fake.shell[1][-1] == "start"
    assert fake.shell[0][0].endswith("local_model.sh")


def test_ensure_server_healthy_needs_no_restart():
    fake = Fake()
    assert ll.ensure_server(10.0, 3, fake.get, fake.run, fake.sleep, fake.log) == (True, 0)
    assert fake.shell == []


def test_driver_stops_when_server_stays_down(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    fake = Fake(health=[0] * 1000)
    assert ll.cmd_run(_args(root, out, max_restarts=2), fake.hooks()) == 5
    assert fake.jobs == []
    assert [c[-1] for c in fake.shell] == ["stop", "start", "stop", "start"]


def test_driver_waits_for_ac(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    batt = {"source": "battery", "percent": 50, "energy_wh": 30.0}
    fake = Fake(power=[{"source": "ac"}, batt, batt, {"source": "ac"}])
    assert ll.cmd_run(_args(root, out, limit=1), fake.hooks()) == 0
    assert fake.slept >= 120 and len(fake.jobs) == 4
    fake2 = Fake(power=[{"source": "ac"}, batt])
    out2 = tmp_path / "out2"
    assert ll.cmd_run(_args(root, out2, limit=1, on_battery="stop"), fake2.hooks()) == 5


def test_metrics_power_parsers():
    m = ll.parse_metrics("# HELP x\nllamacpp:prompt_tokens_total 10\n"
                         "llamacpp:tokens_predicted_total{slot=\"0\"} 4\nother 1\n")
    assert m == {"llamacpp:prompt_tokens_total": 10.0, "llamacpp:tokens_predicted_total": 4.0}
    before = {"llamacpp:prompt_tokens_total": 10, "llamacpp:tokens_predicted_total": 4,
              "llamacpp:tokens_predicted_seconds_total": 1}
    after = {"llamacpp:prompt_tokens_total": 30, "llamacpp:tokens_predicted_total": 24,
             "llamacpp:tokens_predicted_seconds_total": 2}
    d = ll.metrics_delta(before, after)
    assert d["prompt_tokens_total"] == 20 and d["gen_tokens_per_s"] == 20.0
    assert ll.metrics_delta(after, before) is None          # server restarted mid-job
    assert ll.metrics_delta(None, after) is None
    assert ll.parse_pmset("Now drawing from 'AC Power'\n -InternalBattery-0 80%;") == \
        {"source": "ac", "percent": 80}
    io = '  "AppleRawCurrentCapacity" = 1000\n  "Voltage" = 11000\n'
    assert ll.parse_ioreg_energy_wh(io) == 11.0
    assert ll.energy_used({"source": "battery", "energy_wh": 11.0},
                          {"source": "battery", "energy_wh": 10.5}) == 0.5
    assert ll.energy_used({"source": "ac"}, {"source": "ac"}) is None
    assert ll.slots_from_cmd("llama-server -c 24576 -np 1 -fa on") == 1
    assert ll.server_root("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080"


# ── combine ───────────────────────────────────────────────────────────────────


def test_combine_shapes_and_stats_audit(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    assert ll.cmd_run(_args(root, out), Fake().hooks()) == 0
    res = ll.combine(out)
    assert res["rows"] == 16 and res["missing"] == [] and res["violations"] == []
    issues = ll.read_issues(out)
    for name, (a, b) in ll.PAIRS.items():
        data = json.loads((out / f"combined_{name}.json").read_text())
        assert data["arms"] == [a, b] and data["pair"] == name
        assert {r["arm"] for r in data["results"]} == {a, b}
        assert sorted({r["job"] for r in data["results"]}) == [1, 2, 3, 4]
        assert all(r["issue"] == issues[r["job"] - 1] for r in data["results"])
        d = sa.audit(out / f"combined_{name}.json", a, b)
        assert d["n_tasks"] == 4 and d["label"] in ("KEEP", "INCONCLUSIVE", "REJECT")
    allr = json.loads((out / "combined_all.json").read_text())
    assert allr["arms"] == list(ll.ARMS) and len(allr["results"]) == 16
    sec = json.loads((out / "secondary.json").read_text())
    assert sec["loop-local"]["solved"] == 4 and sec["loop-local"]["finished_properly_rate"] == 1.0
    assert sec["loop-local"]["valid_reply_rate"] == 0.9
    assert sec["mini-local"]["finished_properly_rate"] == 0.25
    assert sec["loop-local"]["wh_per_solve"] is None and "on AC" in sec["loop-local"]["energy_note"]
    assert sec["loop-local"]["tokens_per_s"] == 20.0
    assert sec["loop-local"]["minutes_per_solve"] is not None


def test_combine_flags_input_mismatch(tmp_path):
    root, out = _series_root(tmp_path, 3), tmp_path / "out"
    assert ll.cmd_run(_args(root, out, limit=1), Fake().hooks()) == 0
    issue = ll.read_issues(out)[0]
    f = sorted((out / "r1" / issue / "mini-local").glob("job_series_*.json"))[-1]
    data = json.loads(f.read_text())
    data["results"][0]["inputs"]["project_sha256"] = "different"
    f.write_text(json.dumps(data))
    res = ll.combine(out)
    assert any(v["check"] == "input_mismatch" for v in res["violations"])
    assert {m["issue"] for m in res["missing"]} == set(ll.read_issues(out)[1:])
