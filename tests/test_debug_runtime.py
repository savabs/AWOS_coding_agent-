"""Integration tests for real verifier and test-runner result shapes."""

import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_runtime import (
    evidence_from_test_result,
    findings_from_test_result,
    findings_from_verifier_result,
)
from scaffold.agent.core.debug_schema import FailureStage, FindingStatus
from scaffold.agent.test_runner import TestResult
from scaffold.agent.verifier import Verifier


def test_real_verifier_failure_becomes_evidence_linked_proof(tmp_path: Path):
    file_path = tmp_path / "module.py"
    file_path.write_text("value = 1\n")
    result = Verifier().verify_and_apply(
        {"search": "value = 1", "replace": "value ="},
        str(file_path),
    )

    evidence, findings = findings_from_verifier_result(
        result,
        file_path=str(file_path),
        session_id="session-1",
        task_id="task-1",
        attempt=1,
        source_ref="verifier-result:1",
    )

    assert result["success"] is False
    assert evidence.source == "verifier"
    assert evidence.source_ref == "verifier-result:1"
    assert findings
    assert all(finding.status is FindingStatus.PROVEN for finding in findings)
    assert all(evidence.evidence_id in finding.evidence_ids for finding in findings)
    assert any(finding.failure_stage is FailureStage.PARSING for finding in findings)


def test_real_test_runner_result_becomes_test_failure_proof():
    result = TestResult(
        passed=2,
        failed=1,
        errors=0,
        pass_rate=2 / 3,
        raw_output="FAILED test_api.py::test_login - AssertionError\n1 failed, 2 passed",
        test_command=["pytest", "-q"],
    )

    evidence, findings = findings_from_test_result(
        result,
        session_id="session-2",
        task_id="task-2",
        source_ref="test-run:4",
    )

    assert evidence.source == "test_runner"
    assert evidence.payload["tests_failed"] == 1
    assert evidence.payload["passed"] is False
    assert len(findings) == 1
    assert findings[0].proof_rule_id == "test-failure-with-output"
    assert findings[0].evidence_ids == [evidence.evidence_id]


def test_test_runner_no_tests_is_not_proven_failure():
    result = TestResult(
        raw_output="No test framework detected.",
        no_tests_found=True,
    )

    evidence, findings = findings_from_test_result(result)

    assert evidence.payload["passed"] is False
    assert findings == []


def test_runtime_bridge_rejects_unsupported_result_types():
    try:
        evidence_from_test_result(cast(Any, object()))
    except TypeError as exc:
        assert "Unsupported runtime result type" in str(exc)
    else:
        raise AssertionError("unsupported runtime results must be rejected")
