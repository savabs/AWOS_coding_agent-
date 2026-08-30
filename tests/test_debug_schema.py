"""Tests for the canonical evidence-first debugger truth schema."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from scaffold.agent.core.debug_schema import (
    ClaimType,
    DebugEvidence,
    DebugFinding,
    EvidenceKind,
    FailureStage,
    FindingStatus,
)


def test_finding_statuses_remain_distinct_after_json_round_trip():
    findings = []
    for status in FindingStatus:
        proof_fields = (
            {
                "evidence_ids": ["evidence-proof"],
                "proof_rule_id": "round-trip",
                "proof_rule_version": "1",
            }
            if status is FindingStatus.PROVEN
            else {}
        )
        findings.append(
            DebugFinding(
                claim_type=ClaimType.OBSERVATION,
                subject=f"subject-{status.value}",
                status=status,
                **proof_fields,
            )
        )

    restored = [DebugFinding.from_json(finding.to_json()) for finding in findings]

    assert [finding.status for finding in restored] == list(FindingStatus)
    assert len({finding.to_json() for finding in restored}) == 3


def test_evidence_round_trip_preserves_source_and_kind():
    evidence = DebugEvidence(
        evidence_id="evidence-1",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="pytest",
        observed_at=123.5,
        payload={"exit_code": 1, "stderr": "failure"},
        source_ref=".awos/debug/session/tool.stderr:1-2",
        artifact_hash="sha256:abc",
        event_id="event-1",
        parent_evidence_ids=["event-0"],
    )

    restored = DebugEvidence.from_dict(json.loads(evidence.to_json()))

    assert restored == evidence
    assert restored.kind is EvidenceKind.DIRECT_OBSERVATION
    assert restored.payload["exit_code"] == 1


def test_proven_finding_requires_versioned_proof_and_evidence():
    with pytest.raises(ValueError, match="evidence_ids"):
        DebugFinding(
            claim_type=ClaimType.OBSERVATION,
            subject="test command failed",
            status=FindingStatus.PROVEN,
            proof_rule_id="test-failure",
            proof_rule_version="1",
        )

    with pytest.raises(ValueError, match="proof_rule_id"):
        DebugFinding(
            claim_type=ClaimType.OBSERVATION,
            subject="test command failed",
            status=FindingStatus.PROVEN,
            evidence_ids=["evidence-1"],
            proof_rule_version="1",
        )


def test_proven_finding_round_trip_preserves_claim_scope_and_proof_metadata():
    finding = DebugFinding(
        finding_id="finding-1",
        claim_type=ClaimType.DIAGNOSIS,
        subject="parser rejected src/app.py at line 4",
        status=FindingStatus.PROVEN,
        title="Syntax error",
        conclusion="The parser reported an invalid expression at line 4.",
        evidence_ids=["evidence-1"],
        proof_rule_id="parser-diagnostic",
        proof_rule_version="1.0",
        failure_stage=FailureStage.PARSING,
        source_run_id="run-1",
    )

    restored = DebugFinding.from_json(finding.to_json())

    assert restored == finding
    assert restored.claim_type is ClaimType.DIAGNOSIS
    assert restored.failure_stage is FailureStage.PARSING
    assert restored.status is FindingStatus.PROVEN
