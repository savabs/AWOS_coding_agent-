"""
confidence_calibrator.py — P9 Confidence Calibration.

Fuses scores from multiple signal sources (Critic, PRM, GP, LinUCB) into a
single calibrated probability that the current patch is correct.

Signal sources (ordered by directness):
    critic  (0.40) — reviewed the actual patch text, most specific
    prm     (0.35) — trained on historical patch success, most predictive
    gp      (0.15) — GP world model P(success | task features)
    linucb  (0.10) — LinUCB expected reward for chosen action

Calibration:
    PRM outputs raw logits; we apply a Platt sigmoid to map them to [0,1].
    Scale/bias are updated from RewardStore history via `fit_calibration()`.
    All other sources already output [0,1].

Fusion:
    Weighted average over *available* signals (unavailable sources are excluded
    and their weight redistributed proportionally so scores always sum to 1).

Thresholds (configurable via env):
    AWOS_ABORT_THRESHOLD  (default 0.15) — too low: auto-abort before write
    AWOS_HITL_THRESHOLD   (default 0.30) — uncertain: pause for human review
    (anything >= 0.30 proceeds automatically)

Usage:
    calibrator = ConfidenceCalibrator()
    report = calibrator.fuse(
        critic_score=0.87,
        prm_raw=0.4,
        gp_score=None,          # not available
        linucb_score=0.6,
    )
    if report.should_abort:
        ...
    elif report.should_pause:
        ...   # HITL gate
    else:
        apply_patch()
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
_ABORT_THRESHOLD = float(os.getenv("AWOS_ABORT_THRESHOLD", "0.15"))
_HITL_THRESHOLD  = float(os.getenv("AWOS_HITL_THRESHOLD",  "0.30"))
_VERBOSE         = os.getenv("AWOS_CONFIDENCE_VERBOSE", "false").lower() != "false"

# Default signal weights — must sum to 1.0
_DEFAULT_WEIGHTS = {
    "critic":  0.40,
    "prm":     0.35,
    "gp":      0.15,
    "linucb":  0.10,
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ConfidenceSignal:
    """
    One score contribution to the fused confidence.

    name:      signal source identifier ("critic", "prm", "gp", "linucb")
    raw_score: 0.0–1.0 calibrated probability from this source
    weight:    the relative trust weight assigned to this source
    available: False when the source was not computed (e.g. PRM not trained)
    """
    name:      str
    raw_score: float
    weight:    float
    available: bool = True


@dataclass
class ConfidenceReport:
    """
    Fused confidence for one patch candidate.

    fused_score:  0.0–1.0 final calibrated probability of success
    signals:      list of ConfidenceSignal used in the fusion
    verdict:      "confident" | "uncertain" | "abort"
    should_pause: True when fused_score < HITL_THRESHOLD (HITL gate)
    should_abort: True when fused_score < ABORT_THRESHOLD (stop early)
    explanation:  one-line human-readable reasoning
    """
    fused_score:  float
    signals:      List[ConfidenceSignal]
    verdict:      str
    should_pause: bool
    should_abort: bool
    explanation:  str

    def render(self) -> str:
        """One-line terminal representation for live_renderer."""
        col_w = 32
        bar_len = int(self.fused_score * 20)
        col = "\033[32m" if self.fused_score >= 0.70 else (
              "\033[33m" if self.fused_score >= _HITL_THRESHOLD else "\033[31m")
        bar = "█" * bar_len + "░" * (20 - bar_len)
        parts = []
        for s in self.signals:
            if s.available:
                parts.append(f"{s.name}={s.raw_score:.2f}")
        signals_str = "  ".join(parts)
        return (
            f"Confidence  {col}{bar}\033[0m  {self.fused_score:.0%}  "
            f"[{self.verdict}]  {signals_str}"
        )


# ── Calibrator ────────────────────────────────────────────────────────────────

class ConfidenceCalibrator:
    """
    Fuses multiple scoring signals into a single calibrated confidence score.

    Thread-safe: all state is read-only after __init__. Call fit_calibration()
    periodically from the orchestrator to update Platt scale/bias from history.
    """

    def __init__(
        self,
        weights: Optional[dict] = None,
        platt_scale: float = 1.0,
        platt_bias: float  = 0.0,
    ) -> None:
        self._weights     = {**_DEFAULT_WEIGHTS, **(weights or {})}
        self._platt_scale = platt_scale
        self._platt_bias  = platt_bias

    # ── Public API ────────────────────────────────────────────────────────────

    def fuse(
        self,
        critic_score:  Optional[float] = None,
        prm_raw:       Optional[float] = None,   # raw PRM logit (will be calibrated)
        gp_score:      Optional[float] = None,
        linucb_score:  Optional[float] = None,
    ) -> ConfidenceReport:
        """
        Fuse available signals into a calibrated confidence report.

        None means the signal was not available (e.g. PRM not trained yet).
        The weights of missing signals are redistributed to available ones.
        """
        raw_inputs = {
            "critic":  critic_score,
            "prm":     self._calibrate_prm(prm_raw) if prm_raw is not None else None,
            "gp":      gp_score,
            "linucb":  linucb_score,
        }

        signals = []
        for name, score in raw_inputs.items():
            signals.append(ConfidenceSignal(
                name=name,
                raw_score=float(score) if score is not None else 0.0,
                weight=self._weights.get(name, 0.0),
                available=score is not None,
            ))

        fused = self._weighted_average(signals)
        verdict, explanation = self._verdict(fused, signals)

        report = ConfidenceReport(
            fused_score=fused,
            signals=signals,
            verdict=verdict,
            should_pause=fused < _HITL_THRESHOLD and not (fused < _ABORT_THRESHOLD),
            should_abort=fused < _ABORT_THRESHOLD,
            explanation=explanation,
        )

        if _VERBOSE:
            logger.info("[confidence] %s", report.render())

        return report

    def fit_calibration(self, reward_store) -> None:
        """
        Update Platt scale/bias from recent RewardStore episodes that have both
        a PRM prediction and a known outcome.

        Fits logistic regression: P(success) = sigmoid(scale * prm_raw + bias).
        Only updates if >= 20 labelled PRM episodes are available.
        Updates are applied in-place (no file persistence needed).
        """
        try:
            episodes = reward_store.get_recent(n=200)
            prm_preds = [(e.features[-1], int(e.success))
                          for e in episodes
                          if len(e.features) > 0 and e.features[-1] != 0.0]
            if len(prm_preds) < 20:
                return
            self._platt_scale, self._platt_bias = self._fit_platt(prm_preds)
            logger.info(
                "[calibrator] Platt updated: scale=%.3f bias=%.3f (n=%d)",
                self._platt_scale, self._platt_bias, len(prm_preds),
            )
        except Exception as exc:
            logger.debug("[calibrator] fit_calibration failed: %s", exc)

    def summary(self) -> dict:
        """Return calibrator state for LearningInspector."""
        return {
            "platt_scale": round(self._platt_scale, 4),
            "platt_bias":  round(self._platt_bias,  4),
            "weights":     {k: round(v, 3) for k, v in self._weights.items()},
            "abort_threshold": _ABORT_THRESHOLD,
            "hitl_threshold":  _HITL_THRESHOLD,
        }

    # ── Private: calibration ──────────────────────────────────────────────────

    def _calibrate_prm(self, raw: float) -> float:
        """Apply Platt sigmoid to map raw PRM score → calibrated probability."""
        calibrated = 1.0 / (1.0 + math.exp(-(self._platt_scale * raw + self._platt_bias)))
        return max(0.0, min(1.0, calibrated))

    def _fit_platt(self, pairs: list) -> tuple:
        """
        Minimal gradient-descent Platt scaling.
        pairs: [(raw_score, label)] where label ∈ {0, 1}
        Returns (scale, bias) after up to 200 gradient steps.
        """
        scale, bias = self._platt_scale, self._platt_bias
        lr = 0.01
        for _ in range(200):
            d_scale = d_bias = 0.0
            for x, y in pairs:
                p = 1.0 / (1.0 + math.exp(-(scale * x + bias)))
                err = p - y
                d_scale += err * x
                d_bias  += err
            n = len(pairs)
            scale -= lr * d_scale / n
            bias  -= lr * d_bias  / n
        return scale, bias

    # ── Private: fusion ───────────────────────────────────────────────────────

    @staticmethod
    def _weighted_average(signals: List[ConfidenceSignal]) -> float:
        """
        Compute weighted average over available signals.
        Redistributes unavailable signal weights to available ones proportionally.
        Returns 0.5 (max uncertainty) if no signals available.
        """
        available = [s for s in signals if s.available]
        if not available:
            return 0.5

        total_weight = sum(s.weight for s in available)
        if total_weight == 0.0:
            return sum(s.raw_score for s in available) / len(available)

        return sum(s.raw_score * s.weight for s in available) / total_weight

    def _verdict(self, score: float, signals: List[ConfidenceSignal]) -> tuple:
        """Return (verdict_str, explanation_str) for a fused score."""
        n_available = sum(1 for s in signals if s.available)

        if score < _ABORT_THRESHOLD:
            return (
                "abort",
                f"score={score:.0%} < abort_threshold={_ABORT_THRESHOLD:.0%} "
                f"({n_available} signal(s) — halting to save budget)",
            )
        elif score < _HITL_THRESHOLD:
            return (
                "uncertain",
                f"score={score:.0%} < hitl_threshold={_HITL_THRESHOLD:.0%} "
                f"({n_available} signal(s) — HITL review recommended)",
            )
        elif score >= 0.70:
            return (
                "confident",
                f"score={score:.0%} ({n_available} signal(s) agree — proceeding)",
            )
        else:
            return (
                "uncertain",
                f"score={score:.0%} — moderate confidence, proceeding with caution",
            )


# ── Module-level helpers ──────────────────────────────────────────────────────

def abort_threshold() -> float:
    return _ABORT_THRESHOLD


def hitl_threshold() -> float:
    return _HITL_THRESHOLD
