"""Canonical task and session reports for the evidence-first debugger."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .debug_schema import DebugFinding, _enum_value


class ReportScope(str, Enum):
    """The execution boundary summarized by a report."""

    TASK = "TASK"
    SESSION = "SESSION"


class RerunStatus(str, Enum):
    """Outcome of a validation run attached to a report."""

    REPRODUCED = "REPRODUCED"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    DIVERGENT = "DIVERGENT"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


@dataclass
class CauseChain:
    """An explicitly linked chain of findings used for investigation context."""

    chain_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    finding_ids: List[str] = field(default_factory=list)
    relationship: str = ""
    evidence_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.chain_id:
            raise ValueError("chain_id must not be empty")
        if len(self.finding_ids) < 2:
            raise ValueError("cause chain requires at least two finding_ids")
        if not self.relationship:
            raise ValueError("relationship must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CauseChain":
        return cls(**dict(data))


@dataclass
class RerunOutcome:
    """A preserved validation result; it never overwrites prior findings."""

    rerun_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: RerunStatus = RerunStatus.INCONCLUSIVE
    source_finding_id: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.status = _enum_value(RerunStatus, self.status)
        if not self.rerun_id:
            raise ValueError("rerun_id must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RerunOutcome":
        values = dict(data)
        values["status"] = _enum_value(RerunStatus, values.get("status"))
        return cls(**values)


@dataclass
class DebugReport:
    """Deterministically serializable report for one task or session."""

    report_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    scope: ReportScope = ReportScope.SESSION
    session_id: str = ""
    task_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    findings: List[DebugFinding] = field(default_factory=list)
    artifact_refs: List[str] = field(default_factory=list)
    cause_chains: List[CauseChain] = field(default_factory=list)
    rerun_outcomes: List[RerunOutcome] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.scope = _enum_value(ReportScope, self.scope)
        if not self.report_id:
            raise ValueError("report_id must not be empty")
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if self.scope is ReportScope.TASK and not self.task_id:
            raise ValueError("TASK report requires task_id")
        self.findings = [
            finding if isinstance(finding, DebugFinding)
            else DebugFinding.from_dict(finding)
            for finding in self.findings
        ]
        self.cause_chains = [
            chain if isinstance(chain, CauseChain)
            else CauseChain.from_dict(chain)
            for chain in self.cause_chains
        ]
        self.rerun_outcomes = [
            outcome if isinstance(outcome, RerunOutcome)
            else RerunOutcome.from_dict(outcome)
            for outcome in self.rerun_outcomes
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "scope": self.scope.value,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "findings": [finding.to_dict() for finding in self.findings],
            "artifact_refs": list(self.artifact_refs),
            "cause_chains": [chain.to_dict() for chain in self.cause_chains],
            "rerun_outcomes": [
                outcome.to_dict() for outcome in self.rerun_outcomes
            ],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DebugReport":
        values = dict(data)
        values["scope"] = _enum_value(ReportScope, values.get("scope"))
        values["findings"] = [
            DebugFinding.from_dict(finding) for finding in values.get("findings", [])
        ]
        values["cause_chains"] = [
            CauseChain.from_dict(chain)
            for chain in values.get("cause_chains", [])
        ]
        values["rerun_outcomes"] = [
            RerunOutcome.from_dict(outcome)
            for outcome in values.get("rerun_outcomes", [])
        ]
        return cls(**values)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "DebugReport":
        return cls.from_dict(json.loads(value))
