"""Tests for rerun/checkpoint request serialization."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_rerun import RerunRequest, RerunScope


def test_request_round_trip_preserves_boundary_and_frozen_inputs():
    request = RerunRequest(
        request_id="rerun-request-1",
        source_session_id="session-1",
        scope=RerunScope.EVENT,
        boundary_id="event:42",
        reason="Validate whether the recorded parser failure reproduces.",
        frozen_input_refs=["debug/evidence.jsonl:8", "workspace:commit-abc"],
        workspace_ref="workspace:commit-abc",
        environment_ref="env:python-3.12-linux",
        required_capabilities=["local_process", "python"],
        attach_to_finding_ids=["finding:parser-or-import-error:e-1"],
        requested_at=100.0,
        metadata={"attempt": 1},
    )

    restored = RerunRequest.from_json(request.to_json())

    assert restored == request
    assert restored.scope is RerunScope.EVENT
    assert restored.boundary_id == "event:42"
    assert restored.frozen_input_refs == [
        "debug/evidence.jsonl:8",
        "workspace:commit-abc",
    ]


def test_request_supports_session_and_task_boundaries():
    for scope, boundary in [
        (RerunScope.SESSION, "session:1"),
        (RerunScope.TASK, "task:3"),
    ]:
        request = RerunRequest(
            source_session_id="session-1",
            scope=scope,
            boundary_id=boundary,
            reason="reproduce",
            frozen_input_refs=["input:1"],
        )
        assert RerunRequest.from_dict(request.to_dict()).scope is scope


def test_request_rejects_missing_reproduction_contract_fields():
    cases = [
        {"source_session_id": "session-1", "boundary_id": "task:1", "reason": "x", "frozen_input_refs": []},
        {"source_session_id": "", "boundary_id": "task:1", "reason": "x", "frozen_input_refs": ["input:1"]},
        {"source_session_id": "session-1", "boundary_id": "", "reason": "x", "frozen_input_refs": ["input:1"]},
    ]

    for fields in cases:
        try:
            RerunRequest(**fields)
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete rerun request must be rejected")
