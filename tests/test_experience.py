"""
scaffold/agent/experience.py — ablation B (AWOS_EXPERIENCE) and the always-on
trajectory log, plus their wiring in one_shot, the orchestrator and
scripts/job_series.py. No network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import experience as exp
from scaffold.agent import one_shot
from scaffold.agent.orchestrator import Orchestrator

REPO = Path(__file__).resolve().parent.parent


def _green(passed=3):
    return SimpleNamespace(passed=passed, failed=0, errors=0, no_tests_found=False,
                           timed_out=False, infra_error=False,
                           test_command=["python", "-m", "pytest", "-q"])


def _red():
    return SimpleNamespace(passed=2, failed=1, errors=0, no_tests_found=False,
                           timed_out=False, infra_error=False, test_command=["pytest"])


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_EXPERIENCE_DIR", str(tmp_path / "exp"))
    monkeypatch.setenv("AWOS_EXPERIENCE_REPO_KEY", "backupd")
    monkeypatch.setenv("AWOS_EXPERIENCE", "1")
    return tmp_path / "exp" / "backupd.jsonl"


def _project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return root


# ── Default off ─────────────────────────────────────────────────────────────

def test_default_off_no_injection_and_identical_prompts(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_EXPERIENCE", raising=False)
    monkeypatch.setenv("AWOS_EXPERIENCE_DIR", str(tmp_path / "exp"))
    assert exp.enabled() is False
    # Even with a store on disk, nothing is read or injected when off.
    monkeypatch.setenv("AWOS_EXPERIENCE", "1")
    exp.save_record(".", goal="Fix add", files=["pkg/calc.py"], diff="-a\n+b",
                    success=True, test_result=_green())
    monkeypatch.delenv("AWOS_EXPERIENCE")
    assert exp.experience_context("Fix add", ".") == ("", [])
    assert Orchestrator._experience_context("Fix add", ".") == ""
    # Off: save is a no-op too.
    assert exp.save_record(".", goal="g", files=[], diff="", success=True,
                           test_result=_green()) is None

    # one-shot messages: no extra, no change from the plain build.
    root = _project()
    ctx = one_shot.build_context(str(root), "Fix add", None)
    assert ctx.extra == ""
    msgs = one_shot.build_messages("Fix add", ctx)
    assert msgs[1]["content"].startswith("# Task\n\nFix add\n\n# Repository\n\n")
    # agent prompt: an empty experience changes nothing.
    task = {"action": "Fix add"}
    assert Orchestrator._agent_loop_prompt(task, {"experience": ""}) == \
        Orchestrator._agent_loop_prompt(task, {})


def test_on_injects_into_one_shot_and_agent_prompt(store, capsys):
    exp.save_record(".", goal="Fix add in calc to return the sum", files=["pkg/calc.py"],
                    diff="-    return a - b\n+    return a + b", success=True,
                    test_result=_green())
    block, ids = exp.experience_context("add should return a sum", ".")
    assert ids == [1] and exp.HEADER in block and "pkg/calc.py" in block
    out = capsys.readouterr().out
    assert "[EXPERIENCE] saved record (#1)" in out
    assert "[EXPERIENCE] store: 1 records; injected 1 (#1, ~" in out

    root = _project()
    ctx = one_shot.build_context(str(root), "task", {"extra_context": block})
    assert ctx.extra == block
    user = one_shot.build_messages("task", ctx)[1]["content"]
    assert user.index(exp.HEADER) < user.index("# Repository")
    # explicit argument wins over the exploration key
    assert one_shot.build_context(str(root), "t", {"extra_context": block},
                                  extra_context="X").extra == "X"
    prompt = Orchestrator._agent_loop_prompt({"action": "task"}, {"experience": block})
    assert exp.HEADER in prompt


# ── Store only on verified-green success ────────────────────────────────────

def test_store_only_on_success_with_green_tests(store):
    assert exp.save_record(".", goal="a", files=["x.py"], diff="d", success=False,
                           test_result=_green()) is None
    assert exp.save_record(".", goal="a", files=["x.py"], diff="d", success=True,
                           test_result=_red()) is None
    assert exp.save_record(".", goal="a", files=["x.py"], diff="d", success=True,
                           test_result=None) is None
    no_tests = SimpleNamespace(passed=0, failed=0, errors=0, no_tests_found=True)
    assert exp.save_record(".", goal="a", files=["x.py"], diff="d", success=True,
                           test_result=no_tests) is None
    assert not store.exists()
    assert exp.save_record(".", goal="a", files=["x.py"], diff="d", success=True,
                           test_result=_green(), cost_usd=0.01, turns=2) == 1
    [rec] = exp.load_records(".")
    assert rec["test_command"] == "python -m pytest -q" and rec["turns"] == 2
    assert rec["test_status"] == "3 passed, 0 failed"


# ── BM25 ────────────────────────────────────────────────────────────────────

def test_bm25_ranks_relevant_record_first():
    recs = [
        {"id": 1, "goal": "Add a --verbose flag to the CLI", "files": ["backupd/cli.py"]},
        {"id": 2, "goal": "Retention policy: prune snapshots older than N days",
         "files": ["backupd/retention.py", "tests/test_retention.py"]},
        {"id": 3, "goal": "Fix checksum verification of restored files",
         "files": ["backupd/verify.py"]},
        {"id": 4, "goal": "Log rotation for the daemon log", "files": ["backupd/log.py"]},
    ]
    ranked = exp.bm25_rank("Prune old snapshots according to the retention settings", recs)
    assert ranked[0][1]["id"] == 2
    ranked = exp.bm25_rank("checksum mismatch after restore", recs)
    assert ranked[0][1]["id"] == 3
    assert exp.bm25_rank("completely unrelated zebra", recs) == []
    assert len(exp.bm25_rank("backupd", recs)) <= exp.TOP_K
    assert "retention" in exp.tokenize("RetentionPolicy") and "policy" in exp.tokenize(
        "RetentionPolicy")


def test_token_cap_respected_and_diffs_trimmed():
    big = "+" + "x = 1\n" * 20000
    recs = [{"id": i, "goal": f"change {i}", "files": [f"f{i}.py"], "diff": big}
            for i in range(3)]
    block = exp.build_block(recs, cap_tokens=3000)
    assert exp.estimate_tokens(block) <= 3000
    assert block.count("### Past change ") == 3 and "trimmed" in block
    tiny = exp.build_block(recs, cap_tokens=300)
    assert exp.estimate_tokens(tiny) <= 300


def test_retrieval_respects_default_cap(store):
    for i in range(5):
        exp.save_record(".", goal=f"Retention prune job {i}", files=["backupd/retention.py"],
                        diff="+" + "y\n" * 50000, success=True, test_result=_green())
    block, ids = exp.experience_context("retention prune", ".")
    assert len(ids) == 3 and exp.estimate_tokens(block) <= exp.DEFAULT_TOKENS
    # stored diffs are bounded too
    assert all(len(r["diff"]) <= exp.STORED_DIFF_CHARS for r in exp.load_records("."))


# ── Trajectory log ──────────────────────────────────────────────────────────

def test_trajectory_written_with_call_slice(tmp_path, monkeypatch):
    calls = tmp_path / "llm_calls.jsonl"
    now = 1_000_000.0
    lines = [{"ts": now - 100, "pid": os.getpid(), "component": "before"},
             {"ts": now + 1, "pid": os.getpid(), "component": "agent"},
             {"ts": now + 2, "pid": os.getpid() + 1, "component": "other process"},
             {"ts": now + 3, "pid": os.getpid(), "component": "one_shot"}]
    calls.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(calls))
    monkeypatch.setenv("AWOS_TRAJECTORY_DIR", str(tmp_path / "traj"))
    path = exp.write_trajectory(goal="g", model="m", verdict={"success": True},
                                files_changed=["a.py"], diff="d" * 50000,
                                start_ts=now, end_ts=now + 10, cost_usd=0.01)
    assert path is not None and path.parent == tmp_path / "traj"
    [rec] = [json.loads(l) for l in path.read_text().splitlines()]
    assert [c["component"] for c in rec["calls"]] == ["agent", "one_shot"]
    assert rec["turns"] == 2 and len(rec["diff"]) <= exp.TRAJ_DIFF_CHARS
    assert rec["files_changed"] == ["a.py"] and rec["model"] == "m"


def test_trajectory_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("AWOS_TRAJECTORY_DIR", str(blocker / "sub"))  # mkdir fails
    assert exp.write_trajectory(goal="g", model=None, verdict={}, files_changed=None,
                                diff=None, start_ts=0) is None
    monkeypatch.setenv("AWOS_TRAJECTORY_DIR", "0")
    assert exp.write_trajectory(goal="g", model=None, verdict={}, files_changed=[],
                                diff="", start_ts=0) is None
    # Off by default under pytest (never writes into the repo's .awos/).
    monkeypatch.delenv("AWOS_TRAJECTORY_DIR")
    assert exp.trajectory_dir() is None


def test_goal_end_hook_writes_trajectory_and_saves_on_green(store, tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_TRAJECTORY_DIR", str(tmp_path / "traj"))
    orch = Orchestrator.__new__(Orchestrator)  # the hook needs no other state
    orch._run_files_changed = {"pkg/calc.py"}
    root = _project()
    green = [{"task": {"test_result": _green()}, "success": True}]
    orch._experience_goal_end("Fix add", str(root), 0.0, True, green)
    assert len(exp.load_records(str(root))) == 1
    orch._experience_goal_end("Fix add", str(root), 0.0, False, green)
    orch._experience_goal_end("Fix add", str(root), 0.0, True,
                              [{"task": {"test_result": _red()}}])
    assert len(exp.load_records(str(root))) == 1
    [traj] = list((tmp_path / "traj").glob("*.jsonl"))
    recs = [json.loads(l) for l in traj.read_text().splitlines()]
    assert len(recs) == 3
    assert [r["verdict"]["visible_tests_green"] for r in recs] == [True, True, False]
    # garbage in: still never raises
    orch._experience_goal_end(None, "/nonexistent/x", "bad", True, [None, 3])


# ── job_series: one fresh store per (arm, repeat) ───────────────────────────

_spec = importlib.util.spec_from_file_location("job_series_exp", REPO / "scripts" / "job_series.py")
js = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(js)


def test_job_series_routing_env_records_experience():
    assert "AWOS_EXPERIENCE" in js.ROUTING_ENV


def test_job_series_store_per_arm_and_repeat(tmp_path, monkeypatch):
    """Each (arm, repeat) gets its own store that persists across its jobs."""
    script = tmp_path / "fake_harness.py"
    script.write_text(textwrap.dedent("""
        import json, os, sys, pathlib
        d = pathlib.Path(os.environ["AWOS_EXPERIENCE_DIR"])
        key = os.environ["AWOS_EXPERIENCE_REPO_KEY"]
        f = d / (key + ".jsonl")
        seen = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
        d.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as fh:
            fh.write(json.dumps({"arm": os.environ["AWOS_EVAL_ARM"],
                                 "rep": os.environ["AWOS_EVAL_REPEAT"],
                                 "job": os.environ["AWOS_EVAL_JOB"],
                                 "seen": [(s["arm"], s["rep"], s["job"]) for s in seen]}) + "\\n")
        pathlib.Path("report.json").write_text(json.dumps({"success": True, "usage": {
            "cost_usd": 0.0, "turns": 1, "input_tokens": 1, "output_tokens": 1,
            "models": ["m"]}}))
    """))
    for arm in ("fa", "fb"):
        monkeypatch.setitem(js.ARM_CHILDREN, arm, {
            "cmd": [sys.executable, str(script), "{job_dir}", "{project}"], "env": {}})
    monkeypatch.setattr(js, "backend_ok", lambda: True)
    monkeypatch.setattr(js, "key_usage", lambda state: None)
    sys.path.insert(0, str(REPO / "tests"))
    from test_job_series_runner import make_series
    make_series(tmp_path, "mini")
    out_root = tmp_path / "out"
    assert js.main(["run", "--series", "mini", "--series-root", str(tmp_path),
                    "--out-root", str(out_root), "--arms", "fa,fb", "--jobs", "1,2",
                    "--repeat", "2", "--skip-preflight"]) == 0
    run_dir = next((out_root / "job_series").iterdir())
    for rep in ("1", "2"):
        for arm in ("fa", "fb"):
            f = run_dir / f"r{rep}" / arm / "state" / ".awos" / "experience" / "mini.jsonl"
            recs = [json.loads(l) for l in f.read_text().splitlines()]
            assert [(r["arm"], r["rep"], r["job"]) for r in recs] == [
                (arm, rep, "1"), (arm, rep, "2")]
            assert recs[0]["seen"] == [] and recs[1]["seen"] == [[arm, rep, "1"]]


def test_experience_env_and_snapshot(tmp_path):
    a = js.experience_env(tmp_path / "r1" / "off", tmp_path / "backupd")
    b = js.experience_env(tmp_path / "r2" / "off", tmp_path / "backupd")
    assert a["AWOS_EXPERIENCE_REPO_KEY"] == "backupd"
    assert a["AWOS_EXPERIENCE_DIR"] != b["AWOS_EXPERIENCE_DIR"]
    state = tmp_path / "state"
    (state / ".awos" / "experience").mkdir(parents=True)
    (state / ".awos" / "experience" / "k.jsonl").write_text("one\n")
    snap = js.snapshot_notebook(state, "experience")
    (state / ".awos" / "experience" / "k.jsonl").write_text("one\ntwo-from-invalid\n")
    js.restore_notebook(state, snap, "experience")
    js.drop_snapshot(snap)
    assert (state / ".awos" / "experience" / "k.jsonl").read_text() == "one\n"


# ── Interaction with ablation A (best-of-N) ─────────────────────────────────

def _bon_run(monkeypatch, exploration):
    sys.path.insert(0, os.path.dirname(__file__))
    from test_best_of_n import FIX, WRONG, _SyncPool, _judge_for
    from test_acceptance import _project as acc_project
    from test_acceptance_v2 import _SeqClient
    from scaffold.agent import best_of_n as bon

    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    monkeypatch.setattr(bon, "ThreadPoolExecutor", _SyncPool)
    root = acc_project()
    client = _SeqClient([WRONG, FIX, WRONG])
    bon.run_best_of_n(3, client=client, model="deepseek/deepseek-chat",
                      codebase_root=str(root), task_text="fix add in pkg/calc.py",
                      exploration=exploration, tracker=None, allow_test_edits=False,
                      judge=_judge_for(root))
    return [c["messages"][1]["content"] for c in client.calls
            if c.get("messages") and len(c["messages"]) > 1]


def test_best_of_n_each_candidate_sees_experience_once(monkeypatch):
    block = f"## {exp.HEADER}\n\n### Past change 1: fix sub\nFiles: pkg/calc.py\n"
    prompts = _bon_run(monkeypatch, {"extra_context": block})
    assert len(prompts) >= 3
    for p in prompts[:3]:
        assert p.count(exp.HEADER) == 1
        assert p.index(exp.HEADER) < p.index("# Repository")


def test_best_of_n_without_experience_prompts_unchanged(monkeypatch):
    prompts = _bon_run(monkeypatch, {})
    assert prompts and all(exp.HEADER not in p for p in prompts)
    assert all(p.startswith("# Task\n\nfix add in pkg/calc.py\n\n# Repository\n\n")
               for p in prompts[:3])


def test_prebuilt_context_gets_extra_once():
    import dataclasses

    root = _project()
    ctx = one_shot.build_context(str(root), "t", {"extra_context": "EXTRA"})
    assert ctx.extra == "EXTRA"
    bare = dataclasses.replace(ctx, extra="")
    captured = []

    class _C:
        def __init__(self):
            self.chat = self.completions = self

        def create(self, **kw):
            captured.append(kw["messages"][1]["content"])
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="no blocks"), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    one_shot.run_one_shot(_C(), "m", str(root), "t", context=ctx, extra_context="EXTRA")
    one_shot.run_one_shot(_C(), "m", str(root), "t", context=bare, extra_context="EXTRA")
    one_shot.run_one_shot(_C(), "m", str(root), "t", context=bare)
    assert [c.count("EXTRA") for c in captured] == [1, 1, 0]
