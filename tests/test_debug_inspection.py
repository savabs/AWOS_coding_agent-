"""Tests for read-only debugger bundle inspection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_inspection import inspect_debug_bundle
from scaffold.agent.core.debug_journal import DebugJournal
from scaffold.agent.core.debug_report import RerunOutcome, RerunStatus
from scaffold.agent.core.debug_schema import (
    ClaimType,
    DebugEvidence,
    DebugFinding,
    EvidenceKind,
    FindingStatus,
)


def test_inspection_builds_machine_readable_session_report(tmp_path: Path):
    journal = DebugJournal(tmp_path / "debug", "session-1")
    evidence = DebugEvidence(
        evidence_id="e-1",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="pytest",
        observed_at=1.0,
        payload={"passed": False},
    )
    finding = DebugFinding(
        finding_id="f-1",
        claim_type=ClaimType.OBSERVATION,
        subject="test failed",
        status=FindingStatus.PROVEN,
        evidence_ids=[evidence.evidence_id],
        proof_rule_id="test-failure-with-output",
        proof_rule_version="1.0",
    )
    journal.append_evidence(evidence)
    journal.append_finding(finding)
    journal.append_rerun(
        RerunOutcome(rerun_id="r-1", status=RerunStatus.DIVERGENT)
    )

    report = inspect_debug_bundle(tmp_path / "debug", "session-1")
    payload = report.to_dict()

    assert payload["session_id"] == "session-1"
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["status"] == "PROVEN"
    assert payload["rerun_outcomes"][0]["status"] == "DIVERGENT"
    assert payload["metadata"]["evidence_count"] == 1


def test_inspection_is_read_only_for_missing_bundle(tmp_path: Path):
    try:
        inspect_debug_bundle(tmp_path / "debug", "missing")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing bundle must not be created during inspection")
    assert not (tmp_path / "debug").exists()
