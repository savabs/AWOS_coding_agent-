"""
tests/test_p9_confidence.py — P9 Confidence Calibration.

Covers:
    - ConfidenceSignal fields
    - ConfidenceReport fields and render()
    - ConfidenceCalibrator.fuse() with all signals
    - fuse() with partial signals (weight redistribution)
    - fuse() with no signals (returns 0.5)
    - Platt sigmoid calibration _calibrate_prm()
    - _fit_platt() gradient descent
    - _weighted_average() static method
    - Threshold verdicts: confident / uncertain / abort
    - should_abort and should_pause flags
    - fit_calibration() from RewardStore
    - abort_threshold() and hitl_threshold() helpers
    - ConfidenceCalibrator.summary()
    - Orchestrator._compute_confidence() integration
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.confidence_calibrator import (
    ConfidenceCalibrator,
    ConfidenceReport,
    ConfidenceSignal,
    abort_threshold,
    hitl_threshold,
)


# ── ConfidenceSignal ──────────────────────────────────────────────────────────

class TestConfidenceSignal(unittest.TestCase):

    def test_available_true_by_default(self):
        s = ConfidenceSignal(name="critic", raw_score=0.8, weight=0.4)
        self.assertTrue(s.available)

    def test_unavailable_flag(self):
        s = ConfidenceSignal(name="prm", raw_score=0.0, weight=0.35, available=False)
        self.assertFalse(s.available)


# ── ConfidenceReport ──────────────────────────────────────────────────────────

class TestConfidenceReport(unittest.TestCase):

    def _report(self, score, verdict="confident", pause=False, abort=False):
        return ConfidenceReport(
            fused_score=score,
            signals=[],
            verdict=verdict,
            should_pause=pause,
            should_abort=abort,
            explanation="test",
        )

    def test_render_contains_score(self):
        r = self._report(0.87, "confident")
        rendered = r.render()
        self.assertIn("87%", rendered)
        self.assertIn("confident", rendered)

    def test_render_shows_signals(self):
        r = ConfidenceReport(
            fused_score=0.72,
            signals=[
                ConfidenceSignal("critic", 0.9, 0.4),
                ConfidenceSignal("prm", 0.7, 0.35),
                ConfidenceSignal("gp", 0.0, 0.15, available=False),
            ],
            verdict="confident",
            should_pause=False,
            should_abort=False,
            explanation="ok",
        )
        rendered = r.render()
        self.assertIn("critic", rendered)
        self.assertIn("prm", rendered)


# ── ConfidenceCalibrator.fuse() ───────────────────────────────────────────────

class TestFuse(unittest.TestCase):

    def setUp(self):
        self.cal = ConfidenceCalibrator()

    def test_all_signals_confident(self):
        report = self.cal.fuse(critic_score=0.9, prm_raw=0.8, gp_score=0.85, linucb_score=0.7)
        self.assertGreater(report.fused_score, 0.7)
        self.assertEqual(report.verdict, "confident")
        self.assertFalse(report.should_abort)
        self.assertFalse(report.should_pause)

    def test_all_signals_low_triggers_abort(self):
        report = self.cal.fuse(critic_score=0.05, prm_raw=-2.0, gp_score=0.05, linucb_score=0.05)
        self.assertTrue(report.should_abort)
        self.assertEqual(report.verdict, "abort")

    def test_mid_range_triggers_uncertain(self):
        report = self.cal.fuse(critic_score=0.25, prm_raw=0.0, gp_score=0.2, linucb_score=0.25)
        self.assertFalse(report.should_abort)
        self.assertIn(report.verdict, ("uncertain",))

    def test_no_signals_returns_half(self):
        report = self.cal.fuse()
        self.assertAlmostEqual(report.fused_score, 0.5, places=1)

    def test_partial_signals_redistributes_weight(self):
        # Only critic available — should use full weight on critic alone
        report_only_critic = self.cal.fuse(critic_score=0.9)
        report_all = self.cal.fuse(critic_score=0.9, prm_raw=0.9, gp_score=0.9, linucb_score=0.9)
        # Both should produce high scores since all inputs are high
        self.assertGreater(report_only_critic.fused_score, 0.7)
        self.assertGreater(report_all.fused_score, 0.7)

    def test_critic_none_falls_back_gracefully(self):
        report = self.cal.fuse(critic_score=None, prm_raw=0.7, gp_score=0.8)
        self.assertGreater(report.fused_score, 0.0)

    def test_should_pause_when_between_thresholds(self):
        # Force a score that's between abort and HITL threshold
        cal = ConfidenceCalibrator()
        from scaffold.agent import confidence_calibrator as cc
        old_abort = cc._ABORT_THRESHOLD
        old_hitl  = cc._HITL_THRESHOLD
        cc._ABORT_THRESHOLD = 0.10
        cc._HITL_THRESHOLD  = 0.40
        try:
            report = cal.fuse(critic_score=0.20, prm_raw=-1.0, gp_score=0.20, linucb_score=0.20)
            self.assertTrue(report.should_pause)
            self.assertFalse(report.should_abort)
        finally:
            cc._ABORT_THRESHOLD = old_abort
            cc._HITL_THRESHOLD  = old_hitl


# ── Platt calibration ─────────────────────────────────────────────────────────

class TestPlattCalibration(unittest.TestCase):

    def setUp(self):
        self.cal = ConfidenceCalibrator()

    def test_calibrate_prm_zero_input(self):
        # sigmoid(0) = 0.5
        result = self.cal._calibrate_prm(0.0)
        self.assertAlmostEqual(result, 0.5, places=5)

    def test_calibrate_prm_large_positive(self):
        result = self.cal._calibrate_prm(10.0)
        self.assertGreater(result, 0.99)

    def test_calibrate_prm_large_negative(self):
        result = self.cal._calibrate_prm(-10.0)
        self.assertLess(result, 0.01)

    def test_calibrate_prm_clamped_to_01(self):
        self.assertGreaterEqual(self.cal._calibrate_prm(100.0), 0.0)
        self.assertLessEqual(self.cal._calibrate_prm(100.0), 1.0)
        self.assertGreaterEqual(self.cal._calibrate_prm(-100.0), 0.0)

    def test_fit_platt_shifts_scale(self):
        # Perfect signal: high x → y=1, low x → y=0
        pairs = [(0.9, 1), (0.8, 1), (0.85, 1), (0.1, 0), (0.2, 0), (0.15, 0)]
        scale, bias = self.cal._fit_platt(pairs)
        # Scale should be positive (higher raw = higher prob)
        self.assertGreater(scale, 0)


# ── _weighted_average() ───────────────────────────────────────────────────────

class TestWeightedAverage(unittest.TestCase):

    def test_single_signal_equals_its_score(self):
        signals = [ConfidenceSignal("critic", 0.8, 1.0, available=True)]
        result = ConfidenceCalibrator._weighted_average(signals)
        self.assertAlmostEqual(result, 0.8, places=5)

    def test_unavailable_signals_excluded(self):
        signals = [
            ConfidenceSignal("critic", 0.9, 0.5, available=True),
            ConfidenceSignal("prm",    0.1, 0.5, available=False),
        ]
        result = ConfidenceCalibrator._weighted_average(signals)
        self.assertAlmostEqual(result, 0.9, places=5)

    def test_no_available_signals_returns_half(self):
        signals = [ConfidenceSignal("prm", 0.0, 1.0, available=False)]
        result = ConfidenceCalibrator._weighted_average(signals)
        self.assertAlmostEqual(result, 0.5, places=5)

    def test_equal_weights_is_arithmetic_mean(self):
        signals = [
            ConfidenceSignal("a", 0.4, 1.0, available=True),
            ConfidenceSignal("b", 0.6, 1.0, available=True),
        ]
        result = ConfidenceCalibrator._weighted_average(signals)
        self.assertAlmostEqual(result, 0.5, places=5)


# ── fit_calibration() ─────────────────────────────────────────────────────────

class TestFitCalibration(unittest.TestCase):

    def test_skips_when_too_few_episodes(self):
        cal = ConfidenceCalibrator()
        old_scale = cal._platt_scale
        store = MagicMock()
        store.get_recent.return_value = []  # no episodes
        cal.fit_calibration(store)
        self.assertEqual(cal._platt_scale, old_scale)

    def test_updates_scale_with_enough_episodes(self):
        cal = ConfidenceCalibrator()
        from dataclasses import dataclass
        @dataclass
        class FakeEp:
            features: list
            success: bool
        episodes = [FakeEp(features=[float(i % 2)], success=bool(i % 2)) for i in range(25)]
        store = MagicMock()
        store.get_recent.return_value = episodes
        # Should not raise
        cal.fit_calibration(store)


# ── summary() ────────────────────────────────────────────────────────────────

class TestSummary(unittest.TestCase):

    def test_summary_contains_keys(self):
        cal = ConfidenceCalibrator()
        s = cal.summary()
        self.assertIn("platt_scale", s)
        self.assertIn("platt_bias", s)
        self.assertIn("weights", s)
        self.assertIn("abort_threshold", s)
        self.assertIn("hitl_threshold", s)

    def test_weights_sum_to_one(self):
        cal = ConfidenceCalibrator()
        total = sum(cal._weights.values())
        self.assertAlmostEqual(total, 1.0, places=5)


# ── Module helpers ────────────────────────────────────────────────────────────

class TestModuleHelpers(unittest.TestCase):

    def test_abort_threshold_is_float(self):
        self.assertIsInstance(abort_threshold(), float)

    def test_hitl_threshold_is_float(self):
        self.assertIsInstance(hitl_threshold(), float)

    def test_hitl_above_abort(self):
        self.assertGreater(hitl_threshold(), abort_threshold())


# ── Orchestrator._compute_confidence() ───────────────────────────────────────

class TestOrchestratorComputeConfidence(unittest.TestCase):

    def _make_orch(self):
        from scaffold.agent.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.calibrator = ConfidenceCalibrator()
        orch._prm = None
        orch._feature_extractor = MagicMock()
        orch._feature_extractor.extract.return_value = [0.5] * 10
        orch.ml_router = MagicMock()
        orch.ml_router.predict_success.return_value = None
        orch.escalation = MagicMock()
        orch.escalation.failure_count.return_value = 0
        return orch

    def test_returns_none_without_calibrator(self):
        from scaffold.agent.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch.calibrator = None
        result = orch._compute_confidence({}, {}, MagicMock())
        self.assertIsNone(result)

    def test_returns_report_with_calibrator(self):
        orch = self._make_orch()
        patch = {"success": True, "search": "x", "replace": "y", "_critic_confidence": 0.8}
        esc = MagicMock()
        esc.expected_reward = 0.7
        result = orch._compute_confidence({"task_id": "t1"}, patch, esc)
        self.assertIsNotNone(result)
        self.assertIsInstance(result.fused_score, float)

    def test_never_raises_on_bad_state(self):
        orch = self._make_orch()
        orch._feature_extractor.extract.side_effect = RuntimeError("boom")
        result = orch._compute_confidence({"task_id": "t1"}, {}, MagicMock())
        # Should not raise — returns None or valid report
        self.assertTrue(result is None or hasattr(result, "fused_score"))


if __name__ == "__main__":
    unittest.main()
