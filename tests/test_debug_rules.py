"""Tests for deterministic proof rules and their honesty boundaries."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_rules import (
    apply_proof_rules,
    prove_edit_application_failure,
    prove_nonzero_exit,
    prove_parser_or_import_error,
    prove_test_failure,
    prove_verifier_rejection,
)
from scaffold.agent.core.debug_schema import (
    DebugEvidence,
    EvidenceKind,
    FailureStage,
    FindingStatus,
)


def _evidence(payload, *, evidence_id="e-1", valid=True):
    return DebugEvidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="test-fixture",
        observed_at=1.0,
        payload=payload,
        event_id="run-1",
        valid=valid,
    )


def test_nonzero_exit_proves_process_failure_only():
    finding = prove_nonzero_exit(
        _evidence({"exit_code": 2, "stderr": "command failed"})
    )

    assert finding is not None
    assert finding.status is FindingStatus.PROVEN
    assert finding.failure_stage is FailureStage.EXECUTION
    assert finding.proof_rule_id == "process-nonzero-exit"
    assert finding.claim_type.value == "OBSERVATION"
    assert "root" not in finding.conclusion.lower()


def test_failing_test_requires_recorded_output():
    finding = prove_test_failure(
        _evidence(
            {
                "normalized_event_type": "test_result",
                "test_name": "test_login",
                "passed": False,
                "output": "AssertionError: expected 1, got 2",
            }
        )
    )

    assert finding is not None
    assert finding.failure_stage is FailureStage.TEST
    assert "test_login" in finding.conclusion

    no_output = prove_test_failure(
        _evidence(
            {
                "normalized_event_type": "test_result",
                "test_name": "test_login",
                "passed": False,
            },
            evidence_id="e-2",
        )
    )
    assert no_output is None


def test_verifier_rejection_proves_rejection_not_cause():
    finding = prove_verifier_rejection(
        _evidence(
            {
                "verify_passed": False,
                "verify_attempts": 1,
                "failure_reason": "edit did not match target",
            }
        )
    )

    assert finding is not None
    assert finding.failure_stage is FailureStage.VERIFICATION
    assert "rejected" in finding.conclusion.lower()
    assert "caused" not in finding.conclusion.lower()


def test_parser_rule_requires_known_tool_diagnostic():
    finding = prove_parser_or_import_error(
        _evidence({"stderr": "  File 'app.py', line 2\nSyntaxError: invalid syntax"})
    )
    assert finding is not None
    assert finding.failure_stage is FailureStage.PARSING

    unrelated = prove_parser_or_import_error(
        _evidence({"stderr": "the parser completed successfully"}, evidence_id="e-2")
    )
    assert unrelated is None


def test_edit_rule_requires_explicit_failure_and_context():
    finding = prove_edit_application_failure(
        _evidence(
            {
                "status": "failed",
                "error_context": "search text did not match",
            }
        )
    )
    assert finding is not None
    assert finding.failure_stage is FailureStage.EDIT

    no_context = prove_edit_application_failure(
        _evidence({"status": "failed"}, evidence_id="e-2")
    )
    assert no_context is None


def test_invalid_or_heuristic_evidence_can_never_produce_proof():
    invalid = _evidence(
        {
            "exit_code": 1,
            "stderr": "SyntaxError: invalid syntax",
            "normalized_event_type": "test_result",
            "passed": False,
            "output": "traceback",
        },
        valid=False,
    )
    heuristic = DebugEvidence(
        evidence_id="e-heuristic",
        kind=EvidenceKind.ATTRIBUTION_SIGNAL,
        source="fault_localizer",
        observed_at=1.0,
        payload={"file": "app.py", "score": 0.99},
    )

    assert apply_proof_rules([invalid, heuristic]) == []


def test_apply_rules_can_emit_multiple_bounded_observations_from_one_artifact():
    evidence = _evidence(
        {
            "exit_code": 1,
            "normalized_event_type": "test_result",
            "passed": False,
            "test_name": "test_api",
            "output": "SyntaxError: invalid syntax",
        }
    )

    findings = apply_proof_rules([evidence])

    assert {finding.proof_rule_id for finding in findings} == {
        "process-nonzero-exit",
        "test-failure-with-output",
        "parser-or-import-error",
    }
    assert all(finding.status is FindingStatus.PROVEN for finding in findings)
