"""Tests enforcing FaultLocalizer attribution-only behavior."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_attribution import (
    hypotheses_from_localized_faults,
    localized_fault_to_hypothesis,
    localize_to_hypotheses,
)
from scaffold.agent.core.debug_schema import ClaimType, FindingStatus
from scaffold.agent.fault_localizer import FaultLocalizer, LocalizedFault


def test_localized_fault_becomes_root_cause_hypothesis_not_proof():
    fault = LocalizedFault(
        file_path="src/auth.py",
        symbol_name="authenticate",
        start_line=10,
        end_line=22,
        confidence=0.91,
        reason="stack trace hit",
    )

    finding = localized_fault_to_hypothesis(
        fault,
        evidence_ids=["finding:test-failure"],
        source_run_id="run-1",
    )

    assert finding.status is FindingStatus.HYPOTHESIS
    assert finding.claim_type is ClaimType.ROOT_CAUSE
    assert finding.proof_rule_id is None
    assert finding.proof_rule_version is None
    assert finding.evidence_ids == ["finding:test-failure"]
    assert finding.attribution_score == 0.91
    assert finding.metadata["attribution_source"] == "FaultLocalizer"


def test_hypotheses_are_ranked_stably_and_confidence_is_bounded():
    faults = [
        LocalizedFault("b.py", "b", 2, 3, 0.8, "keyword"),
        LocalizedFault("a.py", "a", 1, 2, 1.5, "stack trace"),
        LocalizedFault("c.py", "c", 4, 5, -1.0, "recency"),
    ]

    findings = hypotheses_from_localized_faults(faults)

    assert [finding.metadata["file_path"] for finding in findings] == [
        "a.py",
        "b.py",
        "c.py",
    ]
    assert [finding.attribution_score for finding in findings] == [1.0, 0.8, 0.0]
    assert all(finding.status is FindingStatus.HYPOTHESIS for finding in findings)


def test_actual_localizer_output_stays_hypothesis_only(tmp_path: Path):
    source = tmp_path / "worker.py"
    source.write_text(
        "def execute():\n"
        "    return missing_name\n"
    )
    trace = f'File "{source}", line 2, in execute\nNameError: missing_name\n'

    findings = localize_to_hypotheses(
        FaultLocalizer(),
        task_description="fix execute failure",
        error_trace=trace,
        project_root=str(tmp_path),
        evidence_ids=["e-test-failure"],
        source_run_id="run-2",
    )

    assert findings
    assert all(finding.status is FindingStatus.HYPOTHESIS for finding in findings)
    assert all(finding.claim_type is ClaimType.ROOT_CAUSE for finding in findings)
    assert all(finding.proof_rule_id is None for finding in findings)
    assert all(finding.evidence_ids == ["e-test-failure"] for finding in findings)
