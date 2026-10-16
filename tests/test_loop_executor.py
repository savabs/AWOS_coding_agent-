"""
The loop executor (AWOS_EXECUTOR=loop, scaffold/agent/loop_executor.py): a
mini-style open loop with one sandboxed bash tool, ended by the model's
submit and checked by AWOS's success gate. Fakes only: scripted model
clients (fake Anthropic SDK, fake OpenAI-compatible local server object), a
fake sandbox, a stubbed TestRunner. No model call, no network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent import orchestrator as orch_mod  # noqa: E402
from scaffold.agent.agent_loop import (AnthropicToolClient, ModelReply, OpenAIToolClient,  # noqa: E402
                                       ToolCall)
from scaffold.agent.sandbox import SandboxResult  # noqa: E402
from scaffold.agent.test_runner import TestResult  # noqa: E402

MARK = lx.SUBMIT_MARKER


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeSandbox:
    """Runs the command with /bin/sh in the workspace (a test stand-in for
    Seatbelt/docker) and records every command it was handed."""

    backend = "fake"

    def __init__(self, workspace, outputs=None):
        self.workspace = Path(workspace)
        self.commands: list[str] = []
        self.timeouts: list[int] = []
        self.outputs = outputs  # optional: command -> SandboxResult
        self.closed = False

    def run(self, command, timeout_sec=120):
        self.commands.append(command)
        self.timeouts.append(timeout_sec)
        if self.outputs is not None:
            return self.outputs(command)
        proc = subprocess.run(["/bin/sh", "-c", command], cwd=self.workspace,
                              capture_output=True, text=True, timeout=30)
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False, 0.0)

    def close(self):
        self.closed = True


class Scripted:
    """A ModelClient (Anthropic message shapes) replaying ModelReply objects."""

    model = "claude-haiku-5-5"

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list] = []
        self._fmt = AnthropicToolClient(client=None, model=self.model)

    def complete(self, system, messages, registry):
        self.seen.append([dict(m) for m in messages])
        assert registry.names() == ["bash"]
        return self.replies.pop(0) if self.replies else ModelReply(text="(silence)")

    def format_assistant_turn(self, reply):
        return self._fmt.format_assistant_turn(reply)

    def format_tool_results(self, calls, results):
        return self._fmt.format_tool_results(calls, results)


_n = [0]


def bash(cmd, text="", tin=100, tout=20):
    _n[0] += 1
    return ModelReply(text=text, tool_calls=[ToolCall(id=f"c{_n[0]}", name="bash",
                                                      arguments={"command": cmd})],
                      input_tokens=tin, output_tokens=tout)


def submit(summary="Fixed it.", tin=100, tout=20):
    return ModelReply(text=f"{MARK}\n{summary}", input_tokens=tin, output_tokens=tout)


def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return ws


def _loop(ws, replies, *, gate=None, sandbox=None, **kw):
    sandbox = sandbox or FakeSandbox(ws)
    client = replies if hasattr(replies, "complete") else Scripted(replies)
    ex = lx.LoopExecutor(client, lx.build_loop_registry(sandbox), "Fix add() in calc.py",
                         workspace=str(ws), gate=gate, **kw)
    return ex, ex.run(), sandbox, client


EDIT = "sed -i.bak 's/a - b/a + b/' calc.py && rm calc.py.bak"


# ── prompt ───────────────────────────────────────────────────────────────────


def test_prompt_contains_goal_verbatim_and_submit_marker():
    goal = "`JoinedStr.as_string()` must round-trip:\n- case {v} with 'quotes' and \"more\""
    text = lx.instance_prompt(goal)
    assert goal in text and MARK in text
    assert "Recommended Workflow" in text            # mini's workflow, adapted
    mac = lx.instance_prompt("x", SimpleNamespace(system="Darwin", release="25", machine="arm64"))
    assert "sed -i ''" in mac
    linux = lx.instance_prompt("x", SimpleNamespace(system="Linux", release="6", machine="x86"))
    assert "MacOS" not in linux


def test_first_message_is_the_instance_prompt(tmp_path):
    ws = _ws(tmp_path)
    _, run, _, client = _loop(ws, [submit()], gate=lambda f, s: {"green": True})
    first = client.seen[0][0]
    assert first["role"] == "user" and "Fix add() in calc.py" in first["content"]
    assert MARK in first["content"]


def test_mini_licence_attribution_in_header():
    src = (REPO / "scaffold" / "agent" / "loop_executor.py").read_text()
    assert "mini-swe-agent" in src and "Kilian A. Lieret and Carlos E. Jimenez" in src
    assert "MIT License" in src and "Permission is hereby granted" in src


# ── bash tool ────────────────────────────────────────────────────────────────


def test_bash_runs_in_the_workspace_through_the_sandbox(tmp_path):
    ws = _ws(tmp_path)
    ex, run, sb, _ = _loop(ws, [bash("pwd && ls"), bash(EDIT), submit()],
                           gate=lambda f, s: {"green": True})
    assert len(sb.commands) == 2 and all(c for c in sb.commands)
    assert "pwd && ls" in sb.commands[0]
    assert sb.timeouts == [lx.DEFAULT_CMD_TIMEOUT_S] * 2
    assert "a + b" in (ws / "calc.py").read_text()
    # the observation the model saw: returncode + the workspace's own listing
    obs = ex.messages[2]["content"][0]["content"]
    assert "<returncode>0</returncode>" in obs and "calc.py" in obs
    assert ws.name in obs                      # pwd: the workspace itself
    assert run.files_changed == ["calc.py"] and run.success


def test_registry_requires_a_sandbox():
    with pytest.raises(ValueError):
        lx.build_loop_registry(None)


def test_command_timeout_env(monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_CMD_TIMEOUT_S", "7")
    assert lx.BashTool(FakeSandbox(".")).timeout_s == 7
    monkeypatch.setenv("AWOS_LOOP_CMD_TIMEOUT_S", "99999")
    assert lx.command_timeout() == lx.MAX_CMD_TIMEOUT_S


def test_timeout_and_nonzero_exit_are_reported(tmp_path):
    sb = FakeSandbox(tmp_path, outputs=lambda c: SandboxResult(124, "partial", "", True, 1.0))
    res = lx.BashTool(sb, timeout_s=5).execute({"command": "sleep 99"})
    assert not res.success and "timed out after 5s" in res.text and "partial" in res.text


def test_truncation_keeps_head_and_tail():
    text = "H" * 6000 + "M" * 5000 + "T" * 6000
    cut = lx.truncate_output(text)
    assert cut.startswith("H" * 5000) and cut.endswith("T" * 5000)
    assert "7000 characters elided" in cut and "M" * 100 not in cut
    assert lx.truncate_output("short") == "short"
    obs = lx.render_observation(0, text)
    assert len(obs) < 11_000


# ── limits ───────────────────────────────────────────────────────────────────


def test_step_limit_stops(tmp_path):
    ws = _ws(tmp_path)
    _, run, sb, _ = _loop(ws, [bash("true")] * 10, max_steps_=3,
                          gate=lambda f, s: pytest.fail("no submit, no gate"))
    assert run.stop_reason == "max_steps" and run.steps == 3 and not run.success
    assert len(sb.commands) == 3


def test_step_limit_env_default(monkeypatch):
    monkeypatch.delenv("AWOS_LOOP_MAX_STEPS", raising=False)
    assert lx.max_steps() == 60
    monkeypatch.setenv("AWOS_LOOP_MAX_STEPS", "5")
    assert lx.max_steps() == 5
    monkeypatch.delenv("AWOS_LOOP_GATE_RETRIES", raising=False)
    assert lx.gate_retries() == 2


def test_cost_cap_stops(tmp_path):
    ws = _ws(tmp_path)
    # claude-haiku-5-5: $0.10 / $0.50 per MTok -> 1M input tokens = $0.10 a call
    replies = [bash("true", tin=1_000_000, tout=0) for _ in range(10)]
    _, run, _, _ = _loop(ws, replies, cost_cap=0.25)
    assert run.stop_reason == "cost_cap" and run.steps == 3
    assert run.cost_usd == pytest.approx(0.3)


def test_wall_clock_stops(tmp_path):
    ws = _ws(tmp_path)
    t = [0.0]

    def clock():
        t[0] += 10.0
        return t[0]

    _, run, _, _ = _loop(ws, [bash("true")] * 10, wall_s=35, clock=clock)
    assert run.stop_reason == "wall_clock" and 1 <= run.steps < 10


def test_goal_budget_stops(tmp_path):
    ws = _ws(tmp_path)
    calls = iter([False, False, True])
    _, run, _, _ = _loop(ws, [bash("true")] * 10, budget_exhausted=lambda: next(calls))
    assert run.stop_reason == "goal_budget" and run.steps == 2


def test_format_errors_get_mini_feedback_then_stop(tmp_path):
    ws = _ws(tmp_path)
    ex, run, _, _ = _loop(ws, [ModelReply(text="thinking...")] * 5)
    assert run.stop_reason == "format_error" and run.steps == lx.MAX_FORMAT_ERRORS
    assert any("no bash tool call" in str(m.get("content")) for m in ex.messages)


# ── submit + gate ────────────────────────────────────────────────────────────


def test_submit_gate_green_success(tmp_path):
    ws = _ws(tmp_path)
    seen = []

    def gate(files, submission):
        seen.append((list(files), submission))
        return {"green": True}

    _, run, _, _ = _loop(ws, [bash(EDIT), submit("Changed - to +.")], gate=gate)
    assert run.success and run.stop_reason == "submitted"
    assert run.submits == 1 and run.gate_retries == 0 and run.steps == 2
    assert seen == [(["calc.py"], "Changed - to +.")]


def test_echo_submit_command_is_a_submit_too(tmp_path):
    ws = _ws(tmp_path)
    _, run, sb, _ = _loop(ws, [bash(EDIT), bash(f"echo {MARK}")],
                          gate=lambda f, s: {"green": True})
    assert run.success and run.submits == 1 and len(sb.commands) == 1


def test_red_gate_feedback_goes_into_the_same_conversation_then_green(tmp_path):
    ws = _ws(tmp_path)
    verdicts = iter([
        {"green": False, "retry": True, "feedback": "FAILED tests/test_calc.py::test_sub"},
        {"green": True},
    ])
    ex, run, _, client = _loop(
        ws, [bash(EDIT), submit("first"), bash("echo fixing"), submit("second")],
        gate=lambda f, s: next(verdicts))
    assert run.success and run.submits == 2 and run.gate_retries == 1
    assert run.final_message == "second"
    # same conversation: the 4th call saw the whole history plus the feedback
    history = client.seen[2]
    assert history[0]["content"] == client.seen[0][0]["content"]
    fb = history[-1]
    assert fb["role"] == "user" and "FAILED tests/test_calc.py::test_sub" in fb["content"]
    assert "these tests fail after your change" in fb["content"]
    assert "explain why they are unrelated" in fb["content"]


def test_red_gate_after_echo_submit_feeds_back_as_the_tool_result(tmp_path):
    ws = _ws(tmp_path)
    verdicts = iter([{"green": False, "retry": True, "feedback": "boom"}, {"green": True}])
    ex, run, _, client = _loop(ws, [bash(f"echo {MARK}"), submit()],
                               gate=lambda f, s: next(verdicts))
    assert run.success and run.gate_retries == 1
    block = client.seen[1][-1]["content"][0]
    assert block["type"] == "tool_result" and "boom" in block["content"]


def test_gate_retries_exhausted_reports_failure_honestly(tmp_path):
    ws = _ws(tmp_path)
    red = {"green": False, "retry": True, "feedback": "still failing"}
    _, run, _, _ = _loop(ws, [bash(EDIT)] + [submit()] * 5, gate=lambda f, s: dict(red),
                         gate_retries_=2)
    assert not run.success and run.stop_reason == "gate_red"
    assert run.submits == 3 and run.gate_retries == 2
    assert run.files_changed == ["calc.py"]


def test_green_gate_without_retry_flag_ends_even_if_not_green(tmp_path):
    # Not red with NEW failures (e.g. tests could not run): no feedback, the
    # model's submit stands and the gate's verdict is reported as is.
    ws = _ws(tmp_path)
    _, run, _, _ = _loop(ws, [bash(EDIT), submit()], gate=lambda f, s: {"green": False})
    assert run.submits == 1 and run.gate_retries == 0 and run.stop_reason == "gate_red"


def test_submit_marker_parsing():
    assert lx.submitted_text(f"{MARK}\nDone.") == "Done."
    assert lx.submitted_text(f"I verified it.\n\n{MARK}\nSummary") == "Summary"
    assert lx.submitted_text(f"`{MARK}`") == ""
    assert lx.submitted_text("no marker here") is None
    assert lx.is_submit_command(ToolCall("i", "bash", {"command": f"echo '{MARK}'"}))
    assert not lx.is_submit_command(ToolCall("i", "bash", {"command": f"ls && echo {MARK}"}))


def test_component_shape(tmp_path):
    ws = _ws(tmp_path)
    _, run, _, _ = _loop(ws, [bash(EDIT), submit()], gate=lambda f, s: {"green": True})
    comp = run.component()
    assert comp["status"] == "ran" and comp["steps"] == 2 and comp["submits"] == 1
    assert comp["gate_retries"] == 0 and comp["cost_usd"] > 0


def test_every_call_is_logged_as_component_loop(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(log))
    ws = _ws(tmp_path)
    _loop(ws, [bash("true"), submit()], gate=lambda f, s: {"green": True})
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["component"] for x in lines] == ["loop", "loop"]


# ── providers: fake Anthropic SDK and fake local (OpenAI-compatible) ─────────


class _FakeAnthropicSDK:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        kind, payload = self.script.pop(0)
        if kind == "bash":
            content = [SimpleNamespace(type="text", text="let me look"),
                       SimpleNamespace(type="tool_use", id=f"tu{len(self.calls)}", name="bash",
                                       input={"command": payload})]
            stop = "tool_use"
        else:
            content = [SimpleNamespace(type="text", text=payload)]
            stop = "end_turn"
        usage = SimpleNamespace(input_tokens=50, output_tokens=10,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, usage=usage, stop_reason=stop,
                               model="claude-haiku-5-5")


class _FakeOpenAISDK:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        kind, payload = self.script.pop(0)
        if kind == "bash":
            msg = SimpleNamespace(content="", tool_calls=[SimpleNamespace(
                id=f"oc{len(self.calls)}",
                function=SimpleNamespace(name="bash", arguments=json.dumps({"command": payload})))])
            finish = "tool_calls"
        else:
            msg = SimpleNamespace(content=payload, tool_calls=None)
            finish = "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                               usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
                               model="qwen")


def test_anthropic_client_with_prompt_caching(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_ANTHROPIC_CACHE", raising=False)
    ws = _ws(tmp_path)
    sdk = _FakeAnthropicSDK([("bash", EDIT), ("text", f"{MARK}\ndone")])
    client = AnthropicToolClient(sdk, "claude-haiku-5-5")
    _, run, _, _ = _loop(ws, client, gate=lambda f, s: {"green": True})
    assert run.success and run.steps == 2
    second = sdk.calls[1]
    assert [t["name"] for t in second["tools"]] == ["bash"]
    assert second["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert second["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert second["messages"][1]["content"][1]["type"] == "tool_use"


def test_local_openai_compatible_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    # The tool-call path; the grammar text protocol (default for local) is
    # covered in tests/test_loop_grammar.py.
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "0")
    ws = _ws(tmp_path)
    sdk = _FakeOpenAISDK([("bash", EDIT), ("text", f"{MARK}\ndone")])
    client = OpenAIToolClient(sdk, "local/qwen3.5-9b")
    _, run, _, _ = _loop(ws, client, gate=lambda f, s: {"green": True})
    assert run.success and run.cost_usd == 0.0          # local: $0
    second = sdk.calls[1]["messages"]
    assert second[0]["role"] == "system" and second[-1]["role"] == "tool"
    assert sdk.calls[0]["tools"][0]["function"]["name"] == "bash"


def test_build_client_from_env_local_is_used(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    from scaffold.agent.agent_loop import build_client_from_env
    client = build_client_from_env("claude-haiku-5-5")
    assert isinstance(client, OpenAIToolClient) and client.model.startswith("local/")


# ── orchestrator wiring ──────────────────────────────────────────────────────


@pytest.mark.parametrize("value, choice, label", [
    (None, "agent_loop", "pipeline"), ("pipeline", "agent_loop", "pipeline"),
    ("agent_loop", "agent_loop", "pipeline"), ("loop", "loop", "loop"),
    (" LOOP ", "loop", "loop"),
])
def test_executor_switch_default_is_pipeline(monkeypatch, value, choice, label):
    if value is None:
        monkeypatch.delenv("AWOS_EXECUTOR", raising=False)
    else:
        monkeypatch.setenv("AWOS_EXECUTOR", value)
    assert orch_mod.executor_choice() == choice
    assert orch_mod.executor_label() == label


def test_doctor_shows_the_active_executor(monkeypatch):
    from scaffold.agent import doctor
    monkeypatch.setattr(doctor._ob, "_env_files", lambda: [])
    # Unset: loop when a sandbox exists, else the pipeline (test_executor_default.py).
    monkeypatch.setattr(orch_mod, "_sandbox_possible", lambda: False)
    assert doctor.check_executor({}).detail.startswith("pipeline")
    monkeypatch.setattr(orch_mod, "_sandbox_possible", lambda: True)
    assert doctor.check_executor({}).detail.startswith("loop")
    c = doctor.check_executor({"AWOS_EXECUTOR": "loop"})
    assert c.ok and c.detail.startswith("loop")
    assert not doctor.check_executor({"AWOS_EXECUTOR": "bogus"}).ok


def _orchestrator():
    o = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    o._gui_bus = None
    o.execution_log = []
    for name in ("performance", "reward_store", "escalation", "strategy_router",
                 "obs_store", "_feature_extractor", "_update_task_node",
                 "_persist_worker_failure_pattern"):
        setattr(o, name, MagicMock())
    o.escalation.failure_count.return_value = 0
    return o


GREEN = TestResult(passed=3, failed=0, errors=0, pass_rate=1.0, no_tests_found=False)


def _red(n=1):
    return TestResult(passed=2, failed=n, errors=0, pass_rate=0.5, no_tests_found=False,
                      raw_output="FAILED tests/test_calc.py::test_add - assert -1 == 3")


def _run_task(tmp_path, script, results, monkeypatch, sandbox="fake"):
    ws = _ws(tmp_path)
    o = _orchestrator()
    ctx = {"codebase_root": str(ws), "git": MagicMock(),
           "session": MagicMock(goal="Fix add() in calc.py: it must return a + b",
                                session_id="s1"),
           "codebase_context": {}}
    spec = SimpleNamespace(model_id="claude-haiku-5-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.side_effect = list(results)
    sandboxes = []

    def make_sb(root, backend=None):
        if sandbox is None:
            return None
        sb = FakeSandbox(root)
        sandboxes.append(sb)
        return sb

    ledger = MagicMock()
    client = Scripted(script)
    with patch("scaffold.agent.agent_loop.build_client_from_env", return_value=client), \
         patch("scaffold.agent.sandbox.make_sandbox", side_effect=make_sb), \
         patch("scaffold.agent.budget_ledger.get_ledger", return_value=ledger), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = o._execute_task_via_loop(
            {"task_id": 1, "action": "Fix add() in calc.py: it must return a + b", "file": ""},
            ctx, task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    return SimpleNamespace(out=out, ctx=ctx, ws=ws, orch=o, runner=runner, ledger=ledger,
                           sandboxes=sandboxes, client=client)


def test_orchestrator_loop_green_submit(tmp_path, monkeypatch):
    r = _run_task(tmp_path, [bash(EDIT), submit()], [GREEN], monkeypatch)
    assert r.out["success"] is True and r.out["loop_executor"] is True
    assert r.sandboxes and r.sandboxes[0].workspace == r.ws and r.sandboxes[0].closed
    assert "a + b" in (r.ws / "calc.py").read_text()
    r.ctx["git"].record_modified.assert_called_once_with(str(r.ws / "calc.py"))
    comp = r.orch._components["loop"]
    assert comp["steps"] == 2 and comp["submits"] == 1 and comp["gate_retries"] == 0
    assert comp["cost_usd"] > 0
    # spend reached the ledger per call, tagged "loop"
    assert r.ledger.record.call_count == 2
    assert r.ledger.record.call_args.kwargs["request_type"] == "loop"
    # the goal reached the model verbatim
    assert "it must return a + b" in r.client.seen[0][0]["content"]


def test_orchestrator_loop_red_then_green(tmp_path, monkeypatch):
    r = _run_task(tmp_path, [bash(EDIT), submit(), bash("echo more"), submit()],
                  [_red(), GREEN], monkeypatch)
    assert r.out["success"] is True
    assert r.orch._components["loop"]["gate_retries"] == 1
    fb = r.client.seen[2][-1]["content"]
    assert "test_add" in fb and "1 failed" in fb


def test_orchestrator_loop_retries_exhausted_fails_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_GATE_RETRIES", "1")
    r = _run_task(tmp_path, [bash(EDIT), submit(), submit(), submit()],
                  [_red(), _red(), _red()], monkeypatch)
    assert r.out["success"] is False and r.out["failure_kind"] == "loop_fail"
    assert "stayed red" in r.out["verify_error"]
    assert r.orch._components["loop"]["submits"] == 2
    r.ctx["git"].rollback_file.assert_called_once_with(str(r.ws / "calc.py"))


def test_orchestrator_loop_no_sandbox_never_runs_unsandboxed(tmp_path, monkeypatch):
    r = _run_task(tmp_path, [bash("touch pwned"), submit()], [GREEN], monkeypatch,
                  sandbox=None)
    assert r.out["success"] is False and "sandbox" in r.out["verify_error"]
    assert not (r.ws / "pwned").exists() and r.client.seen == []


def test_execute_single_task_selection_point_routes_loop_first():
    src = Path(orch_mod.__file__).read_text()
    sel = src.index("# ── Executor selection (AWOS_EXECUTOR)")
    assert src.index('if _executor == "loop":', sel) < src.index('if _executor == "agent_loop":', sel)
    assert src.index("self._execute_task_via_loop(", sel) < src.index(
        "self._execute_task_via_agent_loop(", sel)


# ── job_series arm + ablation driver ─────────────────────────────────────────


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_job_series_loop_arm_is_off_plus_executor():
    js = _load("job_series_loop_t", "scripts/job_series.py")
    off, loop = js.ARM_CHILDREN["off"], js.ARM_CHILDREN["loop"]
    assert loop["cmd"] == off["cmd"]
    assert loop["env"] == {**off["env"], "AWOS_EXECUTOR": "loop"}
    assert js.child_executor("loop") == "loop" and js.child_executor("off") == "agent_loop"
    assert js.child_executor("mini") == "agent_loop"
    for knob in ("AWOS_LOOP_MAX_STEPS", "AWOS_LOOP_GATE_RETRIES"):
        assert knob in js.ROUTING_ENV


def test_job_series_input_manifest_records_the_executor(tmp_path):
    js = _load("job_series_loop_t2", "scripts/job_series.py")
    job = tmp_path / "job"
    job.mkdir()
    (job / "task.json").write_text("{}")
    proj = tmp_path / "p"
    proj.mkdir()
    env = {**js.ARM_CHILDREN["loop"]["env"], "AWOS_SERIES_ARM": "loop"}
    m = js.input_manifest("loop", job, proj, ["x"], env)
    assert m["env"]["AWOS_EXECUTOR"] == "loop" and m["env"]["AWOS_SERIES_ARM"] == "loop"


def test_ladder_ci_config_accepts_loop_mini(tmp_path):
    lc = _load("ladder_collect_loop_t", "scripts/ladder_collect.py")
    cfg = tmp_path / "ladder.env"
    cfg.write_text("ARMS=loop,mini\nN_SHARDS=8\nSHARDS=0\nCAP=5\nLIMIT=1\n")
    assert lc.read_config(cfg)["arms"] == "loop,mini"
    assert lc.main(["ci-config", str(cfg)]) == 0
    cfg.write_text("ARMS=lopo,mini\nCAP=5\n")
    with pytest.raises(ValueError):
        lc.read_config(cfg)


def test_ablation_driver_dry_run_with_loop_and_mini(tmp_path, monkeypatch):
    ab = _load("ablation_loop_t", "scripts/ablation_awos_vs_aider.py")
    root = tmp_path / "series"
    for i in range(2):
        (root / f"fresh_{i}" / "jobs" / "01_x").mkdir(parents=True)
    (root / "_tools").mkdir()
    (root / "READY").write_text("ok")
    out = tmp_path / "out"
    cmds = []

    def fake_run(cmd, cwd=None, check=False, env=None):
        cmds.append(cmd)
        issue = cmd[cmd.index("--series") + 1]
        d = out / "r1" / issue
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"arm": a, "job": 1, "solved": None} for a in ("loop", "mini")]
        (d / "job_series_20261015T000000.json").write_text(json.dumps({"results": rows}))

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    assert ab.main(["run", "--series-root", str(root), "--out", str(out), "--min-issues", "1",
                    "--dry-run", "--arms", "loop,mini"]) == 0
    arms = [c[c.index("--arms") + 1] for c in cmds]
    assert arms == ["loop,mini", "mini,loop"] and all("--dry-run" in c for c in cmds)


# ── execute_feature with AWOS_EXECUTOR=loop: no planner, goal check, notebook ─


def test_execute_feature_loop_skips_planner_goal_check_notebook_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k, v in {"AWOS_RUNTIME_SESSION": "true", "AWOS_LEARNING_DISABLE": "true",
                 "AWOS_MCTS_DISABLE": "true", "OPENCODE_GO_API_KEY": "fake-key-for-testing",
                 "AWOS_E2E": "1", "AWOS_EXECUTOR": "loop", "AWOS_GOAL_CHECK": "1",
                 "AWOS_NOTEBOOK": "1", "AWOS_EXPLORATION": "1"}.items():
        monkeypatch.setenv(k, v)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("x = 1\n", encoding="utf-8")
    o = orch_mod.Orchestrator()
    seen = []

    def task(task, ctx):
        seen.append(task)
        o._component("loop", status="ran", steps=3, submits=1, gate_retries=0, cost_usd=0.01)
        return {"task_id": task["task_id"], "success": True, "task": task}

    with patch.object(o, "_execute_single_task", side_effect=task), \
         patch.object(o, "_plan_goal", side_effect=AssertionError("planner called")), \
         patch.object(o, "_update_project_notebook",
                      side_effect=AssertionError("notebook called")), \
         patch.object(o.integration_reviewer, "review",
                      side_effect=AssertionError("review called")), \
         patch("scaffold.agent.orchestrator.run_exploration",
               side_effect=AssertionError("exploration called")), \
         patch("scaffold.agent.goal_check.GoalChecker",
               side_effect=AssertionError("goal check called")), \
         patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
        result = o.execute_feature(goal="Fix calc: x must be 2", codebase_root=str(repo))
    assert result["success"] is True and result["plan_source"] == "skipped_by_executor"
    assert [t["action"] for t in seen] == ["Fix calc: x must be 2"]
    comps = result["components"]
    assert comps["planner"]["status"] == "skipped"
    assert comps["goal_check"]["status"] == "skipped"
    assert comps["notebook"]["status"] == "skipped"
    assert comps["integration_review"]["status"] == "skipped"
    assert comps["loop"]["steps"] == 3 and comps["loop"]["submits"] == 1
    for name in ("acceptance", "one_shot", "arbiter"):
        assert comps[name]["status"] == "not_run"
