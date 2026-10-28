"""
mcp_server.py: no tool edits the caller's directory in place. awos_run queues
a host job (own worktree, review branch, budget) for the top of an existing
git repo and refuses anything else; awos_chat is answer-only; the read-only
tools never create state dirs. No model calls.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytest.importorskip("mcp")


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        mod = importlib.import_module("mcp_server")
    except ImportError as exc:  # no FastMCP / MCPServer in this mcp build
        pytest.skip(f"mcp server API unavailable: {exc}")
    from scaffold.agent.host.queue import JobQueue
    q = JobQueue(tmp_path / "host")
    monkeypatch.setattr(mod, "_queue", lambda: q)
    monkeypatch.setattr(mod, "_state_dir", lambda *parts: tmp_path.joinpath("state", *parts))
    mod._test_queue = q
    return mod


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "b.py").write_text("y = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return repo


def test_awos_run_queues_a_job_and_does_not_touch_the_repo(srv, tmp_path, monkeypatch):
    import scaffold.agent.orchestrator as orch
    monkeypatch.setattr(orch.Orchestrator, "execute_feature",
                        lambda *a, **k: pytest.fail("ran in place"))
    repo = _repo(tmp_path)
    out = json.loads(srv.awos_run("add a flag", str(repo), budget_usd=0.5))
    assert out["queued"] is True and out["repo"] == str(repo.resolve())
    job = srv._test_queue.get(out["job_id"])
    assert job.state == "queued" and job.budget_usd == 0.5 and job.goal == "add a flag"
    assert out["review_branch"] == f"awos/{job.id[:8]}"
    assert _git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("case", ["plain_dir", "subdir", "no_commit", "missing"])
def test_awos_run_refuses_non_repo_roots(srv, tmp_path, case):
    if case == "plain_dir":
        root = tmp_path / "plain"
        root.mkdir()
    elif case == "subdir":
        root = _repo(tmp_path) / "sub"
    elif case == "no_commit":
        root = tmp_path / "empty"
        root.mkdir()
        _git(root, "init", "-q")
    else:
        root = tmp_path / "nope"
    out = json.loads(srv.awos_run("goal", str(root)))
    assert out["queued"] is False and out["error"]
    assert srv._test_queue.list() == []


@pytest.mark.parametrize("budget", [0, -1, 1e6, "x"])
def test_awos_run_bounds_the_budget(srv, tmp_path, budget):
    out = json.loads(srv.awos_run("goal", str(_repo(tmp_path)), budget_usd=budget))
    assert out["queued"] is False and "budget" in out["error"]


def test_awos_job_status(srv, tmp_path):
    out = json.loads(srv.awos_run("goal", str(_repo(tmp_path))))
    st = json.loads(srv.awos_job_status(out["job_id"]))
    assert st["state"] == "queued"
    assert "error" in json.loads(srv.awos_job_status("nope"))


def test_awos_chat_is_answer_only(srv, monkeypatch):
    calls = []

    class FakeAgent:
        def handle_request(self, msg):
            calls.append("handle_request")

        def _handle_reasoning(self, msg):
            calls.append("reasoning")
            return f"answer to {msg}"

    import types
    fake = types.ModuleType("agent.unified_agent")
    fake.UnifiedAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "agent.unified_agent", fake)
    assert srv.awos_chat("refactor everything") == "answer to refactor everything"
    assert calls == ["reasoning"]


def test_read_only_tools_create_no_state(srv, tmp_path):
    assert json.loads(srv.awos_memory_search("q"))["results"] == []
    assert json.loads(srv.awos_traces_list())["sessions"] == []
    assert not (tmp_path / "state").exists()
