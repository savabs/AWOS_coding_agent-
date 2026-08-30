"""Tests for canonical debugger task and session reports."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_report import (
    CauseChain,
    DebugReport,
    ReportScope,
    RerunOutcome,
    RerunStatus,
)
from scaffold.agent.core.debug_schema import (
    ClaimType,
    DebugFinding,
    FindingStatus,
)


def _finding(finding_id: str, status: FindingStatus) -> DebugFinding:
    proof_fields = (
        {
            "evidence_ids": [f"evidence-{finding_id}"],
            "proof_rule_id": "test-proof",
            "proof_rule_version": "1",
        }
        if status is FindingStatus.PROVEN
        else {}
    )
    return DebugFinding(
        finding_id=finding_id,
        claim_type=ClaimType.OBSERVATION,
        subject=f"subject-{finding_id}",
        status=status,
        **proof_fields,
    )


def test_session_report_round_trip_preserves_findings_and_reruns():
    report = DebugReport(
        report_id="report-1",
        scope=ReportScope.SESSION,
        session_id="session-1",
        findings=[
            _finding("finding-1", FindingStatus.PROVEN),
            _finding("finding-2", FindingStatus.HYPOTHESIS),
        ],
        artifact_refs=["events.jsonl", "pytest.stderr"],
        cause_chains=[
            CauseChain(
                chain_id="chain-1",
                finding_ids=["finding-2", "finding-1"],
                relationship="candidate-cause-of",
                evidence_ids=["evidence-finding-2"],
            )
        ],
        rerun_outcomes=[
            RerunOutcome(
                rerun_id="rerun-1",
                status=RerunStatus.DIVERGENT,
                source_finding_id="finding-2",
                evidence_ids=["rerun-evidence-1"],
                started_at=10.0,
                completed_at=12.0,
                summary="The failure did not reproduce under the changed workspace.",
            )
        ],
    )

    encoded = report.to_json()
    restored = DebugReport.from_json(encoded)

    assert restored == report
    assert restored.scope is ReportScope.SESSION
    assert [finding.status for finding in restored.findings] == [
        FindingStatus.PROVEN,
        FindingStatus.HYPOTHESIS,
    ]
    assert restored.rerun_outcomes[0].status is RerunStatus.DIVERGENT


def test_report_json_is_deterministic_for_same_report():
    report = DebugReport(
        report_id="report-2",
        scope=ReportScope.TASK,
        session_id="session-2",
        task_id="task-2",
        created_at=20.0,
        findings=[_finding("finding-3", FindingStatus.UNKNOWN)],
    )

    assert report.to_json() == report.to_json()
    assert DebugReport.from_json(report.to_json()).to_dict() == report.to_dict()


def test_task_report_requires_task_id():
    try:
        DebugReport(
            report_id="report-3",
            scope=ReportScope.TASK,
            session_id="session-3",
        )
    except ValueError as exc:
        assert "task_id" in str(exc)
    else:
        raise AssertionError("TASK report without task_id must be rejected")
