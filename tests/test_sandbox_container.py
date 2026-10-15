"""
AWOS_SANDBOX=container (sandbox.ContainerSandbox) and the headless loop entry
(scaffold/agent/headless_loop.py) used by the Harbor adapter. The mode is
explicit only: "auto" (or nothing) never selects it. Fakes only, no model call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent import headless_loop as hl  # noqa: E402
from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent import orchestrator as orch  # noqa: E402
from scaffold.agent import sandbox as sb  # noqa: E402
from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, ToolCall  # noqa: E402

MARK = lx.SUBMIT_MARKER


# ── selection ────────────────────────────────────────────────────────────────


def test_container_mode_only_when_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_SANDBOX", "container")
    box = sb.make_sandbox(tmp_path)
    assert isinstance(box, sb.ContainerSandbox) and box.backend == "container"
    assert sb.container_mode() is True
    assert isinstance(sb.make_sandbox(tmp_path, backend="container"), sb.ContainerSandbox)


@pytest.mark.parametrize("value", [None, "", "auto", "seatbelt", "docker", "none", "Container2"])
def test_container_never_selected_implicitly(monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv("AWOS_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("AWOS_SANDBOX", value)
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    monkeypatch.setattr(sb, "seatbelt_available", lambda: False)
    assert sb.make_sandbox(tmp_path) is None
    assert sb.container_mode() is False


def test_container_mode_missing_workspace_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_SANDBOX", "container")
    assert sb.make_sandbox(tmp_path / "nope") is None


def test_sandbox_possible_with_explicit_container(monkeypatch):
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    monkeypatch.setattr(sb, "seatbelt_available", lambda: False)
    orch._sandbox_possible.cache_clear()
    try:
        monkeypatch.setenv("AWOS_SANDBOX", "container")
        assert orch._sandbox_possible() is True
        orch._sandbox_possible.cache_clear()
        monkeypatch.setenv("AWOS_SANDBOX", "auto")
        assert orch._sandbox_possible() is False
    finally:
        orch._sandbox_possible.cache_clear()


def test_loop_registry_accepts_container_sandbox(tmp_path):
    reg = lx.build_loop_registry(sb.ContainerSandbox(tmp_path))
    assert reg.names() == ["bash"] and reg.sandbox.backend == "container"


# ── running commands ────────────────────────────────────────────────────────


def test_container_runs_in_workspace_and_hides_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("GITHUB_TOKEN_SECRET", "x")
    monkeypatch.setenv("AWOS_SANDBOX", "container")
    monkeypatch.setenv("PYTHONPATH", "/installed-agent/awos")
    monkeypatch.setenv("TASK_VISIBLE", "yes")
    box = sb.ContainerSandbox(tmp_path)
    res = box.run("pwd; echo key=${ANTHROPIC_API_KEY:-unset}; echo pp=${PYTHONPATH:-unset};"
                  " echo awos=${AWOS_SANDBOX:-unset}; echo v=$TASK_VISIBLE; echo hi > f.txt")
    assert res.exit_code == 0 and not res.timed_out
    assert os.path.realpath(res.stdout.splitlines()[0]) == os.path.realpath(tmp_path)
    assert "key=unset" in res.stdout and "pp=unset" in res.stdout
    assert "awos=unset" in res.stdout and "v=yes" in res.stdout
    assert (tmp_path / "f.txt").read_text().strip() == "hi"


def test_container_timeout_and_exit_code(tmp_path):
    box = sb.ContainerSandbox(tmp_path)
    assert box.run("exit 3").exit_code == 3
    res = box.run("sleep 5", timeout_sec=1)
    assert res.timed_out and res.exit_code == -9


# ── the prompt tells the truth about the environment ─────────────────────────


def test_prompt_rule_follows_backend():
    default = lx.instance_prompt("do it")
    assert lx.SANDBOX_RULE in default and lx.CONTAINER_RULE not in default
    inside = lx.instance_prompt("do it", sandbox_backend="container")
    assert lx.CONTAINER_RULE in inside and lx.SANDBOX_RULE not in inside


# ── headless entry ──────────────────────────────────────────────────────────


class Scripted:
    model = "claude-haiku-5-5"

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
        self._fmt = AnthropicToolClient(client=None, model=self.model)

    def complete(self, system, messages, registry):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0) if self.replies else ModelReply(text="(silence)")

    def format_assistant_turn(self, reply):
        return self._fmt.format_assistant_turn(reply)

    def format_tool_results(self, calls, results):
        return self._fmt.format_tool_results(calls, results)


def _bash(cmd, i=1, **usage):
    return ModelReply(text="", tool_calls=[ToolCall(id=f"t{i}", name="bash",
                                                    arguments={"command": cmd})],
                      input_tokens=usage.get("inp", 1000), output_tokens=usage.get("out", 50),
                      cached_tokens=usage.get("cached", 0),
                      cache_write_tokens=usage.get("write", 0))


def test_headless_runs_to_submit_without_git_or_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_SANDBOX", "container")
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", "off")
    ws, out = tmp_path / "app", tmp_path / "logs"
    ws.mkdir()
    client = Scripted([
        _bash("echo 42 > answer.txt", 1, inp=1200, out=40, write=1000),
        ModelReply(text=f"{MARK}\nwrote answer.txt", input_tokens=1300, output_tokens=20,
                   cached_tokens=1000),
    ])
    summary = hl.run_headless("Write 42 to answer.txt", str(ws), str(out), client=client)
    assert summary["stop_reason"] == "submitted" and summary["submitted"]
    assert summary["sandbox"] == "container" and summary["error"] is None
    assert (ws / "answer.txt").read_text().strip() == "42"
    assert summary["steps"] == 2 and summary["input_tokens"] == 2500
    assert summary["cached_tokens"] == 1000 and summary["cache_write_tokens"] == 1000
    calls = [json.loads(x) for x in (out / hl.CALLS_FILE).read_text().splitlines()]
    assert [c["call"] for c in calls] == [1, 2]
    assert calls[0]["cache_write_tokens"] == 1000 and calls[1]["cached_tokens"] == 1000
    saved = json.loads((out / hl.RESULT_FILE).read_text())
    assert saved["stop_reason"] == "submitted"
    assert lx.CONTAINER_RULE in client.seen[0][0]["content"]
    assert not (ws / ".awos").exists()  # nothing of AWOS's left in the task workspace


def test_headless_refuses_without_sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    summary = hl.run_headless("x", str(tmp_path), str(tmp_path / "o"), client=Scripted([]))
    assert summary["stop_reason"] == "setup_error" and "no sandbox" in summary["error"]
    assert (tmp_path / "o" / hl.RESULT_FILE).is_file()


def test_headless_stops_at_max_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_SANDBOX", "container")
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", "off")
    client = Scripted([_bash("true", i) for i in range(1, 10)])
    summary = hl.run_headless("loop", str(tmp_path), str(tmp_path / "o"), client=client,
                              max_steps=3)
    assert summary["stop_reason"] == "max_steps" and summary["steps"] == 3


def test_headless_cli_reads_goal_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AWOS_SANDBOX", "container")
    seen = {}

    def fake_run(goal, workspace, out_dir, model, **kw):
        seen.update(goal=goal, workspace=workspace, model=model, **kw)
        return {"stop_reason": "submitted", "steps": 1, "cost_usd_awos": 0.0, "error": None}

    monkeypatch.setattr(hl, "run_headless", fake_run)
    monkeypatch.setenv("HARBOR_GOAL", "fix it")
    rc = hl.main(["--goal-env", "HARBOR_GOAL", "--workspace", str(tmp_path), "--out-dir",
                  str(tmp_path / "o"), "--model", "claude-haiku-5-5", "--cost-cap", "1.5",
                  "--max-steps", "7"])
    assert rc == 0 and seen["goal"] == "fix it" and seen["cost_cap"] == 1.5
    assert seen["max_steps"] == 7 and "HARBOR_GOAL" not in os.environ
