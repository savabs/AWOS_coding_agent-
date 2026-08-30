"""Serializable requests for controlled debugger reruns."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RerunScope(str, Enum):
    SESSION = "SESSION"
    TASK = "TASK"
    EVENT = "EVENT"


@dataclass
class RerunRequest:
    """A reproducibility request; execution is handled by a later layer."""

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source_session_id: str = ""
    scope: RerunScope = RerunScope.TASK
    boundary_id: str = ""
    reason: str = ""
    frozen_input_refs: List[str] = field(default_factory=list)
    workspace_ref: Optional[str] = None
    environment_ref: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    attach_to_finding_ids: List[str] = field(default_factory=list)
    requested_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.scope, str):
            self.scope = RerunScope(self.scope)
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if not self.source_session_id:
            raise ValueError("source_session_id must not be empty")
        if not self.boundary_id:
            raise ValueError("boundary_id must not be empty")
        if not self.reason:
            raise ValueError("reason must not be empty")
        if not self.frozen_input_refs:
            raise ValueError("frozen_input_refs must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["scope"] = self.scope.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RerunRequest":
        values = dict(data)
        values["scope"] = RerunScope(values.get("scope"))
        return cls(**values)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "RerunRequest":
        return cls.from_dict(json.loads(value))
