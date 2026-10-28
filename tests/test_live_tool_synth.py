"""Tests for LiveToolSynthesizer — Feature 1B (true self-learning)."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scaffold.agent.live_tool_synth import LiveToolSynthesizer, SynthesizedTool, _keyword_overlap


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_tools(tmp_path):
    d = tmp_path / ".awos" / "tools"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def synth(tmp_tools, monkeypatch):
    monkeypatch.setenv("AWOS_LIVE_TOOLS", "true")
    cheap = MagicMock(return_value='{"should_create": false, "tool_purpose": ""}')
    s = LiveToolSynthesizer(tools_dir=str(tmp_tools), cheap_call=cheap)
    return s


VALID_TOOL_CODE = '''"""Check if a file path exists."""
import sys, json
task = json.loads(sys.stdin.read())
print("ok")
'''

SYNTAX_BAD_CODE = "def broken(:\n    pass"


# ── TestKeywordOverlap ────────────────────────────────────────────────────────

class TestKeywordOverlap:
    def test_identical(self):
        assert _keyword_overlap("fix syntax error", "fix syntax error") == 1.0

    def test_no_overlap(self):
        assert _keyword_overlap("apple banana", "cat dog") == 0.0

    def test_partial_overlap(self):
        score = _keyword_overlap("fix syntax error in file", "fix error")
        assert 0 < score < 1.0

    def test_empty_strings(self):
        assert _keyword_overlap("", "something") == 0.0


# ── TestReflect ───────────────────────────────────────────────────────────────

class TestReflect:
    def test_disabled_by_default(self, tmp_tools):
        s = LiveToolSynthesizer(tools_dir=str(tmp_tools), cheap_call=MagicMock())
        result = s.reflect({"action": "fix something"}, "error", attempt=2)
        assert result is None

    def test_no_cheap_call(self, synth):
        synth._cheap_call = None
        result = synth.reflect({"action": "fix"}, "error", attempt=2)
        assert result is None

    def test_attempt_one_skipped(self, synth):
        result = synth.reflect({"action": "fix something"}, "error", attempt=1)
        assert result is None

    def test_should_not_create_returns_none(self, synth):
        synth._cheap_call.return_value = '{"should_create": false, "tool_purpose": ""}'
        result = synth.reflect({"action": "fix"}, "error", attempt=2)
        assert result is None

    def test_reflect_invalid_json_returns_none(self, synth):
        synth._cheap_call.return_value = "NOT JSON"
        result = synth.reflect({"action": "fix"}, "error", attempt=2)
        assert result is None

    def test_reflect_synthesizes_tool_on_should_create(self, synth):
        def side_effect(prompt):
            if "Would writing" in prompt:
                return '{"should_create": true, "tool_purpose": "check if file exists"}'
            return VALID_TOOL_CODE

        synth._cheap_call.side_effect = side_effect
        synth._validate_runtime = MagicMock(return_value=True)  # no subprocess spawn
        result = synth.reflect({"action": "read file path"}, "FileNotFoundError", attempt=2)
        assert result is not None
        assert isinstance(result, SynthesizedTool)
        assert Path(result.script_path).exists()

    def test_reflect_syntax_error_in_code_returns_none(self, synth):
        def side_effect(prompt):
            if "Would writing" in prompt:
                return '{"should_create": true, "tool_purpose": "check file"}'
            return SYNTAX_BAD_CODE

        synth._cheap_call.side_effect = side_effect
        synth._validate_runtime = MagicMock(return_value=True)  # no subprocess spawn
        result = synth.reflect({"action": "edit file"}, "error", attempt=2)
        assert result is None


# ── TestFindRelevantTool ──────────────────────────────────────────────────────

class TestFindRelevantTool:
    def _register(self, synth, name, trigger):
        from dataclasses import asdict
        tool = SynthesizedTool(
            name=name, description="desc", script_path="/fake/path.py",
            trigger_pattern=trigger, created_at="2026-01-01T00:00:00Z", use_count=0
        )
        tools = synth.list_tools()
        tools.append(tool)
        synth._tools_dir.mkdir(parents=True, exist_ok=True)
        synth._index_path.write_text(json.dumps([asdict(t) for t in tools], indent=2))
        return tool

    def test_no_tools_returns_none(self, synth):
        assert synth.find_relevant_tool({"action": "fix syntax error"}) is None

    def test_matching_tool_returned(self, synth):
        self._register(synth, "tool_a", "fix syntax error in python file")
        result = synth.find_relevant_tool({"action": "fix syntax error in module"})
        assert result is not None
        assert result.name == "tool_a"

    def test_non_matching_tool_returns_none(self, synth):
        self._register(synth, "tool_a", "database connection retry logic")
        result = synth.find_relevant_tool({"action": "rename variable x to y"})
        assert result is None

    def test_best_match_returned(self, synth):
        self._register(synth, "tool_a", "read file content validation check")
        self._register(synth, "tool_b", "syntax error detection file parsing")
        result = synth.find_relevant_tool({"action": "syntax error in file parser"})
        assert result is not None
        assert result.name == "tool_b"


# ── TestRunTool ───────────────────────────────────────────────────────────────

class TestRunTool:
    def test_runs_valid_script(self, synth, tmp_tools):
        script = tmp_tools / "hello.py"
        script.write_text('import sys, json\ntask = json.loads(sys.stdin.read())\nprint("hello")\n')
        tool = SynthesizedTool(
            name="hello", description="greet", script_path=str(script),
            trigger_pattern="say hello", created_at="2026-01-01T00:00:00Z", use_count=0,
        )
        output = synth.run_tool(tool, {"action": "test"})
        assert output == "hello"

    def test_missing_script_returns_empty(self, synth):
        tool = SynthesizedTool(
            name="ghost", description="", script_path="/nonexistent/tool.py",
            trigger_pattern="ghost", created_at="2026-01-01T00:00:00Z", use_count=0,
        )
        assert synth.run_tool(tool, {}) == ""

    def test_output_capped(self, synth, tmp_tools):
        script = tmp_tools / "big.py"
        script.write_text('import sys\nprint("x" * 2000)\n')
        tool = SynthesizedTool(
            name="big", description="", script_path=str(script),
            trigger_pattern="big output", created_at="2026-01-01T00:00:00Z", use_count=0,
        )
        output = synth.run_tool(tool, {})
        assert len(output) <= 1000


# ── TestListTools ─────────────────────────────────────────────────────────────

class TestListTools:
    def test_empty_when_no_index(self, synth):
        assert synth.list_tools() == []

    def test_returns_persisted_tools(self, synth, tmp_tools):
        from dataclasses import asdict
        t = SynthesizedTool(
            name="t1", description="d", script_path="/p.py",
            trigger_pattern="tp", created_at="2026-01-01T00:00:00Z", use_count=0,
        )
        (tmp_tools / "index.json").write_text(json.dumps([asdict(t)]))
        tools = synth.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "t1"


# ── TestValidation ────────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_syntax(self, synth):
        assert synth._validate_syntax(VALID_TOOL_CODE) is True

    def test_invalid_syntax(self, synth):
        assert synth._validate_syntax(SYNTAX_BAD_CODE) is False

    def test_strip_fences(self, synth):
        code = "```python\nprint('hi')\n```"
        result = synth._strip_fences(code)
        assert result == "print('hi')"

    def test_strip_fences_no_fences(self, synth):
        code = "print('hi')"
        assert synth._strip_fences(code) == "print('hi')"
