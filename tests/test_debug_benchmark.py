"""Anti-hallucination benchmark for the evidence-first debugger contract."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_attribution import localized_fault_to_hypothesis
from scaffold.agent.core.debug_report import RerunOutcome, RerunStatus
from scaffold.agent.core.debug_resolution import (
    ResolutionAction,
    resolve_finding_after_rerun,
)
from scaffold.agent.core.debug_rules import apply_proof_rules
from scaffold.agent.core.debug_schema import (
    ClaimType,
    DebugEvidence,
    FindingStatus,
    EvidenceKind,
)
from scaffold.agent.fault_localizer import LocalizedFault


def _direct(case_id, payload, valid=True):
    return DebugEvidence(
        evidence_id=f"benchmark:{case_id}",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="benchmark",
        observed_at=1.0,
        payload=payload,
        valid=valid,
    )


@pytest.mark.parametrize(
    "case_id,payload,expected_rule",
    [
        ("process-crash", {"exit_code": 1, "stderr": "crashed"}, "process-nonzero-exit"),
        (
            "failing-test",
            {"normalized_event_type": "test_result", "passed": False, "output": "AssertionError"},
            "test-failure-with-output",
        ),
        (
            "verifier-rejection",
            {"verify_passed": False, "verify_attempts": 1, "failure_reason": "syntax"},
            "verifier-rejection",
        ),
        ("parser-error", {"stderr": "SyntaxError: invalid syntax"}, "parser-or-import-error"),
        (
            "edit-failure",
            {"status": "failed", "error_context": "SEARCH string not found"},
            "edit-application-failure",
        ),
    ],
)
def test_supported_failure_cases_produce_bounded_proof(case_id, payload, expected_rule):
    findings = apply_proof_rules([_direct(case_id, payload)])

    assert any(finding.proof_rule_id == expected_rule for finding in findings)
    assert all(finding.status is FindingStatus.PROVEN for finding in findings)
    assert all(finding.claim_type is ClaimType.OBSERVATION for finding in findings)


def test_unsupported_cases_never_emit_proven():
    heuristic = DebugEvidence(
        evidence_id="benchmark:heuristic",
        kind=EvidenceKind.ATTRIBUTION_SIGNAL,
        source="FaultLocalizer",
        observed_at=1.0,
        payload={"file": "app.py", "confidence": 0.99},
    )
    missing_output = _direct(
        "missing-output",
        {"normalized_event_type": "test_result", "passed": False},
    )
    corrupt = _direct("corrupt", {"exit_code": 1}, valid=False)

    findings = apply_proof_rules([heuristic, missing_output, corrupt])

    assert findings == []


def test_root_cause_localization_and_divergent_rerun_remain_non_proof():
    hypothesis = localized_fault_to_hypothesis(
        LocalizedFault("app.py", "run", 1, 3, 0.99, "stack trace hit"),
        evidence_ids=["benchmark:failing-test"],
    )
    outcome = RerunOutcome(
        rerun_id="benchmark-rerun",
        status=RerunStatus.DIVERGENT,
        evidence_ids=["benchmark:rerun"],
    )
    resolution = resolve_finding_after_rerun(hypothesis, outcome)

    assert hypothesis.status is FindingStatus.HYPOTHESIS
    assert hypothesis.proof_rule_id is None
    assert resolution.action is ResolutionAction.DIVERGENT
    assert resolution.effective_status is FindingStatus.HYPOTHESIS


def test_ui_and_api_expose_truth_surface_without_model_dependency():
    ui = Path("gui/static/agent.html").read_text(encoding="utf-8")
    server = Path("gui/server.py").read_text(encoding="utf-8")

    assert "Evidence Debugger" in ui
    assert "/api/debug/" in ui
    assert "PROVEN" in ui
    assert "HYPOTHESIS" in ui
    assert "UNKNOWN" in ui
    assert "/api/debug/" in server
    assert "inspect_debug_bundle" in server
