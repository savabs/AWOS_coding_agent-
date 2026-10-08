"""
AWOS_ACCEPTANCE=2 — the fail-safe acceptance gate (ablation C2,
docs/specs/ablation_acceptance_v2.md): arbitration, one bounded repair, and
the visible-green edit restored when the repair fails.

No network: fake OpenAI-shaped clients and a scripted agent model.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_acceptance import (  # noqa: E402  (helpers only, no test functions)
    FIX, GREEN, _orchestrator, _project, _Scripted,
)

from scaffold.agent import acceptance  # noqa: E402
from scaffold.agent import agent_loop as agent_loop_mod  # noqa: E402
from scaffold.agent.acceptance import (  # noqa: E402
    AcceptanceSuite, parse_arbitration, restore_workspace, snapshot_workspace,
)
from scaffold.agent.agent_loop import ModelReply, ToolCall  # noqa: E402


@pytest.fixture(autouse=True)
def _safe(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")


class _SeqClient:
    """OpenAI-shaped: replies in order (one-shot edit first, then arbitration)."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else "{}"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=50),
        )


class _SideEffectScripted(_Scripted):
    """A scripted agent model; a callable in the script runs, then returns its reply."""

    def complete(self, system, messages, registry):
        if len(messages) == 1:
            self.prompts.append(messages[0]["content"])
        if not self._replies:
            return ModelReply(text="done")
        item = self._replies.pop(0)
        return item() if callable(item) else item


SUITE_SRC = ("def test_a():\n    from pkg.calc import add\n    assert add(1, 2) == 3\n\n\n"
             "def test_b():\n    from pkg.calc import sub\n    assert sub(3, 1) == 2\n")
FAIL2 = (False, "2 failing of 2", "FB", {"test_a": "E   assert 0 == 3",
                                         "test_b": "E   ImportError: sub"})
FAIL_B = (False, "1 failing of 2", "FB", {"test_b": "E   ImportError: sub"})
PASS = (True, "2 kept, 2 passed", "", {})


class _LoopSpy(agent_loop_mod.AgentLoop):
    inits: list = []

    def __init__(self, *a, **kw):
        _LoopSpy.inits.append(kw.get("max_turns"))
        super().__init__(*a, **kw)


def _run2(checks, *, arb_reply, one_shot="1", script=None, mode="2", turns="3",
          retries="1", root=None):
    root = root or _project()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(goal="fix add"),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.return_value = GREEN
    scripted = _SideEffectScripted(script or [ModelReply(text="Done.")])
    suite = AcceptanceSuite(source=SUITE_SRC, generated=2, kept=["test_a", "test_b"],
                            cost_usd=0.001)
    check = MagicMock(side_effect=list(checks))
    seq = _SeqClient(([FIX] if one_shot == "1" else []) + [arb_reply])
    _LoopSpy.inits = []
    with patch.dict(os.environ, {"AWOS_ONE_SHOT": one_shot, "AWOS_ACCEPTANCE": mode,
                                 "AWOS_AGENT_RETRIES": retries,
                                 "AWOS_ACCEPTANCE_REPAIR_TURNS": turns}), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.agent_loop.AgentLoop", _LoopSpy), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=seq), \
         patch("scaffold.agent.acceptance.build_suite", return_value=suite), \
         patch("scaffold.agent.acceptance.run_acceptance_detail", check), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    return SimpleNamespace(out=out, scripted=scripted, check=check, orch=orch, root=root,
                           suite=suite, seq=seq, inits=list(_LoopSpy.inits))


def _repair_edit(root):
    """The repair's script: edit calc.py (add `sub`), create a new file, finish."""
    def make_new():
        (root / "pkg" / "extra.py").write_text("X = 1\n", encoding="utf-8")
        return ModelReply(text="", tool_calls=[ToolCall(id="t2", name="read_file", arguments={
            "path": "pkg/calc.py"})], input_tokens=100, output_tokens=20)
    return [
        ModelReply(text="", tool_calls=[ToolCall(id="t1", name="edit_file", arguments={
            "path": "pkg/calc.py", "old_string": "def mul(a, b):",
            "new_string": "def sub(a, b):\n    return a - b\n\n\ndef mul(a, b):"})],
            input_tokens=100, output_tokens=20),
        make_new,
        ModelReply(text="Done repairing.", input_tokens=100, output_tokens=20),
    ]


GREEN_CALC = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"


# ── module ──────────────────────────────────────────────────────────────────

def test_mode_parsing(monkeypatch):
    monkeypatch.delenv("AWOS_ACCEPTANCE", raising=False)
    assert acceptance.acceptance_mode() == "0" and not acceptance.acceptance_enabled()
    monkeypatch.setenv("AWOS_ACCEPTANCE", "1")
    assert acceptance.acceptance_mode() == "1" and acceptance.acceptance_enabled()
    monkeypatch.setenv("AWOS_ACCEPTANCE", "2")
    assert acceptance.acceptance_mode() == "2" and acceptance.acceptance_enabled()
    monkeypatch.delenv("AWOS_ACCEPTANCE_REPAIR_TURNS", raising=False)
    assert acceptance.repair_turns() == 10
    monkeypatch.setenv("AWOS_ACCEPTANCE_REPAIR_TURNS", "4")
    assert acceptance.repair_turns() == 4


def test_parse_arbitration_fail_safe():
    names = ["test_a", "test_b"]
    v, ok = parse_arbitration('Sure: {"test_a": "CODE_INCOMPLETE", "test_b": "WRONG_TEST"}',
                              names)
    assert ok and v == {"test_a": "CODE_INCOMPLETE", "test_b": "WRONG_TEST"}
    v, ok = parse_arbitration("I think test_a is fine", names)
    assert not ok and set(v.values()) == {"WRONG_TEST"}
    v, ok = parse_arbitration('{"test_a": "maybe"}', names)   # unknown/missing -> WRONG
    assert ok and set(v.values()) == {"WRONG_TEST"}


def test_snapshot_restore_walk_mode(tmp_path):
    (tmp_path / "a.py").write_text("green\n")
    (tmp_path / "keep.py").write_text("same\n")
    snap = snapshot_workspace(str(tmp_path))
    assert snap.mode == "walk"
    (tmp_path / "a.py").write_text("repaired\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "new.py").write_text("new\n")
    (tmp_path / "keep.py").unlink()
    touched = restore_workspace(snap)
    assert (tmp_path / "a.py").read_text() == "green\n"
    assert (tmp_path / "keep.py").read_text() == "same\n"
    assert not (tmp_path / "sub" / "new.py").exists()
    assert set(touched) == {"a.py", "keep.py", os.path.join("sub", "new.py")}


def test_snapshot_restore_git_mode(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.py").write_text("base\n")
    (tmp_path / "b.py").write_text("base b\n")
    (tmp_path / ".gitignore").write_text("*.log\n")
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    (tmp_path / "a.py").write_text("green\n")            # the green edit
    (tmp_path / "green_new.py").write_text("g\n")         # a file the green edit added
    snap = snapshot_workspace(str(tmp_path))
    assert snap.mode == "git" and set(snap.files) == {"a.py", "green_new.py"}
    (tmp_path / "a.py").write_text("repaired\n")
    (tmp_path / "b.py").write_text("repair touched b\n")  # clean at snapshot
    (tmp_path / "repair_new.py").write_text("r\n")
    (tmp_path / "green_new.py").unlink()
    restore_workspace(snap)
    assert (tmp_path / "a.py").read_text() == "green\n"
    assert (tmp_path / "b.py").read_text() == "base b\n"
    assert (tmp_path / "green_new.py").read_text() == "g\n"
    assert not (tmp_path / "repair_new.py").exists()


# ── orchestrator: after a green one-shot ────────────────────────────────────

def test_arbitration_drops_wrong_tests_and_accepts(capsys):
    r = _run2([FAIL2], arb_reply='{"test_a": "WRONG_TEST", "test_b": "WRONG_TEST"}')
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] arbitration: 2 failing -> 2 wrong_test, 0 code_incomplete, $" in log
    assert "fell back to the agent loop" not in log
    assert r.out["success"] is True and r.out["one_shot"] is True
    assert r.scripted.prompts == [] and r.inits == []          # no repair, no full loop
    assert r.suite.kept == [] and r.check.call_count == 1
    # arbitration was one call with the test source, failure output and diff context
    arb_msg = r.seq.calls[-1]["messages"][-1]["content"]
    assert "def test_a" in arb_msg and "assert 0 == 3" in arb_msg and "fix add" in arb_msg
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC


def test_unparseable_arbitration_is_fail_safe_accept(capsys):
    r = _run2([FAIL2], arb_reply="Both tests look plausible to me.")
    log = capsys.readouterr().out
    assert "2 wrong_test, 0 code_incomplete" in log and "fail-safe" in log
    assert r.out["success"] is True and r.scripted.prompts == []


def test_code_incomplete_triggers_one_bounded_repair_that_passes(capsys):
    root = _project()
    r = _run2([FAIL2, PASS], script=_repair_edit(root), turns="3", root=root,
              arb_reply='{"test_a": "WRONG_TEST", "test_b": "CODE_INCOMPLETE"}')
    log = capsys.readouterr().out
    assert "1 wrong_test, 1 code_incomplete" in log
    assert "bounded repair: 1 code_incomplete test(s), at most 3 turns" in log
    assert "[ACCEPTANCE] repair passed" in log
    assert r.inits == [3]                                     # exactly one, capped loop
    assert len(r.scripted.prompts) == 1
    assert "test_b" in r.scripted.prompts[0] and "def test_a" not in r.scripted.prompts[0]
    assert r.suite.kept == ["test_b"]
    assert r.out["success"] is True
    assert "def sub" in (r.root / "pkg" / "calc.py").read_text()   # repair kept
    assert (r.root / "pkg" / "extra.py").exists()
    assert "pkg/calc.py" in r.orch._run_files_changed


def test_failed_repair_restores_green_edit_and_succeeds(capsys):
    root = _project()
    r = _run2([FAIL2, FAIL_B], script=_repair_edit(root), turns="3", root=root,
              arb_reply='{"test_a": "CODE_INCOMPLETE", "test_b": "CODE_INCOMPLETE"}')
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] repair failed; restored the green edit" in log
    assert r.inits == [3]
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC     # green contents back
    assert not (r.root / "pkg" / "extra.py").exists()                 # repair's new file gone
    assert r.out["success"] is True
    assert r.orch._run_files_changed == {"pkg/calc.py"}


def test_turn_cap_stops_the_repair_then_restores(capsys):
    # The repair keeps reading files past its 2-turn cap: max_turns, restore.
    reads = [ModelReply(text="", tool_calls=[ToolCall(id=f"r{i}", name="read_file", arguments={
        "path": "pkg/calc.py" if i % 2 else "pkg/__init__.py"})], input_tokens=10,
        output_tokens=5) for i in range(6)]
    r = _run2([FAIL2], script=reads, turns="2",
              arb_reply='{"test_a": "CODE_INCOMPLETE", "test_b": "WRONG_TEST"}')
    log = capsys.readouterr().out
    assert r.inits == [2] and len(r.scripted._replies) == 4    # only 2 turns used
    assert "repair failed; restored the green edit (max_turns" in log
    assert r.out["success"] is True


# ── orchestrator: after the agent loop's green "done" ─────────────────────────

def test_agent_loop_green_gets_one_repair_never_a_resume(capsys):
    first = [ModelReply(text="", tool_calls=[ToolCall(id="t", name="edit_file", arguments={
                 "path": "pkg/calc.py", "old_string": "a - b", "new_string": "a + b"})],
                 input_tokens=100, output_tokens=20),
             ModelReply(text="Done.", input_tokens=100, output_tokens=20)]
    root = _project()
    r = _run2([FAIL2, FAIL2], one_shot="0", script=first + _repair_edit(root),
              turns="5", retries="3", root=root,
              arb_reply='{"test_a": "CODE_INCOMPLETE", "test_b": "CODE_INCOMPLETE"}')
    log = capsys.readouterr().out
    assert "AgentLoop resuming after" not in log
    assert r.inits == [None, 5]                     # the main loop, then one capped repair
    assert len(r.scripted.prompts) == 2
    assert "repair failed; restored the green edit" in log
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC
    assert not (r.root / "pkg" / "extra.py").exists()
    assert r.out["success"] is True and r.out["agent_loop"] is True


# ── modes 1 and 0 unchanged ─────────────────────────────────────────────────

def test_mode_1_never_arbitrates(capsys):
    with patch("scaffold.agent.acceptance.arbitrate") as arb, \
         patch("scaffold.agent.acceptance.run_acceptance",
               MagicMock(side_effect=[(False, "1 failing of 1", "ACC-FEEDBACK"),
                                      (True, "1 kept, 1 passed", "")])):
        r = _run2([], mode="1", arb_reply="{}")
    log = capsys.readouterr().out
    arb.assert_not_called()
    assert "[ONE-SHOT] fell back to the agent loop: acceptance: 1 failing of 1" in log
    assert r.inits == [None] and r.out["success"] is True


def test_mode_0_no_gate(capsys):
    with patch("scaffold.agent.acceptance.arbitrate") as arb:
        r = _run2([], mode="0", arb_reply="{}")
    arb.assert_not_called()
    assert r.check.call_count == 0 and r.out["one_shot"] is True
    assert "[ACCEPTANCE]" not in capsys.readouterr().out
