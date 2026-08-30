"""Tests for append-only native AWOS debugger bundles."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_journal import DebugJournal
from scaffold.agent.core.debug_report import RerunOutcome, RerunStatus
from scaffold.agent.core.debug_schema import (
    ClaimType,
    DebugEvidence,
    DebugFinding,
    EvidenceKind,
    FindingStatus,
)


def _proven_finding() -> DebugFinding:
    return DebugFinding(
        finding_id="finding-1",
        claim_type=ClaimType.OBSERVATION,
        subject="pytest exited with code 1",
        status=FindingStatus.PROVEN,
        evidence_ids=["evidence-1"],
        proof_rule_id="nonzero-exit",
        proof_rule_version="1",
    )


def test_journal_writes_manifest_and_append_only_jsonl_bundle(tmp_path: Path):
    journal = DebugJournal(tmp_path / "debug", "session-1")
    evidence = DebugEvidence(
        evidence_id="evidence-1",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="pytest",
        observed_at=10.0,
        payload={"exit_code": 1},
    )
    finding = _proven_finding()
    rerun = RerunOutcome(
        rerun_id="rerun-1",
        status=RerunStatus.REPRODUCED,
        source_finding_id=finding.finding_id,
        evidence_ids=[evidence.evidence_id],
        started_at=11.0,
        completed_at=12.0,
    )

    journal.append_evidence(evidence)
    journal.append_finding(finding)
    journal.append_rerun(rerun)

    assert journal.bundle_dir == tmp_path / "debug" / "session-1"
    assert journal.manifest_path.exists()
    assert journal.evidence_path.read_text().count("\n") == 1
    assert journal.findings_path.read_text().count("\n") == 1
    assert journal.reruns_path.read_text().count("\n") == 1
    assert journal.read_manifest()["session_id"] == "session-1"
    assert journal.read_evidence() == [evidence]
    assert journal.read_findings() == [finding]
    assert journal.read_reruns() == [rerun]


def test_journal_appends_without_replacing_existing_records(tmp_path: Path):
    journal = DebugJournal(tmp_path / "debug", "session-2")
    first = DebugEvidence(
        evidence_id="evidence-1",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="tool",
        observed_at=1.0,
    )
    second = DebugEvidence(
        evidence_id="evidence-2",
        kind=EvidenceKind.EVIDENCE_GAP,
        source="debugger",
        observed_at=2.0,
    )

    journal.append_evidence(first)
    original_manifest = journal.manifest_path.read_text()
    journal.append_evidence(second)

    assert journal.read_evidence() == [first, second]
    assert journal.manifest_path.read_text() == original_manifest
    assert [json.loads(line)["evidence_id"] for line in
            journal.evidence_path.read_text().splitlines()] == [
                "evidence-1",
                "evidence-2",
            ]


def test_journal_rejects_path_traversal_session_ids(tmp_path: Path):
    for session_id in ("../escape", "nested/session", ""):
        try:
            DebugJournal(tmp_path / "debug", session_id)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe session_id must be rejected")
