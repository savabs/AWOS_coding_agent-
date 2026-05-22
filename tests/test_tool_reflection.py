"""
Tests for Tool Reflection / Success Matrix (Phase 8).

Covers Steps 1–8 of the spec:
  1. JSONL roundtrip + migration
  2. _classify_task_type() helper
  3. matrix_display() formatting
  4–5. Orchestrator enrichment + panel (mocked)
  6. EscalationEngine cold-start soft hint
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.performance_tracker import (
    ToolPerformanceTracker,
    _BUG_FIX_KW,
    _REFACTOR_KW,
    _NEW_FEAT_KW,
    _ARCH_KW,
    _TEST_KW,
)


# ── Step 1: JSONL persistence ────────────────────────────────────────────────


class TestJSONLPersistence:
    def test_jsonl_roundtrip(self, tmp_path):
        """Record 3 entries, reload tracker, assert all 3 present."""
        t1 = ToolPerformanceTracker(persist_dir=tmp_path)
        for i in range(3):
            t1.record(tool="Worker", model="m" + str(i), task_type="t", success=True)
        # New instance reads the same file
        t2 = ToolPerformanceTracker(persist_dir=tmp_path)
        assert len(t2._records) == 3
        assert t2._records[0]["model"] == "m0"
        assert t2._records[2]["model"] == "m2"

    def test_jsonl_migration_from_old_array_format(self, tmp_path):
        """Old JSON array file is detected, migrated to JSONL, and readable."""
        ledger = tmp_path / "performance.json"
        old_data = [
            {"timestamp": 1.0, "tool": "A", "model": "m1", "task_type": "t", "success": True},
            {"timestamp": 2.0, "tool": "B", "model": "m2", "task_type": "t", "success": False},
        ]
        ledger.write_text(json.dumps(old_data), encoding="utf-8")

        t = ToolPerformanceTracker(persist_dir=tmp_path)
        assert len(t._records) == 2
        assert t._records[1]["model"] == "m2"

        # File should now be JSONL
        lines = ledger.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["model"] == "m1"

    def test_jsonl_skips_malformed_lines(self, tmp_path):
        """A corrupted line is skipped; valid lines are loaded."""
        ledger = tmp_path / "performance.json"
        ledger.write_text(
            '{"model": "good"}\nNOT_JSON\n{"model": "also_good"}\n',
            encoding="utf-8",
        )
        t = ToolPerformanceTracker(persist_dir=tmp_path)
        assert len(t._records) == 2
        assert t._records[0]["model"] == "good"
        assert t._records[1]["model"] == "also_good"


# ── Step 2: Task type classification ─────────────────────────────────────────


class TestClassifyTaskType:
    def test_bug_fix(self):
        assert ToolPerformanceTracker._classify_task_type("fix the crash in login") == "bug_fix"
        assert ToolPerformanceTracker._classify_task_type("repair broken auth") == "bug_fix"

    def test_new_feature(self):
        assert ToolPerformanceTracker._classify_task_type("add email verification") == "new_feature"
        assert ToolPerformanceTracker._classify_task_type("implement user signup") == "new_feature"

    def test_general_fallback(self):
        assert ToolPerformanceTracker._classify_task_type("update README") == "general"
        assert ToolPerformanceTracker._classify_task_type("") == "general"

    def test_refactor(self):
        assert ToolPerformanceTracker._classify_task_type("refactor the service layer") == "refactor"

    def test_test_priority(self):
        # "test" should classify as "test" even though "add" is in _NEW_FEAT_KW
        assert ToolPerformanceTracker._classify_task_type("add unit tests for auth") == "test"


# ── Step 3: matrix_display + success_matrix ──────────────────────────────────


class TestMatrixDisplay:
    def _tracker(self, tmp_path):
        return ToolPerformanceTracker(persist_dir=tmp_path)

    def test_success_matrix_shape(self, tmp_path):
        t = self._tracker(tmp_path)
        t.record(tool="Worker", model="deepseek", task_type="bug_fix", success=True)
        t.record(tool="Worker", model="deepseek", task_type="bug_fix", success=False)
        t.record(tool="Worker", model="sonnet", task_type="bug_fix", success=True)
        t.record(tool="Worker", model="sonnet", task_type="architecture", success=True)
        t.record(tool="Worker", model="sonnet", task_type="architecture", success=True)
        matrix = t.success_matrix(min_count=1)
        assert "deepseek" in matrix
        assert "sonnet" in matrix
        assert "bug_fix" in matrix["deepseek"]
        assert "architecture" in matrix["sonnet"]

    def test_success_matrix_min_count_filter(self, tmp_path):
        t = self._tracker(tmp_path)
        t.record(tool="Worker", model="a", task_type="t", success=True)
        t.record(tool="Worker", model="a", task_type="t", success=True)
        t.record(tool="Worker", model="b", task_type="t", success=True)
        t.record(tool="Worker", model="b", task_type="t", success=True)
        t.record(tool="Worker", model="b", task_type="t", success=True)
        matrix = t.success_matrix(min_count=3)
        assert "a" not in matrix
        assert "b" in matrix

    def test_matrix_display_none_on_empty(self, tmp_path):
        t = self._tracker(tmp_path)
        assert t.matrix_display() is None

    def test_matrix_display_contains_model_name(self, tmp_path):
        t = self._tracker(tmp_path)
        for _ in range(5):
            t.record(tool="Worker", model="deepseek", task_type="bug_fix", success=True, latency_ms=100.0)
        display = t.matrix_display()
        assert display is not None
        assert "deepseek" in display
        assert "bug_fix" in display
        assert "100%" in display

    def test_best_model_no_data(self, tmp_path):
        t = self._tracker(tmp_path)
        best, rate = t.best_model_for("bug_fix", ["a", "b"])
        assert best is None
        assert rate is None

    def test_best_model_returns_highest(self, tmp_path):
        t = self._tracker(tmp_path)
        for _ in range(3):
            t.record(tool="Worker", model="model_a", task_type="t", success=True)
        for _ in range(2):
            t.record(tool="Worker", model="model_a", task_type="t", success=False)
        for _ in range(5):
            t.record(tool="Worker", model="model_b", task_type="t", success=True)
        best, rate = t.best_model_for("t", ["model_a", "model_b"], min_count=1)
        assert best == "model_b"
        assert rate == pytest.approx(1.0)

    def test_recommend_model_multiple_candidates(self, tmp_path):
        t = self._tracker(tmp_path)
        for _ in range(3):
            t.record(tool="Worker", model="bad", task_type="t", success=False)
        for _ in range(5):
            t.record(tool="Worker", model="good", task_type="t", success=True)
        for _ in range(4):
            t.record(tool="Worker", model="medium", task_type="t", success=True)
        rec = t.recommend_model("t", ["bad", "good", "medium"])
        assert rec == "good"


# ── Step 4: Orchestrator enrichment (mocked) ───────────────────────────────────


class TestOrchestratorEnrichment:
    def test_record_has_latency_and_cost(self, tmp_path):
        """Simulate what orchestrator does: record with latency_ms and cost."""
        t = ToolPerformanceTracker(persist_dir=tmp_path)
        t.record(
            tool="Worker",
            model="deepseek",
            task_type=t._classify_task_type("fix the bug"),
            success=True,
            latency_ms=850.0,
            cost=0.001,
        )
        stats = t.get_stats(model="deepseek", task_type="bug_fix")
        assert stats["avg_latency_ms"] == pytest.approx(850.0)
        assert stats["avg_cost"] == pytest.approx(0.001)
        assert stats["success_rate"] == pytest.approx(1.0)


# ── Step 6: EscalationEngine cold-start soft hint ────────────────────────────


class TestEscalationSoftHint:
    def _engine(self, tracker=None, ml_router=None):
        from scaffold.agent.escalation_engine import EscalationEngine
        return EscalationEngine(
            monthly_budget=20.0,
            performance_tracker=tracker,
            ml_router=ml_router,
        )

    def test_cold_start_soft_hint_fires(self, tmp_path):
        """When LinUCB is cold and performance tracker has data, use recommend_model."""
        from scaffold.agent.escalation_engine import EscalationLevel
        from scaffold.agent.ml_router import LinUCBRouter

        tracker = ToolPerformanceTracker(persist_dir=tmp_path)
        # Sonnet has 100% success on "architecture" tasks
        for _ in range(5):
            tracker.record(tool="Worker", model="Claude Sonnet 4.6",
                           task_type="architecture", success=True)
        # DeepSeek has 0% success on "architecture" tasks
        for _ in range(5):
            tracker.record(tool="Worker", model="DeepSeek V4 Flash",
                           task_type="architecture", success=False)

        # Cold-start LinUCB (0 updates, min_samples=20)
        ml_router = LinUCBRouter(min_samples=20)
        assert not ml_router.is_ready()

        engine = self._engine(tracker=tracker, ml_router=ml_router)
        task = {"action": "design the api architecture", "file": "api.py", "complexity": "high"}
        decision = engine.decide(task, failure_count=0, budget_remaining=20.0)

        assert "Performance hint" in decision.reason
        assert decision.spec.level == EscalationLevel.SONNET

    def test_warm_linucb_ignores_soft_hint(self, tmp_path):
        """When LinUCB is ready, the soft hint is bypassed."""
        from scaffold.agent.ml_router import LinUCBRouter
        import numpy as np

        tracker = ToolPerformanceTracker(persist_dir=tmp_path)
        for _ in range(5):
            tracker.record(tool="Worker", model="Claude Sonnet 4.6",
                           task_type="architecture", success=True)

        # Warm LinUCB: inject enough updates to be ready
        ml_router = LinUCBRouter(min_samples=5)
        features = np.zeros(10)
        features[-1] = 1.0  # bias
        for i in range(5):
            ml_router.update(features, action_id=1, reward=1.0)
        assert ml_router.is_ready()

        engine = self._engine(tracker=tracker, ml_router=ml_router)
        task = {"action": "design the api architecture", "file": "api.py", "complexity": "high"}
        decision = engine.decide(task, failure_count=0, budget_remaining=20.0)

        # LinUCB takes over — reason should mention LinUCB, not Performance hint
        assert "LinUCB" in decision.reason

    def test_soft_hint_respects_budget(self, tmp_path):
        """If recommended model is too expensive for remaining budget, fall through."""
        from scaffold.agent.ml_router import LinUCBRouter

        tracker = ToolPerformanceTracker(persist_dir=tmp_path)
        for _ in range(5):
            tracker.record(tool="Worker", model="Claude Sonnet 4.6",
                           task_type="architecture", success=True)

        ml_router = LinUCBRouter(min_samples=20)
        engine = self._engine(tracker=tracker, ml_router=ml_router)
        task = {"action": "design the api architecture", "file": "api.py", "complexity": "high"}
        # Budget too low for Sonnet (needs $3.0)
        decision = engine.decide(task, failure_count=0, budget_remaining=1.0)

        assert "Performance hint" not in decision.reason

    def test_no_hint_when_no_performance_tracker(self):
        from scaffold.agent.ml_router import LinUCBRouter
        ml_router = LinUCBRouter(min_samples=20)
        engine = self._engine(tracker=None, ml_router=ml_router)
        task = {"action": "fix the bug", "file": "x.py", "complexity": "low"}
        decision = engine.decide(task, failure_count=0, budget_remaining=20.0)
        assert "Performance hint" not in decision.reason

    def test_hard_veto_still_works_with_semantic_task_type(self, tmp_path):
        """Existing veto uses _classify_task_type; verify it still fires."""
        from scaffold.agent.escalation_engine import EscalationLevel
        from scaffold.agent.ml_router import LinUCBRouter

        tracker = ToolPerformanceTracker(persist_dir=tmp_path)
        # DeepSeek failing on "new_feature" tasks (action "add feature" → new_feature)
        for _ in range(8):
            tracker.record(tool="Worker", model="DeepSeek V4 Flash",
                           task_type="new_feature", success=False)
        for _ in range(2):
            tracker.record(tool="Worker", model="DeepSeek V4 Flash",
                           task_type="new_feature", success=True)
        # Sonnet succeeding on "new_feature" tasks
        for _ in range(9):
            tracker.record(tool="Worker", model="Claude Sonnet 4.6",
                           task_type="new_feature", success=True)
        for _ in range(1):
            tracker.record(tool="Worker", model="Claude Sonnet 4.6",
                           task_type="new_feature", success=False)

        ml_router = LinUCBRouter(min_samples=20)
        engine = self._engine(tracker=tracker, ml_router=ml_router)
        task = {"action": "add new feature to auth", "file": "auth.py", "complexity": "high"}
        decision = engine.decide(task, failure_count=0, budget_remaining=20.0)

        assert "Performance veto" in decision.reason or decision.spec.level.value >= EscalationLevel.SONNET.value


# ── Existing veto tests (updated for semantic task types) ─────────────────────


class TestPerformanceVeto:
    def _engine(self, tracker=None):
        from scaffold.agent.escalation_engine import EscalationEngine
        return EscalationEngine(monthly_budget=20.0, performance_tracker=tracker)

    def _task(self, complexity="low"):
        return {"task_id": "t1", "action": "add feature", "file": "x.py",
                "complexity": complexity}

    def test_no_veto_without_tracker(self):
        engine = self._engine(tracker=None)
        decision = engine.decide(self._task(), failure_count=0)
        assert decision.spec is not None

    def test_no_veto_insufficient_data(self, tmp_path):
        tracker = ToolPerformanceTracker(persist_dir=tmp_path)
        # Only 3 records — below min_count=10 threshold
        for _ in range(3):
            tracker.record(tool="Worker", model="DeepSeek V4 Flash",
                           task_type="new_feature", success=False)
        engine = self._engine(tracker=tracker)
        decision = engine.decide(self._task("low"), failure_count=0)
        assert "Performance veto" not in decision.reason

    def test_forced_level_overrides_veto(self, tmp_path):
        from scaffold.agent.escalation_engine import EscalationLevel
        tracker = ToolPerformanceTracker(persist_dir=tmp_path)
        engine = self._engine(tracker=tracker)
        decision = engine.decide(self._task(), force_level=EscalationLevel.DEEPSEEK)
        assert decision.forced is True
        assert decision.spec.level == EscalationLevel.DEEPSEEK
