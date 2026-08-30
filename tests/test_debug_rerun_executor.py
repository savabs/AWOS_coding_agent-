"""Tests for safe local controlled reruns."""

import sys
from pathlib import Path

from scaffold.agent.core.debug_journal import DebugJournal
from scaffold.agent.core.debug_report import RerunStatus
from scaffold.agent.core.debug_rerun import RerunRequest
from scaffold.agent.core.debug_rerun_executor import (
    ControlledRerunExecutor,
    default_environment_ref,
)


def _request(tmp_path: Path, *, expected: int | None, command=None, **metadata):
    values = {
        "source_session_id": "session-rerun",
        "boundary_id": "task:1",
        "reason": "validate the recorded local result",
        "frozen_input_refs": ["fixture:test.py"],
        "workspace_ref": str(tmp_path),
        "environment_ref": default_environment_ref(),
        "metadata": {
            "command": command or [sys.executable, "-m", "pytest", "test_sample.py", "-q"],
            "expected_exit_code": expected,
            **metadata,
        },
    }
    return RerunRequest(**values)


def test_reproduced_failure_persists_evidence_findings_and_outcome(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_failure():\n    assert 1 == 2\n", encoding="utf-8"
    )
    journal = DebugJournal(tmp_path / "debug", "session-rerun")

    outcome = ControlledRerunExecutor(tmp_path).execute(
        _request(tmp_path, expected=1), journal=journal
    )

    assert outcome.status is RerunStatus.REPRODUCED
    assert outcome.evidence_ids
    assert journal.read_reruns()[0].status is RerunStatus.REPRODUCED
    assert journal.read_evidence()[0].payload["returncode"] == 1
    assert any(finding.proof_rule_id == "process-nonzero-exit" for finding in journal.read_findings())


def test_changed_exit_code_is_not_reproduced(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_success():\n    assert 1 == 1\n", encoding="utf-8"
    )

    outcome = ControlledRerunExecutor(tmp_path).execute(_request(tmp_path, expected=1))

    assert outcome.status is RerunStatus.NOT_REPRODUCED


def test_environment_mismatch_is_divergent_and_does_not_run(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")

    request = _request(tmp_path, expected=0)
    request.environment_ref = "different-environment"
    outcome = ControlledRerunExecutor(tmp_path).execute(request)

    assert outcome.status is RerunStatus.DIVERGENT
    assert "environment" in outcome.summary


def test_disallowed_shell_command_is_rejected(tmp_path: Path):
    request = _request(tmp_path, expected=0, command=["sh", "-c", "exit 0"])

    outcome = ControlledRerunExecutor(tmp_path).execute(request)

    assert outcome.status is RerunStatus.ERROR
    assert "allowlisted" in outcome.summary


def test_workspace_escape_is_rejected(tmp_path: Path):
    request = _request(tmp_path, expected=0)
    request.workspace_ref = str(tmp_path.parent)

    outcome = ControlledRerunExecutor(tmp_path).execute(request)

    assert outcome.status is RerunStatus.ERROR
    assert "inside executor root" in outcome.summary
