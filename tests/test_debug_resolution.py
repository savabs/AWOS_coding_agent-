"""Tests for append-only rerun resolution."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_report import RerunOutcome, RerunStatus
from scaffold.agent.core.debug_resolution import (
    ResolutionAction,
    resolve_finding_after_rerun,
)
from scaffold.agent.core.debug_schema import ClaimType, DebugFinding, FindingStatus


def _finding(claim_type, status=FindingStatus.HYPOTHESIS):
    fields = {}
    if status is FindingStatus.PROVEN:
        fields = {
            "evidence_ids": ["e-original"],
            "proof_rule_id": "direct-observation",
            "proof_rule_version": "1",
        }
    return DebugFinding(
        finding_id="finding-1",
        claim_type=claim_type,
        subject="recorded failure",
        status=status,
        conclusion="recorded failure",
        **fields,
    )


def _outcome(status, evidence_ids=None):
    return RerunOutcome(
        rerun_id="rerun-1",
        status=status,
        evidence_ids=evidence_ids or [],
    )


def test_reproduced_observation_creates_proven_replacement():
    original = _finding(ClaimType.OBSERVATION)
    resolution = resolve_finding_after_rerun(
        original,
        _outcome(RerunStatus.REPRODUCED, ["e-rerun"]),
    )

    assert resolution.action is ResolutionAction.UPGRADED_REPRODUCED
    assert resolution.effective_status is FindingStatus.PROVEN
    assert resolution.replacement_finding is not None
    assert resolution.replacement_finding.status is FindingStatus.PROVEN
    assert resolution.replacement_finding.evidence_ids == ["e-rerun"]
    assert original.status is FindingStatus.HYPOTHESIS


def test_reproduced_root_cause_remains_hypothesis():
    original = _finding(ClaimType.ROOT_CAUSE)
    resolution = resolve_finding_after_rerun(
        original,
        _outcome(RerunStatus.REPRODUCED, ["e-rerun"]),
    )

    assert resolution.action is ResolutionAction.REPRODUCED_CAUSE_UNPROVEN
    assert resolution.effective_status is FindingStatus.HYPOTHESIS
    assert resolution.replacement_finding is None


def test_non_reproduced_and_divergent_outcomes_preserve_original():
    original = _finding(ClaimType.OBSERVATION)

    for status, action in [
        (RerunStatus.NOT_REPRODUCED, ResolutionAction.NOT_REPRODUCED),
        (RerunStatus.DIVERGENT, ResolutionAction.DIVERGENT),
        (RerunStatus.INCONCLUSIVE, ResolutionAction.INCONCLUSIVE),
        (RerunStatus.ERROR, ResolutionAction.ERROR),
    ]:
        resolution = resolve_finding_after_rerun(original, _outcome(status))
        assert resolution.action is action
        assert resolution.effective_status is FindingStatus.HYPOTHESIS
        assert resolution.replacement_finding is None


def test_existing_proven_finding_is_never_downgraded():
    original = _finding(ClaimType.OBSERVATION, FindingStatus.PROVEN)
    resolution = resolve_finding_after_rerun(
        original,
        _outcome(RerunStatus.NOT_REPRODUCED),
    )

    assert resolution.action is ResolutionAction.PRESERVED_PROVEN
    assert resolution.effective_status is FindingStatus.PROVEN
    assert original.status is FindingStatus.PROVEN
