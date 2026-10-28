"""
Speed work on the acceptance path (fresh_astroid_3130: 164 s, mostly a 36 s
full suite run before a 2 s acceptance check, then a repair loop that kept
going after its fix landed):

1. cheap checks first: kept acceptance tests run before the project suite;
2. map_tests(related=True): test files that import the changed module;
3. AgentLoop done_check: the bounded repair stops at "acceptance_green";
4. AWOS_REASONING_AGENT / AWOS_REASONING_REPAIR effort knobs (default: none);
5. host verify_tests reuses the child's green full-suite run on the same tree.

No network: fake clients, scripted agent models, mocked or tiny real pytest.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_acceptance import FIX, GREEN, _orchestrator, _project  # noqa: E402
from test_acceptance_v2 import (  # noqa: E402
    FAIL2, PASS, SUITE_SRC, _LoopSpy, _repair_edit, _SeqClient, _SideEffectScripted,
)

from scaffold.agent import agent_loop as agent_loop_mod  # noqa: E402
from scaffold.agent.acceptance import AcceptanceSuite  # noqa: E402
from scaffold.agent.agent_loop import (  # noqa: E402
    AgentLoop, AnthropicToolClient, ModelReply, ToolCall, with_repair_effort,
)
from scaffold.agent.claude_compat import effort_from_env  # noqa: E402
from scaffold.agent.host import worker  # noqa: E402
from scaffold.agent.test_runner import TestResult, TestRunner  # noqa: E402


@pytest.fixture(autouse=True)
def _host_verify_unsandboxed(monkeypatch):
    """verify_tests needs a sandbox backend or the documented unsafe opt-in
    (host/worker.py); these tests' code is trusted, so opt in."""
    monkeypatch.setenv(worker.UNSAFE_HOST_VERIFY_ENV, "1")

RED = TestResult(passed=2, failed=1, errors=0, pass_rate=0.66, raw_output="1 failed",
                 no_tests_found=False)


@pytest.fixture(autouse=True)
def _safe(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    for name in ("AWOS_ACCEPTANCE_FIRST", "AWOS_ACCEPTANCE_EARLY_STOP",
                 "AWOS_REASONING_AGENT", "AWOS_REASONING_REPAIR", "AWOS_FULL_SUITE_MARKER"):
        monkeypatch.delenv(name, raising=False)


def _run(checks, *, arb_reply, suite_results=None, one_shot="1", script=None, turns="3",
         retries="1", root=None, env=None):
    """Like test_acceptance_v2._run2, but records the order of suite runs and checks."""
    root = root or _project()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(goal="fix add"),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    events: list = []
    results = list(suite_results or [])

    def _suite(**kw):
        events.append("suite")
        return results.pop(0) if results else GREEN

    runner = MagicMock()
    runner.return_value.run.side_effect = _suite
    checks = list(checks)

    def _check(*a, **kw):
        events.append("acc")
        return checks.pop(0)

    scripted = _SideEffectScripted(script or [ModelReply(text="Done.")])
    suite = AcceptanceSuite(source=SUITE_SRC, generated=2, kept=["test_a", "test_b"],
                            cost_usd=0.001)
    seq = _SeqClient(([FIX] if one_shot == "1" else []) + [arb_reply])
    _LoopSpy.inits = []
    with patch.dict(os.environ, {"AWOS_ONE_SHOT": one_shot, "AWOS_ACCEPTANCE": "2",
                                 "AWOS_AGENT_RETRIES": retries,
                                 "AWOS_ACCEPTANCE_REPAIR_TURNS": turns, **(env or {})}), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.agent_loop.AgentLoop", _LoopSpy), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=seq), \
         patch("scaffold.agent.acceptance.build_suite", return_value=suite), \
         patch("scaffold.agent.acceptance.run_acceptance_detail", MagicMock(side_effect=_check)), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    return SimpleNamespace(out=out, events=events, scripted=scripted, root=root,
                           inits=list(_LoopSpy.inits), checks_left=checks)


# ── 1. cheap checks first ───────────────────────────────────────────────────

def test_one_shot_passing_acceptance_runs_the_suite_once_after_it(capsys):
    r = _run([PASS], arb_reply="{}")
    assert r.events == ["acc", "suite"]           # acceptance first, then the gate
    assert r.out["success"] is True and r.out["one_shot"] is True


def test_one_shot_failing_acceptance_skips_suite_until_repair_passes(capsys):
    root = _project()
    r = _run([FAIL2, PASS], script=_repair_edit(root), root=root,
             arb_reply='{"test_a": "CODE_INCOMPLETE", "test_b": "CODE_INCOMPLETE"}')
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] checked first: 2 failing of 2; the project test suite is deferred" in log
    # No suite before the repair; the done check passed after the edit turn,
    # then the suite gated the result exactly once.
    assert r.events == ["acc", "acc", "suite"]
    assert "repair stopped early after turn 1 of 3" in log
    assert "[ACCEPTANCE] repair passed (acceptance_green, 1 turn(s))" in log
    assert r.inits == [3] and r.out["success"] is True


def test_failed_repair_runs_deferred_suite_once_on_restored_edit(capsys):
    root = _project()
    r = _run([FAIL2, FAIL2, FAIL2], script=_repair_edit(root), root=root,
             arb_reply='{"test_a": "CODE_INCOMPLETE", "test_b": "CODE_INCOMPLETE"}')
    log = capsys.readouterr().out
    # one-shot check, done check after the edit turn, post-repair check (fails:
    # no suite for the repair), then the deferred suite on the restored edit.
    assert r.events == ["acc", "acc", "acc", "suite"]
    assert "repair failed; restored the green edit" in log
    assert "running the deferred project test suite on the kept edit" in log
    assert r.out["success"] is True and r.out["one_shot"] is True


def test_deferred_red_one_shot_falls_back_to_the_agent_loop(capsys):
    # Arbitration drops both tests; the deferred suite is red on the one-shot
    # edit: not solved, the agent loop runs (the gate never reports red done).
    r = _run([FAIL2], suite_results=[RED, GREEN],
             arb_reply='{"test_a": "WRONG_TEST", "test_b": "WRONG_TEST"}',
             script=[ModelReply(text="Done.")])
    log = capsys.readouterr().out
    assert "fell back to the agent loop: tests: 2 passed, 1 failed" in log
    assert r.out.get("one_shot") is not True and r.out.get("agent_loop") is True
    assert r.scripted.prompts and "tests: 2 passed, 1 failed" in r.scripted.prompts[0]
    assert r.events[:2] == ["acc", "suite"]


def test_acceptance_first_off_keeps_the_old_order():
    r = _run([PASS], arb_reply="{}", env={"AWOS_ACCEPTANCE_FIRST": "0"})
    assert r.events == ["suite", "acc"]


def test_agent_loop_attempt_checks_acceptance_before_the_suite(capsys):
    first = [ModelReply(text="", tool_calls=[ToolCall(id="t", name="edit_file", arguments={
                 "path": "pkg/calc.py", "old_string": "a - b", "new_string": "a + b"})]),
             ModelReply(text="Done.")]
    r = _run([PASS], one_shot="0", script=first, arb_reply="{}")
    assert r.events == ["acc", "suite"]
    assert r.out["success"] is True and r.out["agent_loop"] is True


# ── 3. AgentLoop done_check ─────────────────────────────────────────────────

class _Replies:
    model = "replay"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, system, messages, registry, tool_choice=None):
        return self.replies.pop(0) if self.replies else ModelReply(text="done")

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text or "x"}

    def format_tool_results(self, calls, results):
        return [{"role": "user", "content": "ok"}]


def _registry(tmp_path):
    from scaffold.agent.tools.base import ToolRegistry
    from scaffold.agent.tools.code_edit import EditFileTool
    from scaffold.agent.tools.filesystem import ReadFileTool
    reg = ToolRegistry()
    reg.project_root = str(tmp_path)
    reg.register(ReadFileTool(project_root=str(tmp_path)))
    reg.register(EditFileTool(project_root=str(tmp_path)))
    return reg


def _edit(i, old, new):
    return ModelReply(text="", tool_calls=[ToolCall(id=f"e{i}", name="edit_file", arguments={
        "path": "m.py", "old_string": old, "new_string": new})])


def _read(i):
    return ModelReply(text="", tool_calls=[ToolCall(id=f"r{i}", name="read_file",
                                                    arguments={"path": "m.py"})])


def test_done_check_stops_after_the_edit_turn(tmp_path):
    (tmp_path / "m.py").write_text("X = 1\n")
    calls = []

    def done():
        calls.append(1)
        return "acceptance_green" if "X = 3" in (tmp_path / "m.py").read_text() else None

    replies = [_read(0), _edit(1, "X = 1", "X = 2"), _read(2), _edit(3, "X = 2", "X = 3"),
               _read(4), ModelReply(text="Done.")]
    out = AgentLoop(registry=_registry(tmp_path), client=_Replies(replies), max_turns=10,
                    done_check=done).run("task")
    assert out.stop_reason == "acceptance_green" and out.success is True
    assert out.turns == 4 and len(calls) == 2         # only after the two edit turns
    assert out.files_touched


def test_done_check_default_none_and_errors_are_ignored(tmp_path):
    (tmp_path / "m.py").write_text("X = 1\n")
    replies = [_edit(1, "X = 1", "X = 2"), ModelReply(text="Done.")]
    out = AgentLoop(registry=_registry(tmp_path), client=_Replies(list(replies)),
                    max_turns=5).run("task")
    assert out.stop_reason == "completed" and out.turns == 2
    (tmp_path / "m.py").write_text("X = 1\n")

    def boom():
        raise RuntimeError("no")
    out = AgentLoop(registry=_registry(tmp_path), client=_Replies(list(replies)),
                    max_turns=5, done_check=boom).run("task")
    assert out.stop_reason == "completed" and out.turns == 2


def test_judge_treats_acceptance_green_as_a_finish(tmp_path):
    orch = _orchestrator()
    orch._run_files_changed = set()
    runner = MagicMock()
    runner.return_value.run.return_value = GREEN
    outcome = SimpleNamespace(stop_reason="acceptance_green", final_message="ok")
    with patch("scaffold.agent.orchestrator.TestRunner", runner):
        v = orch._judge_agent_attempt({"action": "x"}, outcome, ["m.py"], True,
                                      str(tmp_path), None, 1)
    assert v["success"] is True
    from scaffold.agent.orchestrator import _agent_resume_reason
    assert _agent_resume_reason(outcome, v, 0) is None


# ── 2. related test mapping ─────────────────────────────────────────────────

def _astroid_like(root: Path) -> None:
    (root / "astroid").mkdir()
    (root / "astroid" / "__init__.py").write_text("")
    (root / "astroid" / "protocols.py").write_text("X = 1\n")
    (root / "astroid" / "nodes.py").write_text("Y = 1\n")
    t = root / "tests"
    t.mkdir()
    (t / "test_inference.py").write_text("from astroid import nodes, protocols\n")
    (t / "test_regrtest.py").write_text("import astroid.protocols as p\n")
    (t / "test_scoped.py").write_text("from astroid import (\n    nodes)\nnodes.protocols\n")
    (t / "test_attr.py").write_text("from astroid import nodes\n\ndef t():\n    protocols.X\n")
    (t / "test_nodes.py").write_text("from astroid import nodes\n")
    (t / "test_unrelated.py").write_text("def test_x():\n    pass\n")


def test_related_mapping_finds_importers_without_a_name_match(tmp_path):
    _astroid_like(tmp_path)
    runner = TestRunner(str(tmp_path))
    assert runner.map_tests(["astroid/protocols.py"]) == []          # name-only: unchanged
    got = runner.map_tests(["astroid/protocols.py"], related=True)
    assert got == [os.path.join("tests", "test_inference.py"),
                   os.path.join("tests", "test_regrtest.py"),        # strong refs first
                   os.path.join("tests", "test_attr.py")]            # then `protocols.`
    # Name matches come first and the related ones follow, without duplicates.
    got = runner.map_tests(["astroid/nodes.py"], related=True)
    assert got[0] == os.path.join("tests", "test_nodes.py")
    assert os.path.join("tests", "test_unrelated.py") not in got
    assert len(got) == len(set(got))


def test_related_mapping_is_capped(tmp_path, monkeypatch):
    _astroid_like(tmp_path)
    for i in range(20):
        (tmp_path / "tests" / f"test_more{i}.py").write_text("import astroid.protocols\n")
    got = TestRunner(str(tmp_path)).map_tests(["astroid/protocols.py"], related=True)
    assert len(got) == 12


def test_run_related_runs_mapped_not_full(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sql.py").write_text("def f():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_tok.py").write_text(
        "from pkg.sql import f\n\ndef test_f():\n    assert f() == 1\n")
    (tmp_path / "tests" / "test_other.py").write_text("def test_o():\n    pass\n")
    runner = TestRunner(str(tmp_path))
    assert runner.run(changed_files=["pkg/sql.py"]).mode == "full"   # the gate: unchanged
    res = runner.run(changed_files=["pkg/sql.py"], related=True)
    assert res.mode == "mapped" and res.passed == 1


# ── 4. effort knobs ─────────────────────────────────────────────────────────

class _Inner:
    def __init__(self):
        self.kwargs = []
        self.messages = self

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


class _Reg:
    def anthropic_schemas(self):
        return []


def test_agent_effort_unset_sends_nothing(monkeypatch):
    inner = _Inner()
    AnthropicToolClient(inner, "claude-haiku-5-5").complete("s", [{"role": "user",
                                                                   "content": "x"}], _Reg())
    assert "output_config" not in inner.kwargs[0]


def test_agent_effort_env_maps_through_claude_compat(monkeypatch):
    monkeypatch.setenv("AWOS_REASONING_AGENT", "medium")
    inner = _Inner()
    client = AnthropicToolClient(inner, "claude-haiku-5-5")
    client.complete("s", [{"role": "user", "content": "x"}], _Reg())
    assert inner.kwargs[0]["output_config"] == {"effort": "medium"}
    # A model without effort support gets nothing.
    old = _Inner()
    AnthropicToolClient(old, "claude-haiku-4-5").complete("s", [{"role": "user",
                                                                 "content": "x"}], _Reg())
    assert "output_config" not in old.kwargs[0]
    monkeypatch.setenv("AWOS_REASONING_AGENT", "off")
    assert AnthropicToolClient(_Inner(), "claude-haiku-5-5").effort == "low"
    monkeypatch.setenv("AWOS_REASONING_AGENT", "bogus")
    assert AnthropicToolClient(_Inner(), "claude-haiku-5-5").effort is None


def test_repair_effort_overrides_only_when_set(monkeypatch):
    monkeypatch.setenv("AWOS_REASONING_AGENT", "high")
    client = AnthropicToolClient(_Inner(), "claude-haiku-5-5")
    assert with_repair_effort(client) is client                     # unset: same client
    monkeypatch.setenv("AWOS_REASONING_REPAIR", "low")
    repair = with_repair_effort(client)
    assert repair is not client and repair.effort == "low" and client.effort == "high"
    assert with_repair_effort(_Replies([])).__class__ is _Replies   # non-Anthropic: unchanged
    assert effort_from_env("AWOS_REASONING_NOPE", "claude-haiku-5-5") is None


# ── 5. host verify reuse ───────────────────────────────────────────────────

def _tiny_project(root: Path) -> None:
    (root / "mod.py").write_text("X = 1\n")
    (root / "test_mod.py").write_text("from mod import X\n\ndef test_x():\n    assert X == 1\n")


def test_host_reuses_child_green_full_suite_on_same_tree(tmp_path, monkeypatch, capsys):
    _tiny_project(tmp_path)
    marker = tmp_path.parent / f"{tmp_path.name}_marker.json"
    monkeypatch.setenv("AWOS_FULL_SUITE_MARKER", str(marker))
    res = TestRunner(str(tmp_path)).run()                      # the child's gate run
    assert res.mode == "full" and res.passed == 1 and marker.exists()
    monkeypatch.delenv("AWOS_FULL_SUITE_MARKER")
    data = json.loads(marker.read_text())
    assert data["passed"] == 1 and data["tree"]

    with patch.object(TestRunner, "run_targets", side_effect=AssertionError("ran")) as rt:
        out = worker.verify_tests(str(tmp_path), marker=marker)
    assert rt.call_count == 0
    assert out["status"] == "passed" and out["passed"] == 1 and out["full"]["reused"]
    assert "reused child full-suite result" in capsys.readouterr().out
    assert "reused child full-suite result" in out["line"]


def test_host_runs_full_suite_when_tree_or_marker_differs(tmp_path, monkeypatch, capsys):
    _tiny_project(tmp_path)
    marker = tmp_path.parent / f"{tmp_path.name}_marker.json"
    monkeypatch.setenv("AWOS_FULL_SUITE_MARKER", str(marker))
    TestRunner(str(tmp_path)).run()
    monkeypatch.delenv("AWOS_FULL_SUITE_MARKER")
    (tmp_path / "mod.py").write_text("X = 1\nY = 2\n")          # edited after the child's run
    out = worker.verify_tests(str(tmp_path), marker=marker)
    assert out["status"] == "passed" and not out["full"].get("reused")
    assert "the tree changed since" in capsys.readouterr().out
    # No marker at all: as today.
    out = worker.verify_tests(str(tmp_path), marker=tmp_path.parent / "absent.json")
    assert out["status"] == "passed" and not out["full"].get("reused")


def test_red_full_suite_removes_the_marker(tmp_path, monkeypatch):
    _tiny_project(tmp_path)
    marker = tmp_path.parent / f"{tmp_path.name}_marker.json"
    monkeypatch.setenv("AWOS_FULL_SUITE_MARKER", str(marker))
    TestRunner(str(tmp_path)).run()
    assert marker.exists()
    (tmp_path / "mod.py").write_text("X = 2\n")
    assert TestRunner(str(tmp_path)).run().failed == 1
    assert not marker.exists()


def test_tree_hash_ignores_bytecode_and_state_dirs(tmp_path):
    from scaffold.agent.test_runner import workspace_tree_hash
    _tiny_project(tmp_path)
    h = workspace_tree_hash(str(tmp_path))
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"x")
    (tmp_path / ".awos").mkdir()
    (tmp_path / ".awos" / "state.json").write_text("{}")
    assert workspace_tree_hash(str(tmp_path)) == h
    (tmp_path / "new.py").write_text("")
    assert workspace_tree_hash(str(tmp_path)) != h
