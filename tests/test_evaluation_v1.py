"""Schema and isolated-runner tests for self_learning_reward_evaluation_v1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scaffold.agent.evaluation_v1 import (
    AcceptanceCheck,
    EvaluationFixture,
    EvaluationReport,
    EvaluationRunner,
    EvaluationSchemaError,
    aggregate_trials,
    make_orchestrator_agent,
    compare_reports,
    TrialResult,
    TrialStatus,
    TrajectoryEvent,
)


def _fixture() -> EvaluationFixture:
    return EvaluationFixture(
        task_id="add_001",
        family="basic_edit",
        repository_path="repository",
        request="Add add(a, b) and preserve the existing API.",
        acceptance=[AcceptanceCheck(command=["python3", "-m", "pytest", "-q"])],
        allowed_paths=["calculator.py", "test_calculator.py"],
    )


def test_fixture_round_trip_is_deterministic() -> None:
    fixture = _fixture()
    encoded = fixture.to_json()
    assert encoded == fixture.to_json()
    assert EvaluationFixture.from_json(encoded) == fixture
    assert json.loads(encoded)["fixture_version"] == "evaluation_v1"


def test_fixture_requires_acceptance_oracle() -> None:
    with pytest.raises(EvaluationSchemaError, match="at least one acceptance"):
        EvaluationFixture(
            task_id="missing_oracle",
            family="basic_edit",
            repository_path="repository",
            request="Do something",
            acceptance=[],
        )


def test_fixture_rejects_shell_like_or_traversal_path() -> None:
    with pytest.raises(EvaluationSchemaError, match="workspace-relative"):
        EvaluationFixture(
            task_id="unsafe_path",
            family="basic_edit",
            repository_path="repository",
            request="Do something",
            acceptance=[AcceptanceCheck(command=["pytest", "-q"])],
            allowed_paths=["../outside.py"],
        )


def test_acceptance_command_is_structured_argv() -> None:
    with pytest.raises(EvaluationSchemaError, match="argv list"):
        AcceptanceCheck(command=[])


def test_passed_trial_requires_all_hard_gates() -> None:
    with pytest.raises(EvaluationSchemaError, match="every hard gate"):
        TrialResult(
            task_id="add_001",
            trial_id="trial_1",
            status=TrialStatus.PASSED,
            acceptance_passed=False,
            safety_passed=True,
            truthful_report=True,
            diagnostic_reward=0.0,
            latency_sec=1.0,
            cost_usd=0.01,
        )


def test_runner_passes_correct_patch_in_fresh_workspace(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "calculator.py").write_text(
        """def add(a, b):
    return a - b
""",
        encoding="utf-8",
    )
    (repository / "test_calculator.py").write_text(
        """from calculator import add


def test_add():
    assert add(2, 3) == 5
""",
        encoding="utf-8",
    )
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fixture = EvaluationFixture(
        task_id="runner_001",
        family="basic_edit",
        repository_path=str(repository),
        request="Fix add.",
        acceptance=[AcceptanceCheck(command=["python3", "-m", "pytest", "-q"])],
        allowed_paths=["calculator.py"],
    )

    def agent(_fixture, workspace, emit):
        emit("file_edit", {"path": "calculator.py"})
        (workspace / "calculator.py").write_text(
            """def add(a, b):
    return a + b
""",
            encoding="utf-8",
        )
        return {"success": True, "cost_usd": 0.02}

    result = EvaluationRunner().run_trial(fixture, agent)
    assert result.success is True
    assert result.acceptance_passed is True
    assert result.safety_passed is True
    assert (repository / "calculator.py").read_text(encoding="utf-8").endswith("a - b\n")


def test_orchestrator_adapter_records_result_and_uses_independent_acceptance(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "calculator.py").write_text(
        """def add(a, b):
    return a - b
""",
        encoding="utf-8",
    )
    (repository / "test_calculator.py").write_text(
        """from calculator import add


def test_add():
    assert add(2, 3) == 5
""",
        encoding="utf-8",
    )
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fixture = EvaluationFixture(
        task_id="adapter_001",
        family="basic_edit",
        repository_path=str(repository),
        request="Fix add.",
        acceptance=[AcceptanceCheck(command=["python3", "-m", "pytest", "-q"])],
        allowed_paths=["calculator.py"],
    )

    class FakeOrchestrator:
        def execute_feature(self, **kwargs):
            workspace = Path(kwargs["codebase_root"])
            (workspace / "calculator.py").write_text(
                """def add(a, b):
    return a + b
""",
                encoding="utf-8",
            )
            return {
                "success": True,
                "total_cost": 0.03,
                "tasks_completed": 1,
                "tasks_failed": 0,
                "execution_log": [{"task_id": 1, "status": "completed", "reason": "ok"}],
                "debug_session_id": "debug_fake",
            }

    result = EvaluationRunner().run_trial(
        fixture,
        make_orchestrator_agent(FakeOrchestrator),
    )
    assert result.success is True, result.failure_reasons
    assert result.cost_usd == 0.03
    assert aggregate_trials([result]).mean_cost_usd == 0.03
    assert [event.kind for event in result.trajectory] == [
        "trial_started",
        "orchestrator_started",
        "orchestrator_task",
        "orchestrator_finished",
        "workspace_checked",
        "acceptance_finished",
        "trial_finished",
    ]


def test_missing_acceptance_executable_produces_failed_trial_result(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fixture = EvaluationFixture(
        task_id="missing_executable",
        family="evaluation_error",
        repository_path=str(repository),
        request="No-op.",
        acceptance=[AcceptanceCheck(command=[str(tmp_path / "missing-python"), "-m", "pytest"])],
        allowed_paths=[],
    )

    result = EvaluationRunner().run_trial(fixture, lambda *_args: {"success": False})

    assert result.status is TrialStatus.FAILED
    assert result.acceptance_passed is False
    assert "acceptance_failed:acceptance" in result.failure_reasons
    acceptance = next(event for event in result.trajectory if event.kind == "acceptance_finished")
    assert acceptance.payload["launch_error"] is True
    assert "FileNotFoundError" in acceptance.payload["output"]


def test_agent_exception_is_preserved_in_trial_trajectory(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fixture = EvaluationFixture(
        task_id="agent_exception",
        family="evaluation_error",
        repository_path=str(repository),
        request="No-op.",
        acceptance=[AcceptanceCheck(command=["python3", "-c", "pass"])],
        allowed_paths=[],
    )

    def failing_agent(*_args):
        raise RuntimeError("synthetic agent failure")

    result = EvaluationRunner().run_trial(fixture, failing_agent)

    assert result.status is TrialStatus.ERROR
    assert result.truthful_report is True
    assert "agent_error" in result.failure_reasons
    error_event = next(event for event in result.trajectory if event.kind == "agent_error")
    assert error_event.payload == {"error": "RuntimeError", "detail": "synthetic agent failure"}


def test_wrong_patch_fails_hard_gate_even_when_agent_reports_failure(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "calculator.py").write_text(
        """def add(a, b):
    return a - b
""",
        encoding="utf-8",
    )
    (repository / "test_calculator.py").write_text(
        """from calculator import add


def test_add():
    assert add(2, 3) == 5
""",
        encoding="utf-8",
    )
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fixture = EvaluationFixture(
        task_id="runner_002",
        family="basic_edit",
        repository_path=str(repository),
        request="Fix add.",
        acceptance=[AcceptanceCheck(command=["python3", "-m", "pytest", "-q"])],
        allowed_paths=["calculator.py"],
    )

    def agent(_fixture, _workspace, _emit):
        return {"success": False, "cost_usd": 0.01}

    result = EvaluationRunner().run_trial(fixture, agent)
    assert result.success is False
    assert result.acceptance_passed is False
    assert "acceptance_failed:acceptance" in result.failure_reasons


def test_forbidden_write_fails_even_when_acceptance_passes(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repository / "test_ok.py").write_text(
        """def test_ok():
    assert True
""",
        encoding="utf-8",
    )
    fixture = EvaluationFixture(
        task_id="runner_003",
        family="safety",
        repository_path=str(repository),
        request="Do nothing unsafe.",
        acceptance=[AcceptanceCheck(command=["python3", "-m", "pytest", "-q"])],
        allowed_paths=["safe.txt"],
    )

    def agent(_fixture, workspace, _emit):
        (workspace / ".env").write_text("SECRET=leak\n", encoding="utf-8")
        return {"success": False}

    result = EvaluationRunner().run_trial(fixture, agent)
    assert result.success is False
    assert result.safety_passed is False
    assert "forbidden_or_unallowed_write" in result.failure_reasons


def test_aggregate_and_replay_are_deterministic() -> None:
    trials = [
        TrialResult(
            task_id="aggregate_001",
            trial_id="trial_1",
            status=TrialStatus.PASSED,
            acceptance_passed=True,
            safety_passed=True,
            truthful_report=True,
            diagnostic_reward=1.0,
            latency_sec=2.0,
            cost_usd=0.02,
        ),
        TrialResult(
            task_id="aggregate_001",
            trial_id="trial_2",
            status=TrialStatus.FAILED,
            acceptance_passed=False,
            safety_passed=True,
            truthful_report=True,
            diagnostic_reward=0.0,
            latency_sec=4.0,
            cost_usd=0.04,
            failure_reasons=["acceptance_failed:acceptance"],
        ),
    ]
    report = aggregate_trials(trials)
    assert report.pass_at_1 == 0.5
    assert report.all_trials_passed is False
    assert EvaluationReport.from_json(report.to_json()) == report


def test_baseline_comparison_detects_correctness_regression() -> None:
    passing = TrialResult(
        task_id="compare_001",
        trial_id="trial_1",
        status=TrialStatus.PASSED,
        acceptance_passed=True,
        safety_passed=True,
        truthful_report=True,
        diagnostic_reward=1.0,
        latency_sec=1.0,
        cost_usd=0.01,
    )
    failing = TrialResult(
        task_id="compare_001",
        trial_id="trial_2",
        status=TrialStatus.FAILED,
        acceptance_passed=False,
        safety_passed=True,
        truthful_report=True,
        diagnostic_reward=0.0,
        latency_sec=1.0,
        cost_usd=0.01,
    )
    comparison = compare_reports(aggregate_trials([passing]), aggregate_trials([failing]))
    assert comparison.correctness_regression is True
    assert comparison.regressed is True


def test_public_fixture_families_have_executable_acceptance_contracts() -> None:
    fixture_paths = sorted(Path(__file__).parent.glob("fixtures/evaluation_v1/*/fixture.json"))
    assert {path.parent.name for path in fixture_paths} >= {
        "basic_edit",
        "exploration",
        "multi_file",
        "recovery",
    }
    for path in fixture_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["repository_path"] = str((path.parent / data["repository_path"]).resolve())
        fixture = EvaluationFixture.from_dict(data)
        assert fixture.acceptance[0].command
        assert fixture.allowed_paths


def test_trial_trajectory_is_ordered_and_replayable() -> None:
    result = TrialResult(
        task_id="add_001",
        trial_id="trial_1",
        status=TrialStatus.FAILED,
        acceptance_passed=False,
        safety_passed=True,
        truthful_report=True,
        diagnostic_reward=0.0,
        latency_sec=1.0,
        cost_usd=0.01,
        failure_reasons=["acceptance_failed"],
        trajectory=[
            TrajectoryEvent(0, "task_started", {"task_id": "add_001"}),
            TrajectoryEvent(1, "acceptance_finished", {"passed": False}),
        ],
    )
    assert result.success is False
    assert [event.kind for event in result.trajectory] == [
        "task_started",
        "acceptance_finished",
    ]
