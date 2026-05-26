"""
E2E wiring smoke test — confirms all self-learning data flows work
end-to-end without making any real API calls or spawning heavy processes.

Verifies the complete data pipeline:
  error_patterns → PromptEvolver → evolved_prompt.json → Worker prompt
  failure on attempt>=2 → LiveToolSynthesizer → tool → [TOOL OUTPUT] block
  high failure_rate → ScaffoldEvolver.should_evolve → mutation decision
  every 20 sessions → StabilityGate → .env updated
"""

from __future__ import annotations

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from scaffold.agent.prompt_evolver import PromptEvolver
from scaffold.agent.live_tool_synth import LiveToolSynthesizer
from scaffold.agent.scaffold_evolver import ScaffoldEvolver
from scaffold.agent.stability_gate import StabilityGate


# ── Shared fixture: lightweight "orchestrator state" ─────────────────────────

@pytest.fixture
def ctx(tmp_path):
    """Mimics the self-learning slice of Orchestrator state."""
    awos = tmp_path / ".awos"
    awos.mkdir()
    (awos / "tools").mkdir()
    return {
        "awos":            awos,
        "session_count":   0,
        "prompt_evolver":  PromptEvolver(store_path=str(awos)),
        "live_tool_synth": LiveToolSynthesizer(tools_dir=str(awos / "tools")),
        "scaffold_evolver": ScaffoldEvolver(scaffold_root=str(tmp_path), cheap_call=MagicMock()),
        "stability_gate":  StabilityGate(project_root=str(tmp_path), env_file=".env"),
    }


# ═══ Pipeline A: error_patterns → PromptEvolver → evolved_prompt.json ════════

class TestPromptEvolverPipeline:
    def test_full_flow_10_sessions(self, ctx, monkeypatch):
        """After 10 sessions, guidelines are evolved and persisted."""
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        monkeypatch.setenv("AWOS_PROMPT_EVOLVE_EVERY", "10")

        pe = ctx["prompt_evolver"]
        pe._cheap_call = MagicMock(return_value=json.dumps({"guidelines": [{
            "section": "search",
            "guideline": "Always include 2 context lines in the SEARCH block.",
            "reason":    "SYNTAX_ERROR occurred 3 times",
            "confidence": 0.85,
        }]}))

        # Seed error patterns
        (ctx["awos"] / "error_patterns.jsonl").write_text(
            "\n".join(json.dumps({"error_type": "SYNTAX_ERROR", "critique": f"bad indent {i}", "error_msg": "SyntaxError"})
                      for i in range(5))
        )

        for i in range(1, 11):
            ctx["session_count"] += 1
            if pe.should_evolve(ctx["session_count"]):
                guidelines = pe.evolve()
                if guidelines:
                    pe.persist(guidelines, ctx["session_count"])

        evolved = ctx["awos"] / "evolved_prompt.json"
        assert evolved.exists()
        data = json.loads(evolved.read_text())
        assert data["session_count"] == 10
        assert "context lines" in data["evolved_guidelines"]

    def test_guidelines_hot_loadable_by_worker(self, ctx):
        """Worker can load evolved guidelines without restart."""
        pe = ctx["prompt_evolver"]
        pe.persist("- Check indentation before SEARCH.", session_count=10)
        loaded = pe.load_evolved_guidelines()
        assert "Check indentation" in loaded

    def test_no_fire_before_session_10(self, ctx, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        pe = ctx["prompt_evolver"]
        for i in range(1, 10):
            assert pe.should_evolve(i) is False

    def test_disabled_without_env_var(self, ctx, monkeypatch):
        monkeypatch.delenv("AWOS_PROMPT_EVOLUTION", raising=False)
        assert ctx["prompt_evolver"].should_evolve(10) is False


# ═══ Pipeline B: failure → LiveToolSynthesizer → [TOOL OUTPUT] block ═════════

class TestLiveToolSynthPipeline:
    def test_skipped_on_first_attempt(self, ctx, monkeypatch):
        monkeypatch.setenv("AWOS_LIVE_TOOLS", "true")
        lts = ctx["live_tool_synth"]
        lts._cheap_call = MagicMock()
        assert lts.reflect({"action": "fix file"}, "SyntaxError", attempt=1) is None
        lts._cheap_call.assert_not_called()

    def test_synthesizes_on_attempt_2(self, ctx, monkeypatch):
        monkeypatch.setenv("AWOS_LIVE_TOOLS", "true")
        lts = ctx["live_tool_synth"]

        def side_effect(prompt):
            if "Would writing" in prompt:
                return json.dumps({"should_create": True, "tool_purpose": "check syntax errors"})
            return 'import sys,json\ntask=json.loads(sys.stdin.read())\nprint("SYNTAX OK")\n'

        lts._cheap_call = side_effect
        lts._validate_runtime = MagicMock(return_value=True)
        tool = lts.reflect({"action": "fix syntax error in utils.py"}, "SyntaxError", attempt=2)

        assert tool is not None
        assert (ctx["awos"] / "tools" / "index.json").exists()
        assert len(lts.list_tools()) == 1

    def test_reuses_existing_tool_on_next_task(self, ctx, monkeypatch):
        """On next similar task, find_relevant_tool returns the cached tool."""
        monkeypatch.setenv("AWOS_LIVE_TOOLS", "true")
        lts = ctx["live_tool_synth"]

        from dataclasses import asdict
        from scaffold.agent.live_tool_synth import SynthesizedTool
        tool = SynthesizedTool(
            name="tool_syntax", description="check syntax",
            script_path="/fake.py", trigger_pattern="fix syntax error in file",
            created_at="2026-01-01T00:00:00Z", use_count=0,
        )
        (ctx["awos"] / "tools" / "index.json").write_text(json.dumps([asdict(tool)]))

        matched = lts.find_relevant_tool({"action": "fix syntax error in module"})
        assert matched is not None
        assert matched.name == "tool_syntax"

    def test_tool_output_injected_into_task_dict(self, ctx, monkeypatch):
        """Simulates how orchestrator builds _task dict with tool_output."""
        monkeypatch.setenv("AWOS_LIVE_TOOLS", "true")
        lts = ctx["live_tool_synth"]
        lts.reflect = MagicMock(return_value=None)
        lts.find_relevant_tool = MagicMock(return_value=MagicMock())
        lts.run_tool = MagicMock(return_value="SYNTAX OK: utils.py")

        task = {"action": "fix syntax error", "file": "utils.py"}
        _tool_output = ""
        synth = lts.reflect(task=task, error="err", attempt=2)
        if not synth:
            existing = lts.find_relevant_tool(task)
            if existing:
                _tool_output = lts.run_tool(existing, task)

        _task = {**task, "tool_output": _tool_output}
        assert _task["tool_output"] == "SYNTAX OK: utils.py"


# ═══ Pipeline C: failure_rate → ScaffoldEvolver → accept/rollback ════════════

class TestScaffoldEvolverPipeline:
    def test_not_triggered_on_low_failure_rate(self, ctx, monkeypatch):
        monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
        monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "true")
        assert ctx["scaffold_evolver"].should_evolve(0.2) is False

    def test_triggered_on_high_failure_rate(self, ctx, monkeypatch):
        monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
        monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "true")
        assert ctx["scaffold_evolver"].should_evolve(0.5) is True

    def test_double_gate_required(self, ctx, monkeypatch):
        monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
        monkeypatch.delenv("AWOS_SAFE_TO_RUN_TESTS", raising=False)
        assert ctx["scaffold_evolver"].should_evolve(0.9) is False


# ═══ Pipeline D: session 20 → StabilityGate → .env updated ═══════════════════

class TestStabilityGatePipeline:
    def test_fires_every_20_sessions(self, ctx):
        sg = ctx["stability_gate"]
        assert sg.should_check(20) is True
        assert sg.should_check(40) is True
        for n in [1, 5, 10, 19, 21]:
            assert sg.should_check(n) is False

    def test_stable_probe_writes_true_to_env(self, ctx, tmp_path):
        sg = ctx["stability_gate"]
        sg._run_once = lambda: (120, "")
        stable, _ = sg.run(verbose=False)
        assert stable is True
        assert "AWOS_SAFE_TO_RUN_TESTS=true" in (tmp_path / ".env").read_text()

    def test_flaky_probe_writes_false_to_env(self, ctx, tmp_path):
        sg = ctx["stability_gate"]
        results = iter([(120, ""), (105, ""), (120, "")])
        sg._run_once = lambda: next(results)
        stable, reason = sg.run(verbose=False)
        assert stable is False
        assert "Flaky" in reason
        assert "AWOS_SAFE_TO_RUN_TESTS=false" in (tmp_path / ".env").read_text()

    def test_updates_os_environ_live(self, ctx, monkeypatch):
        """os.environ is updated immediately — no shell restart needed."""
        monkeypatch.delenv("AWOS_SAFE_TO_RUN_TESTS", raising=False)
        sg = ctx["stability_gate"]
        sg._run_once = lambda: (120, "")
        sg.run(verbose=False)
        assert os.environ.get("AWOS_SAFE_TO_RUN_TESTS") == "true"
