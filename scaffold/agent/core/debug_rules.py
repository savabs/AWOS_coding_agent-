"""Deterministic, auditable proof rules for normalized debugger evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

from .debug_schema import (
    ClaimType,
    DebugEvidence,
    DebugFinding,
    EvidenceKind,
    FailureStage,
    FindingStatus,
)

RULE_VERSION = "1.0"


@dataclass(frozen=True)
class ProofRule:
    """Metadata and evaluator for one narrowly scoped proof rule."""

    rule_id: str
    description: str
    evaluate: Callable[[DebugEvidence], Optional[DebugFinding]]


def _event_type(evidence: DebugEvidence) -> str:
    payload = evidence.payload
    return str(
        payload.get("normalized_event_type")
        or payload.get("event")
        or payload.get("event_type")
        or ""
    ).lower()


def _original_event_type(evidence: DebugEvidence) -> str:
    return str(evidence.payload.get("original_event_type", "")).lower()


def _text(evidence: DebugEvidence) -> str:
    payload = evidence.payload
    values = [
        payload.get("traceback"),
        payload.get("stderr"),
        payload.get("stdout"),
        payload.get("output"),
        payload.get("error"),
        payload.get("failure_reason"),
        payload.get("error_context"),
    ]
    return "\n".join(str(value) for value in values if value)


def _finding(
    evidence: DebugEvidence,
    rule_id: str,
    title: str,
    conclusion: str,
    stage: FailureStage,
) -> DebugFinding:
    """Build a deterministic, claim-scoped proven observation."""
    return DebugFinding(
        finding_id=f"finding:{rule_id}:{evidence.evidence_id}",
        claim_type=ClaimType.OBSERVATION,
        subject=conclusion,
        status=FindingStatus.PROVEN,
        title=title,
        conclusion=conclusion,
        evidence_ids=[evidence.evidence_id],
        proof_rule_id=rule_id,
        proof_rule_version=RULE_VERSION,
        failure_stage=stage,
        source_run_id=evidence.event_id,
        metadata={
            "source": evidence.source,
            "source_ref": evidence.source_ref,
        },
    )


def prove_nonzero_exit(evidence: DebugEvidence) -> Optional[DebugFinding]:
    """Prove only that a recorded process exited with a nonzero code."""
    if not evidence.valid or evidence.kind is not EvidenceKind.DIRECT_OBSERVATION:
        return None
    code = evidence.payload.get("exit_code", evidence.payload.get("returncode"))
    if code is None:
        code = evidence.payload.get("return_code")
    if isinstance(code, bool) or not isinstance(code, (int, float)) or code == 0:
        return None
    conclusion = f"The recorded process exited with nonzero code {code}."
    return _finding(
        evidence,
        "process-nonzero-exit",
        "Process exited unsuccessfully",
        conclusion,
        FailureStage.EXECUTION,
    )


def prove_test_failure(evidence: DebugEvidence) -> Optional[DebugFinding]:
    """Prove a reported test failure when a concrete output artifact exists."""
    if not evidence.valid or evidence.kind is not EvidenceKind.DIRECT_OBSERVATION:
        return None
    payload = evidence.payload
    if payload.get("no_tests_found") or payload.get("timed_out"):
        return None
    event_type = _event_type(evidence)
    is_test_event = event_type == "test_result" or _original_event_type(evidence) == "verify_result"
    failed = payload.get("passed") is False
    if not is_test_event and not (payload.get("tests_failed", 0) > 0):
        return None
    if not failed and not (payload.get("tests_failed", 0) > 0):
        return None
    output = _text(evidence)
    if not output.strip():
        return None
    test_name = payload.get("test_name") or payload.get("test") or "the recorded test"
    conclusion = f"{test_name} failed and produced recorded test output."
    return _finding(
        evidence,
        "test-failure-with-output",
        "Test failure recorded",
        conclusion,
        FailureStage.TEST,
    )


def prove_verifier_rejection(evidence: DebugEvidence) -> Optional[DebugFinding]:
    """Prove an explicit verifier rejection, not the cause of that rejection."""
    if not evidence.valid or evidence.kind is not EvidenceKind.DIRECT_OBSERVATION:
        return None
    payload = evidence.payload
    explicit_rejection = payload.get("verify_passed") is False
    explicit_rejection = explicit_rejection or (
        _original_event_type(evidence) == "verify_result" and payload.get("passed") is False
    )
    if not explicit_rejection:
        return None
    attempts = payload.get("verify_attempts", 0)
    reason = payload.get("failure_reason") or payload.get("error") or _text(evidence)
    if not reason and not (isinstance(attempts, int) and attempts > 0):
        return None
    conclusion = "The verifier rejected the recorded task result."
    return _finding(
        evidence,
        "verifier-rejection",
        "Verifier rejection recorded",
        conclusion,
        FailureStage.VERIFICATION,
    )


_PARSER_ERROR = re.compile(
    r"(?:SyntaxError|IndentationError|ImportError|ModuleNotFoundError|"
    r"invalid syntax|parser error|parse error)",
    re.IGNORECASE,
)


def prove_parser_or_import_error(evidence: DebugEvidence) -> Optional[DebugFinding]:
    """Prove a tool-reported parser/import diagnostic from captured output."""
    if not evidence.valid or evidence.kind is not EvidenceKind.DIRECT_OBSERVATION:
        return None
    text = _text(evidence)
    if not _PARSER_ERROR.search(text):
        return None
    conclusion = "The recorded tool output contains a parser or import error."
    return _finding(
        evidence,
        "parser-or-import-error",
        "Parser or import error recorded",
        conclusion,
        FailureStage.PARSING,
    )


def prove_edit_application_failure(evidence: DebugEvidence) -> Optional[DebugFinding]:
    """Prove an explicit failed edit application with recorded failure context."""
    if not evidence.valid or evidence.kind is not EvidenceKind.DIRECT_OBSERVATION:
        return None
    payload = evidence.payload
    status = str(payload.get("status") or payload.get("edit_status") or "").lower()
    failed_status = status in {"failed", "failure", "error", "rejected", "mismatch"}
    failed_count = isinstance(payload.get("edits_failed"), int) and payload["edits_failed"] > 0
    if not failed_status and not failed_count:
        return None
    if not _text(evidence) and not payload.get("search") and not payload.get("replace"):
        return None
    conclusion = "The recorded edit application failed."
    return _finding(
        evidence,
        "edit-application-failure",
        "Edit application failure recorded",
        conclusion,
        FailureStage.EDIT,
    )


PROOF_RULES: tuple[ProofRule, ...] = (
    ProofRule(
        "process-nonzero-exit",
        "A valid direct observation contains a nonzero process exit code.",
        prove_nonzero_exit,
    ),
    ProofRule(
        "test-failure-with-output",
        "A test result reports failure and includes captured output.",
        prove_test_failure,
    ),
    ProofRule(
        "verifier-rejection",
        "A verifier explicitly reports rejection with a reason or attempt.",
        prove_verifier_rejection,
    ),
    ProofRule(
        "parser-or-import-error",
        "Captured tool output contains a known parser or import diagnostic.",
        prove_parser_or_import_error,
    ),
    ProofRule(
        "edit-application-failure",
        "An edit operation reports failure with context for the failed application.",
        prove_edit_application_failure,
    ),
)


def apply_proof_rules(evidence: Iterable[DebugEvidence]) -> List[DebugFinding]:
    """Apply all deterministic rules without ranking or inferring root cause."""
    findings: List[DebugFinding] = []
    for item in evidence:
        for rule in PROOF_RULES:
            finding = rule.evaluate(item)
            if finding is not None:
                findings.append(finding)
    return findings
