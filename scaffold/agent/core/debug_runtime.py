"""Integration helpers for real AWOS verifier and test-runner results."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from ..test_runner import TestResult
from .debug_adapters import _timestamp_to_epoch
from .debug_rules import apply_proof_rules
from .debug_schema import DebugEvidence, EvidenceKind


def _record_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    if is_dataclass(result):
        return asdict(result)
    raise TypeError(f"Unsupported runtime result type: {type(result).__name__}")


def evidence_from_verifier_result(
    result: Mapping[str, Any],
    *,
    file_path: str,
    session_id: str = "",
    task_id: str = "",
    attempt: int = 1,
    source_ref: Optional[str] = None,
) -> DebugEvidence:
    """Convert one real Verifier result dictionary into direct evidence."""
    errors = list(result.get("errors") or [])
    error_context = str(result.get("error_context") or "")
    failure_reason = str(errors[0]) if errors else error_context
    payload: Dict[str, Any] = {
        "record_type": "verifier_result",
        "session_id": session_id,
        "task_id": task_id,
        "file_path": file_path,
        "attempt": attempt,
        "success": bool(result.get("success")),
        "applied": bool(result.get("applied")),
        "verify_passed": bool(result.get("success")) and bool(result.get("applied")),
        "needs_retry": bool(result.get("needs_retry")),
        "fidelity_fail": bool(result.get("fidelity_fail")),
        "errors": errors,
        "error": failure_reason,
        "failure_reason": failure_reason,
        "error_context": error_context,
        "edit_status": "success" if result.get("success") else "failed",
    }
    # Keep structural edit information, not full file contents, in evidence.
    payload["search_present"] = bool(result.get("search"))
    payload["replace_present"] = bool(result.get("replace"))
    event_id = f"verifier:{session_id}:{task_id}:{attempt}:{file_path}"
    return DebugEvidence(
        evidence_id=event_id,
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="verifier",
        observed_at=0.0,
        source_ref=source_ref or file_path,
        event_id=event_id,
        payload=payload,
    )


def evidence_from_test_result(
    result: TestResult | Mapping[str, Any],
    *,
    session_id: str = "",
    task_id: str = "",
    source_ref: Optional[str] = None,
) -> DebugEvidence:
    """Convert a real TestRunner result into direct evidence."""
    payload = _record_dict(result)
    failed = int(payload.get("failed", 0) or 0)
    errors = int(payload.get("errors", 0) or 0)
    no_tests_found = bool(payload.get("no_tests_found"))
    timed_out = bool(payload.get("timed_out"))
    payload.update(
        {
            "record_type": "test_result",
            "normalized_event_type": "test_result",
            "session_id": session_id,
            "task_id": task_id,
            "tests_failed": failed,
            "tests_passed": int(payload.get("passed", 0) or 0),
            "passed": failed == 0 and errors == 0 and not timed_out and not no_tests_found,
            "output": str(payload.get("raw_output") or ""),
            "test_command": list(payload.get("test_command") or []),
        }
    )
    event_id = f"test:{session_id}:{task_id}"
    return DebugEvidence(
        evidence_id=event_id,
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="test_runner",
        observed_at=0.0,
        source_ref=source_ref or ".awos/test-results",
        event_id=event_id,
        payload=payload,
        valid=bool(payload.get("output")) or failed > 0 or errors > 0,
    )


def findings_from_verifier_result(
    result: Mapping[str, Any],
    *,
    file_path: str,
    session_id: str = "",
    task_id: str = "",
    attempt: int = 1,
    source_ref: Optional[str] = None,
):
    """Convert a real verifier result and return its bounded findings."""
    evidence = evidence_from_verifier_result(
        result,
        file_path=file_path,
        session_id=session_id,
        task_id=task_id,
        attempt=attempt,
        source_ref=source_ref,
    )
    return evidence, apply_proof_rules([evidence])


def findings_from_test_result(
    result: TestResult | Mapping[str, Any],
    *,
    session_id: str = "",
    task_id: str = "",
    source_ref: Optional[str] = None,
):
    """Convert a real test-runner result and return its bounded findings."""
    evidence = evidence_from_test_result(
        result,
        session_id=session_id,
        task_id=task_id,
        source_ref=source_ref,
    )
    return evidence, apply_proof_rules([evidence])
