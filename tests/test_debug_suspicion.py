"""Tests for bounded suspicion ranking."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_schema import ClaimType, FindingStatus
from scaffold.agent.core.debug_suspicion import (
    SuspicionCandidate,
    rank_suspicion,
    suspicion_score,
)


def test_score_is_bounded_and_uses_missing_signals_as_zero():
    assert suspicion_score({"stack_trace_match": 2.0}) == pytest.approx(0.25)
    assert suspicion_score({"stack_trace_match": -1.0}) == 0.0
    assert suspicion_score({}) == 0.0


def test_candidates_are_ranked_and_never_proven():
    findings = rank_suspicion(
        [
            SuspicionCandidate(
                "low", "low candidate", {"recent_edit_overlap": 0.1}
            ),
            SuspicionCandidate(
                "high",
                "high candidate",
                {"stack_trace_match": 1.0, "fault_localizer_rank": 1.0},
                evidence_ids=("e-1",),
            ),
        ]
    )

    assert [finding.finding_id for finding in findings] == [
        "suspicion:high",
        "suspicion:low",
    ]
    assert findings[0].status is FindingStatus.HYPOTHESIS
    assert findings[1].status is FindingStatus.UNKNOWN
    assert all(finding.claim_type is ClaimType.ROOT_CAUSE for finding in findings)
    assert all(finding.proof_rule_id is None for finding in findings)
    assert all(finding.proof_rule_version is None for finding in findings)


def test_ranking_is_stable_for_equal_scores():
    candidates = [
        SuspicionCandidate("b", "B", {"stage_consistency": 0.5}),
        SuspicionCandidate("a", "A", {"stage_consistency": 0.5}),
    ]

    findings = rank_suspicion(candidates)

    assert [finding.finding_id for finding in findings] == [
        "suspicion:a",
        "suspicion:b",
    ]
