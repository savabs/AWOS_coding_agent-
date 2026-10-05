"""
scaffold/agent/one_shot.py and the orchestrator's one-shot-first hook (ablation 3).

No network: the model is a fake OpenAI-shaped client, the project a tmp dir.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import one_shot
from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, ToolCall
from scaffold.agent.one_shot import (
    EditBlock, apply_blocks, build_context, parse_blocks, run_one_shot,
)
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.test_runner import TestResult


# ── fixtures ────────────────────────────────────────────────────────────────

def _project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("from .calc import add\n", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n",
        encoding="utf-8")
    (root / "pkg" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "from pkg import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    for junk in (".git", ".awos", ".venv", "__pycache__"):
        (root / junk).mkdir()
        (root / junk / "x.py").write_text("SECRET = 1\n", encoding="utf-8")
    (root / "pkg" / "blob.py").write_bytes(b"\x00\x01binary")
    return root


class _FakeClient:
    """OpenAI-shaped: client.chat.completions.create(**kw) -> response."""

    def __init__(self, reply="", exc=None):
        self.reply, self.exc, self.calls = reply, exc, []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=9000, completion_tokens=500),
        )


FIX = """Here is the fix.

pkg/calc.py
```python
<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE
```
"""


# ── context ─────────────────────────────────────────────────────────────────

def test_context_skips_state_venv_caches_and_binaries():
    root = _project()
    ctx = build_context(str(root), "fix add", None, 24000)
    assert "SECRET" not in ctx.text
    assert not any(f.startswith((".git", ".awos", ".venv", "__pycache__")) for f in ctx.files)
    assert "pkg/blob.py" not in ctx.files
    assert "## Repository map" in ctx.text


def test_context_marks_tests_read_only_and_ranks_hits_first():
    root = _project()
    exploration = {"grep_hits": [{"file": "pkg/util.py", "line": 1, "text": "x"}],
                   "hit_files": ["pkg/util.py"]}
    ctx = build_context(str(root), "fix add in calc.py", exploration, 24000)
    assert ctx.files[0] == "pkg/util.py"          # exploration hit
    assert ctx.files[1] == "pkg/calc.py"          # named in the task
    assert ctx.files.index("tests/test_calc.py") > ctx.files.index("pkg/__init__.py")
    assert ctx.read_only == ["tests/test_calc.py"]
    assert "tests/test_calc.py  (read-only test file)" in ctx.text


def test_context_respects_budget_and_maps_the_rest():
    root = _project()
    (root / "pkg" / "big.py").write_text(
        "def big_function(x, y=2):\n" + "    x += 1\n" * 3000 + "    return x\n", encoding="utf-8")
    ctx = build_context(str(root), "fix add in calc.py", None, 1200)
    assert "pkg/calc.py" in ctx.files
    assert "pkg/big.py" not in ctx.files
    assert "def big_function(x, y=2)" in ctx.text  # signature in the map
    assert ctx.tokens <= 1200 + 50


def test_budget_env(monkeypatch):
    monkeypatch.setenv("AWOS_ONE_SHOT_BUDGET_TOKENS", "5000")
    assert one_shot.budget_tokens() == 5000
    monkeypatch.delenv("AWOS_ONE_SHOT_BUDGET_TOKENS")
    assert one_shot.budget_tokens() == 24000


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_multiple_files_new_file_and_path_reuse():
    reply = FIX + """
pkg/util.py
<<<<<<< SEARCH
    return 1
=======
    return 2
>>>>>>> REPLACE

<<<<<<< SEARCH
def helper():
=======
def helper():  # same file
>>>>>>> REPLACE

`pkg/new.py`
```python
<<<<<<< SEARCH
=======
VALUE = 3
>>>>>>> REPLACE
```
"""
    blocks, bad = parse_blocks(reply)
    assert not bad
    assert [b.path for b in blocks] == ["pkg/calc.py", "pkg/util.py", "pkg/util.py", "pkg/new.py"]
    assert blocks[0].replace == "def add(a, b):\n    return a + b"
    assert blocks[3].search == "" and blocks[3].replace == "VALUE = 3"


def test_parse_malformed_blocks_are_reported():
    blocks, bad = parse_blocks("pkg/calc.py\n<<<<<<< SEARCH\nx\n>>>>>>> REPLACE\n")
    assert blocks == [] and "divider" in bad[0]["reason"]
    blocks, bad = parse_blocks("pkg/calc.py\n<<<<<<< SEARCH\nx\n=======\ny\n")
    assert blocks == [] and "REPLACE" in bad[0]["reason"]
    assert parse_blocks("no blocks at all") == ([], [])


# ── applying ────────────────────────────────────────────────────────────────

def test_apply_exact_fuzzy_new_and_rejections():
    root = _project()
    outside = Path(tempfile.mkdtemp()) / "evil.py"
    blocks = [
        EditBlock("pkg/calc.py", "def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b"),
        # whitespace differs: the Verifier's fuzzy tier matches it
        EditBlock("pkg/calc.py", "def mul(a, b):\n  return a * b", "def mul(a, b):\n    return b * a"),
        EditBlock("pkg/new.py", "", "VALUE = 3"),
        EditBlock("../" * 12 + str(outside).lstrip("/"), "", "x = 1"),
        EditBlock(str(outside), "", "x = 1"),
        EditBlock(".awos/x.py", "SECRET = 1", "SECRET = 2"),
        EditBlock("pkg/util.py", "not in the file at all", "y"),
        EditBlock("tests/test_calc.py", "== 3", "== -1"),
    ]
    applied, failed = apply_blocks(str(root), blocks, allow_test_edits=False,
                                   read_only={"tests/test_calc.py"})
    assert applied == ["pkg/calc.py", "pkg/new.py"]
    calc = (root / "pkg" / "calc.py").read_text()
    assert "return a + b" in calc and "return b * a" in calc
    assert (root / "pkg" / "new.py").read_text() == "VALUE = 3\n"
    reasons = [f["reason"] for f in failed]
    assert sum("outside the project" in r for r in reasons) == 2
    assert any(".git/.awos" in r for r in reasons)
    assert any("read-only" in r for r in reasons)
    assert len(failed) == 5
    assert not outside.exists()
    assert (root / ".awos" / "x.py").read_text() == "SECRET = 1\n"
    assert "== 3" in (root / "tests" / "test_calc.py").read_text()


def test_run_one_shot_applies_and_counts_usage():
    root = _project()
    client = _FakeClient(FIX)
    res = run_one_shot(client, "deepseek/deepseek-v4-flash", str(root), "fix add", None)
    assert res.error is None
    assert res.applied == ["pkg/calc.py"] and res.failed == []
    assert res.input_tokens == 9000 and res.output_tokens == 500 and res.calls == 1
    assert client.calls[0]["max_tokens"] >= 16384
    assert "pkg/calc.py" in client.calls[0]["messages"][1]["content"]


def test_run_one_shot_never_raises():
    root = _project()
    res = run_one_shot(_FakeClient(exc=RuntimeError("boom")), "m", str(root), "fix add", None)
    assert res.error and "boom" in res.error and res.applied == []


# ── orchestrator hook ───────────────────────────────────────────────────────

class _Scripted(AnthropicToolClient):
    def __init__(self, replies):
        super().__init__(client=None, model="claude-test")
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system, messages, registry):
        if not self.prompts:
            self.prompts.append(messages[0]["content"])
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


def _orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch._gui_bus = None
    orch.execution_log = []
    for name in ("performance", "reward_store", "escalation", "strategy_router",
                 "obs_store", "_feature_extractor", "_update_task_node",
                 "_persist_worker_failure_pattern"):
        setattr(orch, name, MagicMock())
    orch.escalation.failure_count.return_value = 0
    return orch


GREEN = TestResult(passed=3, failed=0, errors=0, pass_rate=1.0, raw_output="3 passed",
                   no_tests_found=False)
RED = TestResult(passed=1, failed=1, errors=0, pass_rate=0.5,
                 raw_output="FAILED tests/test_calc.py::test_add - assert 4 == 3",
                 no_tests_found=False)


def _run(reply, test_results, script=None, env="1", exc=None):
    root = _project()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.side_effect = list(test_results)
    scripted = _Scripted(script or [ModelReply(text="Done.", input_tokens=100, output_tokens=20)])
    fake = _FakeClient(reply, exc=exc)
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    with patch.dict(os.environ, {"AWOS_ONE_SHOT": env}), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=fake), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        span = MagicMock()
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=span,
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage=usage,
        )
    return SimpleNamespace(out=out, root=root, ctx=ctx, scripted=scripted, fake=fake,
                           usage=usage, span=span)


def test_one_shot_success_skips_the_agent_loop(capsys):
    r = _run(FIX, [GREEN])
    assert r.out["success"] is True
    assert r.out["one_shot"] is True
    assert r.scripted.prompts == []                      # AgentLoop never called the model
    assert "return a + b" in (r.root / "pkg" / "calc.py").read_text()
    assert r.usage["input_tokens"] == 9000 and r.usage["output_tokens"] == 500
    assert r.span.attempt_count == 1                     # one turn
    r.ctx["git"].record_modified.assert_called_once()
    assert "[ONE-SHOT] solved in 1 call — 1 file(s) changed, tests 3 passed" in capsys.readouterr().out


def test_failing_tests_fall_back_with_edits_kept_and_prompt_extended(capsys):
    r = _run(FIX, [RED, GREEN])
    assert r.scripted.prompts, "AgentLoop must run"
    prompt = r.scripted.prompts[0]
    assert "one-shot attempt" in prompt and "pkg/calc.py" in prompt
    assert "assert 4 == 3" in prompt
    assert "return a + b" in (r.root / "pkg" / "calc.py").read_text()   # kept
    assert r.out["success"] is True and r.out.get("agent_loop") is True
    assert r.usage["input_tokens"] == 9000 + 100
    assert r.span.attempt_count == 2                     # one-shot turn + one loop turn
    assert "[ONE-SHOT] fell back to the agent loop: tests: 1 passed, 1 failed" in capsys.readouterr().out


def test_zero_applied_runs_the_agent_loop_normally(capsys):
    r = _run("I could not find anything to change.", [GREEN],
             script=[ModelReply(text="", tool_calls=[ToolCall(id="t", name="edit_file", arguments={
                 "path": "pkg/calc.py", "old_string": "a - b", "new_string": "a + b"})],
                 input_tokens=100, output_tokens=20),
                 ModelReply(text="Fixed.", input_tokens=100, output_tokens=20)])
    assert r.scripted.prompts and "one-shot" not in r.scripted.prompts[0]
    assert r.out["success"] is True
    assert "no edit applied" in capsys.readouterr().out


def test_model_error_falls_back_with_no_edits(capsys):
    r = _run("", [GREEN], exc=RuntimeError("503"),
             script=[ModelReply(text="", tool_calls=[ToolCall(id="t", name="edit_file", arguments={
                 "path": "pkg/calc.py", "old_string": "a - b", "new_string": "a + b"})],
                 input_tokens=100, output_tokens=20),
                 ModelReply(text="Fixed.", input_tokens=100, output_tokens=20)])
    assert r.scripted.prompts and r.out["success"] is True
    assert "model call failed" in capsys.readouterr().out


def test_off_by_default_leaves_behaviour_unchanged(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT", raising=False)
    assert one_shot.one_shot_enabled() is False
    r = _run(FIX, [GREEN], env="0")
    assert r.fake.calls == []
    assert r.scripted.prompts and "one-shot" not in r.scripted.prompts[0]
    assert "return a - b" in (r.root / "pkg" / "calc.py").read_text()
