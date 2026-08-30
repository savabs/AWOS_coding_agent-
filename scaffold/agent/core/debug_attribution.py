"""Attribution-only integration for local fault localization."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from ..fault_localizer import FaultLocalizer, LocalizedFault
from .debug_schema import ClaimType, DebugFinding, FindingStatus


def _safe_confidence(value: float) -> float:
    """Keep ranking metadata bounded and deterministic."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(confidence):
        return 0.0
    return min(1.0, max(0.0, confidence))


def localized_fault_to_hypothesis(
    fault: LocalizedFault,
    *,
    evidence_ids: Optional[Sequence[str]] = None,
    source_run_id: Optional[str] = None,
) -> DebugFinding:
    """Convert one localizer result into a non-proof root-cause hypothesis."""
    confidence = _safe_confidence(fault.confidence)
    location = f"{fault.file_path}:{fault.start_line}-{fault.end_line}"
    symbol = f"::{fault.symbol_name}" if fault.symbol_name else ""
    subject = f"{location}{symbol} may be involved in the failure."
    return DebugFinding(
        finding_id=(
            f"hypothesis:localizer:{fault.file_path}:"
            f"{fault.symbol_name}:{fault.start_line}:{fault.end_line}"
        ),
        claim_type=ClaimType.ROOT_CAUSE,
        subject=subject,
        status=FindingStatus.HYPOTHESIS,
        title="Potential fault location",
        conclusion=subject,
        evidence_ids=list(evidence_ids or []),
        source_run_id=source_run_id,
        attribution_score=confidence,
        metadata={
            "attribution_source": "FaultLocalizer",
            "file_path": fault.file_path,
            "symbol_name": fault.symbol_name,
            "start_line": fault.start_line,
            "end_line": fault.end_line,
            "localizer_reason": fault.reason,
        },
    )


def hypotheses_from_localized_faults(
    faults: Sequence[LocalizedFault],
    *,
    evidence_ids: Optional[Sequence[str]] = None,
    source_run_id: Optional[str] = None,
) -> List[DebugFinding]:
    """Create stable, confidence-ranked hypotheses without proof escalation."""
    findings = [
        localized_fault_to_hypothesis(
            fault,
            evidence_ids=evidence_ids,
            source_run_id=source_run_id,
        )
        for fault in faults
    ]
    return sorted(
        findings,
        key=lambda finding: (
            -(finding.attribution_score or 0.0),
            finding.metadata.get("file_path", ""),
            finding.metadata.get("symbol_name", ""),
            finding.metadata.get("start_line", 0),
        ),
    )


def localize_to_hypotheses(
    localizer: FaultLocalizer,
    *,
    task_description: str,
    error_trace: str = "",
    project_root: str = ".",
    recently_modified: Optional[List[str]] = None,
    evidence_ids: Optional[Sequence[str]] = None,
    source_run_id: Optional[str] = None,
) -> List[DebugFinding]:
    """Run the local-only localizer and convert results to hypotheses."""
    faults = localizer.localize(
        task_description=task_description,
        error_trace=error_trace,
        project_root=project_root,
        recently_modified=recently_modified,
    )
    return hypotheses_from_localized_faults(
        faults,
        evidence_ids=evidence_ids,
        source_run_id=source_run_id,
    )
