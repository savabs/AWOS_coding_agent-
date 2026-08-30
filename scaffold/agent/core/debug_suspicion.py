"""Small deterministic ranking layer for unsupported debugger candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .debug_schema import ClaimType, DebugFinding, FindingStatus


DEFAULT_WEIGHTS = {
    "stack_trace_match": 0.25,
    "verifier_test_alignment": 0.25,
    "fault_localizer_rank": 0.20,
    "recent_edit_overlap": 0.10,
    "repeated_failure_prior": 0.10,
    "stage_consistency": 0.10,
}


@dataclass(frozen=True)
class SuspicionCandidate:
    """Non-proof signals describing one candidate investigation target."""

    candidate_id: str
    subject: str
    signals: Dict[str, float] = field(default_factory=dict)
    evidence_ids: Sequence[str] = field(default_factory=tuple)
    source_run_id: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


def _bounded(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return min(1.0, max(0.0, number))


def suspicion_score(
    signals: Dict[str, float], weights: Optional[Dict[str, float]] = None
) -> float:
    """Compute a bounded, explainable score; this is never proof."""
    active_weights = weights or DEFAULT_WEIGHTS
    total_weight = sum(active_weights.values()) or 1.0
    score = sum(
        _bounded(signals.get(name, 0.0)) * weight
        for name, weight in active_weights.items()
    )
    return min(1.0, max(0.0, score / total_weight))


def rank_suspicion(
    candidates: Iterable[SuspicionCandidate],
    *,
    weights: Optional[Dict[str, float]] = None,
    hypothesis_threshold: float = 0.25,
) -> List[DebugFinding]:
    """Return stable-ranked hypotheses/unknowns without proof metadata."""
    findings: List[DebugFinding] = []
    for candidate in candidates:
        score = suspicion_score(candidate.signals, weights)
        status = (
            FindingStatus.HYPOTHESIS
            if score >= hypothesis_threshold
            else FindingStatus.UNKNOWN
        )
        findings.append(
            DebugFinding(
                finding_id=f"suspicion:{candidate.candidate_id}",
                claim_type=ClaimType.ROOT_CAUSE,
                subject=candidate.subject,
                status=status,
                title="Investigation candidate",
                conclusion=candidate.subject,
                evidence_ids=list(candidate.evidence_ids),
                source_run_id=candidate.source_run_id,
                attribution_score=score,
                metadata={
                    **candidate.metadata,
                    "signals": dict(candidate.signals),
                    "weights": dict(weights or DEFAULT_WEIGHTS),
                    "ranking_source": "deterministic_suspicion_engine",
                },
            )
        )
    findings.sort(
        key=lambda finding: (
            -(finding.attribution_score or 0.0),
            finding.finding_id,
        )
    )
    return findings
