"""
Tests for SelfLearningMetrics — reads .awos/ artifacts and produces reports.

Tests verify:
  - Empty store → zeroed snapshot (no crashes)
  - PromptEvolver data read correctly
  - LiveToolSynthesizer data read correctly
  - ScaffoldEvolver data read correctly
  - Error pattern data read correctly
  - print_report() runs without error on real + empty stores
  - snapshot().to_dict() produces expected keys
  - JSON CLI flag path
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from self_learning_metrics import SelfLearningMetrics, SelfLearningSnapshot


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """Return a fresh .awos/ directory inside tmp_path."""
    s = tmp_path / ".awos"
    s.mkdir()
    return s


@pytest.fixture
def metrics(store):
    return SelfLearningMetrics(store_path=str(store))


# ── empty store ───────────────────────────────────────────────────────────────

class TestEmptyStore:
    def test_snapshot_returns_zeroed(self, metrics):
        snap = metrics.snapshot()
        assert snap.guidelines_version == 0
        assert snap.guidelines_lines == 0
        assert snap.tools_synthesized == 0
        assert snap.tools_total_reuses == 0
        assert snap.mutations_attempted == 0
        assert snap.error_patterns_recorded == 0

    def test_print_report_does_not_crash(self, metrics, capsys):
        metrics.print_report()
        out = capsys.readouterr().out
        assert "Self-Learning" in out

    def test_to_dict_has_all_keys(self, metrics):
        d = metrics.snapshot().to_dict()
        assert "prompt_evolver" in d
        assert "live_tools" in d
        assert "scaffold_evolver" in d
        assert "error_patterns" in d


# ── PromptEvolver data ─────────────────────────────────────────────────────────

class TestPromptEvolverCollection:
    def test_reads_guidelines(self, store, metrics):
        (store / "evolved_prompt.json").write_text(json.dumps({
            "evolved_guidelines": "- use exact search\n- verify before replace",
            "evolved_at": "2026-05-28T07:00:00Z",
            "session_count": 5,
            "changes_applied": 2,
        }))
        snap = metrics.snapshot()
        assert snap.guidelines_version == 5
        assert snap.guidelines_evolved_at == "2026-05-28T07:00:00Z"
        assert snap.guidelines_lines == 2
        assert "exact search" in snap.guidelines_preview

    def test_handles_corrupt_file(self, store, metrics):
        (store / "evolved_prompt.json").write_text("not json{{{")
        snap = metrics.snapshot()
        assert snap.guidelines_lines == 0  # graceful fallback

    def test_empty_guidelines_field(self, store, metrics):
        (store / "evolved_prompt.json").write_text(json.dumps({
            "evolved_guidelines": "",
            "session_count": 3,
        }))
        snap = metrics.snapshot()
        assert snap.guidelines_lines == 0


# ── LiveToolSynthesizer data ──────────────────────────────────────────────────

class TestLiveToolsCollection:
    def _write_index(self, store, tools):
        (store / "tools").mkdir(exist_ok=True)
        (store / "tools" / "index.json").write_text(json.dumps(tools))

    def test_reads_tool_count_and_reuses(self, store, metrics):
        self._write_index(store, [
            {"name": "tool_a", "description": "A", "script_path": "/x", "trigger_pattern": "t", "created_at": "Z", "use_count": 5},
            {"name": "tool_b", "description": "B", "script_path": "/y", "trigger_pattern": "t", "created_at": "Z", "use_count": 2},
        ])
        snap = metrics.snapshot()
        assert snap.tools_synthesized == 2
        assert snap.tools_total_reuses == 7

    def test_top_tools_sorted_by_reuse(self, store, metrics):
        self._write_index(store, [
            {"name": "low", "description": "", "script_path": "", "trigger_pattern": "", "created_at": "", "use_count": 1},
            {"name": "high", "description": "", "script_path": "", "trigger_pattern": "", "created_at": "", "use_count": 10},
        ])
        snap = metrics.snapshot()
        assert snap.top_tools[0]["name"] == "high"

    def test_no_tools_file(self, metrics):
        snap = metrics.snapshot()
        assert snap.tools_synthesized == 0
        assert snap.top_tools == []


# ── ScaffoldEvolver data ───────────────────────────────────────────────────────

class TestScaffoldEvolverCollection:
    def _write_mutations(self, store, mutations):
        f = store / "scaffold_mutations.jsonl"
        f.write_text("\n".join(json.dumps(m) for m in mutations) + "\n")

    def test_counts_accepted_rejected(self, store, metrics):
        self._write_mutations(store, [
            {"accepted": True,  "target_file": "scaffold/agent/worker.py", "description": "fix", "error_type": "PATCH", "tests_before": 100, "tests_after": 102, "timestamp": "Z", "rejection_reason": ""},
            {"accepted": False, "target_file": "scaffold/agent/worker.py", "description": "", "error_type": "PATCH",  "tests_before": 100, "tests_after": 98,  "timestamp": "Z", "rejection_reason": "regressed"},
            {"accepted": True,  "target_file": "scaffold/agent/planner.py", "description": "fix2", "error_type": "PARSE", "tests_before": 100, "tests_after": 101, "timestamp": "Z2", "rejection_reason": ""},
        ])
        snap = metrics.snapshot()
        assert snap.mutations_attempted == 3
        assert snap.mutations_accepted == 2
        assert round(snap.acceptance_rate, 2) == 0.67

    def test_files_mutated_deduped(self, store, metrics):
        self._write_mutations(store, [
            {"accepted": True, "target_file": "scaffold/agent/worker.py", "description": "", "error_type": "", "tests_before": 0, "tests_after": 0, "timestamp": "Z", "rejection_reason": ""},
            {"accepted": True, "target_file": "scaffold/agent/worker.py", "description": "", "error_type": "", "tests_before": 0, "tests_after": 0, "timestamp": "Z", "rejection_reason": ""},
        ])
        snap = metrics.snapshot()
        assert len(snap.files_mutated) == 1

    def test_zero_attempted_zero_rate(self, metrics):
        snap = metrics.snapshot()
        assert snap.acceptance_rate == 0.0


# ── Error patterns ────────────────────────────────────────────────────────────

class TestErrorPatternCollection:
    def test_counts_and_top_types(self, store, metrics):
        lines = [
            json.dumps({"error_type": "PATCH_MISMATCH", "error_msg": ""}),
            json.dumps({"error_type": "PATCH_MISMATCH", "error_msg": ""}),
            json.dumps({"error_type": "PARSE_FAIL",     "error_msg": ""}),
        ]
        (store / "error_patterns.jsonl").write_text("\n".join(lines))
        snap = metrics.snapshot()
        assert snap.error_patterns_recorded == 3
        assert snap.top_error_types[0]["type"] == "PATCH_MISMATCH"
        assert snap.top_error_types[0]["count"] == 2

    def test_skips_blank_lines(self, store, metrics):
        (store / "error_patterns.jsonl").write_text('\n\n{"error_type": "X"}\n\n')
        snap = metrics.snapshot()
        assert snap.error_patterns_recorded == 1


# ── print_report with real data ────────────────────────────────────────────────

class TestPrintReport:
    def test_output_contains_section_headers(self, store, metrics, capsys):
        (store / "evolved_prompt.json").write_text(json.dumps({
            "evolved_guidelines": "- be precise",
            "evolved_at": "2026-05-28T00:00:00Z",
            "session_count": 10,
        }))
        metrics.print_report()
        out = capsys.readouterr().out
        assert "PromptEvolver" in out
        assert "LiveToolSynthesizer" in out
        assert "ScaffoldEvolver" in out
        assert "Error Patterns" in out

    def test_json_snapshot_roundtrip(self, metrics):
        d = metrics.snapshot().to_dict()
        assert isinstance(d["prompt_evolver"]["version"], int)
        assert isinstance(d["live_tools"]["synthesized"], int)
        assert isinstance(d["scaffold_evolver"]["acceptance_rate"], float)
        assert isinstance(d["error_patterns"]["total"], int)
