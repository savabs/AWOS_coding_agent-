"""Canonical evidence-first debugger schema.

This module defines the small, serializable vocabulary shared by debugger
producers, proof rules, reports, and persistence.  It deliberately contains
no attribution or detection heuristics: those belong in later layers.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar


class _StringEnum(str, Enum):
    """Enum base that serializes naturally as its stable string value."""

    def __str__(self) -> str:
        return self.value


class FindingStatus(_StringEnum):
    """Truth class for one bounded debugger claim."""

    PROVEN = "PROVEN"
    HYPOTHESIS = "HYPOTHESIS"
    UNKNOWN = "UNKNOWN"


class ClaimType(_StringEnum):
    """Scope of the claim made by a finding."""

    OBSERVATION = "OBSERVATION"
    DIAGNOSIS = "DIAGNOSIS"
    ROOT_CAUSE = "ROOT_CAUSE"
    REMEDIATION = "REMEDIATION"


class EvidenceKind(_StringEnum):
    """Evidence class used by the truth contract."""

    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"
    DERIVED_PROOF = "DERIVED_PROOF"
    RERUN_REPRODUCTION = "RERUN_REPRODUCTION"
    ATTRIBUTION_SIGNAL = "ATTRIBUTION_SIGNAL"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class FailureStage(_StringEnum):
    """Execution stage associated with an evidence item or finding."""

    SESSION = "SESSION"
    PLANNING = "PLANNING"
    ROUTING = "ROUTING"
    EXECUTION = "EXECUTION"
    EDIT = "EDIT"
    PARSING = "PARSING"
    VERIFICATION = "VERIFICATION"
    TEST = "TEST"
    RERUN = "RERUN"
    UNKNOWN = "UNKNOWN"


EnumT = TypeVar("EnumT", bound=Enum)


def _enum_value(enum_type: Type[EnumT], value: Any) -> EnumT:
    """Convert a serialized enum value and fail clearly on unknown values."""
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {enum_type.__name__} value: {value!r}") from exc


def _enum_or_none(enum_type: Type[EnumT], value: Any) -> Optional[EnumT]:
    if value is None:
        return None
    return _enum_value(enum_type, value)


@dataclass
class DebugEvidence:
    """One source-backed evidence item in the debugger evidence graph."""

    evidence_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    kind: EvidenceKind = EvidenceKind.EVIDENCE_GAP
    source: str = ""
    observed_at: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)
    source_ref: Optional[str] = None
    artifact_hash: Optional[str] = None
    event_id: Optional[str] = None
    parent_evidence_ids: List[str] = field(default_factory=list)
    valid: bool = True

    def __post_init__(self) -> None:
        self.kind = _enum_value(EvidenceKind, self.kind)
        if not self.evidence_id:
            raise ValueError("evidence_id must not be empty")
        if not self.source:
            raise ValueError("source must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DebugEvidence":
        values = dict(data)
        values["kind"] = _enum_value(EvidenceKind, values.get("kind"))
        return cls(**values)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "DebugEvidence":
        return cls.from_dict(json.loads(value))


@dataclass
class DebugFinding:
    """A claim whose truth status is bounded by named evidence and rules."""

    finding_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    claim_type: ClaimType = ClaimType.OBSERVATION
    subject: str = ""
    status: FindingStatus = FindingStatus.UNKNOWN
    title: str = ""
    conclusion: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    proof_rule_id: Optional[str] = None
    proof_rule_version: Optional[str] = None
    failure_stage: FailureStage = FailureStage.UNKNOWN
    source_run_id: Optional[str] = None
    attribution_score: Optional[float] = None
    related_finding_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.claim_type = _enum_value(ClaimType, self.claim_type)
        self.status = _enum_value(FindingStatus, self.status)
        self.failure_stage = _enum_value(FailureStage, self.failure_stage)
        if not self.finding_id:
            raise ValueError("finding_id must not be empty")
        if not self.subject:
            raise ValueError("subject must not be empty")
        self.validate()

    def validate(self) -> None:
        """Enforce schema-level truth invariants, not proof-rule semantics."""
        if self.status is FindingStatus.PROVEN:
            missing = []
            if not self.evidence_ids:
                missing.append("evidence_ids")
            if not self.proof_rule_id:
                missing.append("proof_rule_id")
            if not self.proof_rule_version:
                missing.append("proof_rule_version")
            if missing:
                raise ValueError(
                    "PROVEN finding requires: " + ", ".join(missing)
                )

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["claim_type"] = self.claim_type.value
        result["status"] = self.status.value
        result["failure_stage"] = self.failure_stage.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DebugFinding":
        values = dict(data)
        values["claim_type"] = _enum_value(ClaimType, values.get("claim_type"))
        values["status"] = _enum_value(FindingStatus, values.get("status"))
        values["failure_stage"] = _enum_value(
            FailureStage, values.get("failure_stage")
        )
        return cls(**values)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "DebugFinding":
        return cls.from_dict(json.loads(value))
