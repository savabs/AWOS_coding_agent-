"""Resolve findings against rerun outcomes without rewriting evidence history."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from .debug_report import RerunOutcome, RerunStatus
from .debug_schema import ClaimType, DebugFinding, FailureStage, FindingStatus


class ResolutionAction(str, Enum):
    PRESERVED_PROVEN = "PRESERVED_PROVEN"
    UPGRADED_REPRODUCED = "UPGRADED_REPRODUCED"
    REPRODUCED_CAUSE_UNPROVEN = "REPRODUCED_CAUSE_UNPROVEN"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    DIVERGENT = "DIVERGENT"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


@dataclass
class FindingResolution:
    """Append-only decision record linking a finding to one rerun outcome."""

    resolution_id: str
    original_finding_id: str
    rerun_id: str
    action: ResolutionAction
    effective_status: FindingStatus
    explanation: str
    replacement_finding: Optional[DebugFinding] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["action"] = self.action.value
        result["effective_status"] = self.effective_status.value
        if self.replacement_finding is not None:
            result["replacement_finding"] = self.replacement_finding.to_dict()
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def resolve_finding_after_rerun(
    finding: DebugFinding, outcome: RerunOutcome
) -> FindingResolution:
    """Return an append-only resolution; never mutate the original finding."""
    base = {
        "resolution_id": f"resolution:{finding.finding_id}:{outcome.rerun_id}",
        "original_finding_id": finding.finding_id,
        "rerun_id": outcome.rerun_id,
        "metadata": {"rerun_status": outcome.status.value},
    }

    if finding.status is FindingStatus.PROVEN:
        return FindingResolution(
            **base,
            action=ResolutionAction.PRESERVED_PROVEN,
            effective_status=FindingStatus.PROVEN,
            explanation="The original proven observation is preserved; rerun evidence is additive.",
        )

    if outcome.status is RerunStatus.REPRODUCED:
        if finding.claim_type is ClaimType.ROOT_CAUSE:
            return FindingResolution(
                **base,
                action=ResolutionAction.REPRODUCED_CAUSE_UNPROVEN,
                effective_status=finding.status,
                explanation="The failure reproduced, but reproduction alone does not prove the root cause.",
            )
        if not outcome.evidence_ids:
            return FindingResolution(
                **base,
                action=ResolutionAction.INCONCLUSIVE,
                effective_status=finding.status,
                explanation="Rerun reported reproduction without proof evidence references.",
            )
        replacement = DebugFinding(
            finding_id=f"finding:rerun-reproduction:{finding.finding_id}:{outcome.rerun_id}",
            claim_type=finding.claim_type,
            subject=finding.subject,
            status=FindingStatus.PROVEN,
            title="Observation reproduced",
            conclusion=finding.conclusion,
            evidence_ids=list(dict.fromkeys(finding.evidence_ids + outcome.evidence_ids)),
            proof_rule_id="rerun-reproduction",
            proof_rule_version="1.0",
            failure_stage=FailureStage.RERUN,
            source_run_id=outcome.rerun_id,
            related_finding_ids=[finding.finding_id],
            metadata={"original_status": finding.status.value},
        )
        return FindingResolution(
            **base,
            action=ResolutionAction.UPGRADED_REPRODUCED,
            effective_status=FindingStatus.PROVEN,
            explanation="The bounded observation reproduced with recorded rerun evidence.",
            replacement_finding=replacement,
        )

    action_by_status = {
        RerunStatus.NOT_REPRODUCED: ResolutionAction.NOT_REPRODUCED,
        RerunStatus.DIVERGENT: ResolutionAction.DIVERGENT,
        RerunStatus.INCONCLUSIVE: ResolutionAction.INCONCLUSIVE,
        RerunStatus.ERROR: ResolutionAction.ERROR,
    }
    action = action_by_status[outcome.status]
    return FindingResolution(
        **base,
        action=action,
        effective_status=finding.status,
        explanation=(
            "The rerun did not establish the claim; the original finding remains "
            f"{finding.status.value} and the rerun result is preserved."
        ),
    )
