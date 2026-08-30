"""Versioned schemas for the deterministic coding-agent evaluation harness.

This module is intentionally execution-free. It defines the contract shared by
fixture loaders, trial runners, graders, replay, and report consumers. The
contract fails closed: a fixture without an executable acceptance oracle is not
valid, and paths/commands must be represented in safe structured forms.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

SCHEMA_VERSION = "evaluation_v1"


class EvaluationSchemaError(ValueError):
    """Raised when an evaluation fixture or report violates its contract."""


class TrialStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True)
class AcceptanceCheck:
    """One executable acceptance command owned by the evaluator."""

    command: List[str]
    timeout_sec: int = 60
    required: bool = True
    name: str = "acceptance"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise EvaluationSchemaError("acceptance check name must not be empty")
        if not self.command or any(not isinstance(token, str) or not token for token in self.command):
            raise EvaluationSchemaError("acceptance command must be a non-empty argv list")
        if self.timeout_sec <= 0:
            raise EvaluationSchemaError("acceptance timeout must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AcceptanceCheck":
        if not isinstance(data, dict):
            raise EvaluationSchemaError("acceptance check must be an object")
        return cls(
            command=list(data.get("command", [])),
            timeout_sec=int(data.get("timeout_sec", 60)),
            required=bool(data.get("required", True)),
            name=str(data.get("name", "acceptance")),
        )


@dataclass(frozen=True)
class EvaluationFixture:
    """Public task metadata plus evaluator-owned correctness constraints."""

    task_id: str
    family: str
    repository_path: str
    request: str
    acceptance: List[AcceptanceCheck]
    allowed_paths: List[str] = field(default_factory=list)
    forbidden_paths: List[str] = field(default_factory=lambda: [".git", ".env"])
    human_minutes: Optional[float] = None
    fixture_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.fixture_version != SCHEMA_VERSION:
            raise EvaluationSchemaError(
                f"unsupported fixture schema: {self.fixture_version!r}"
            )
        for name, value in (("task_id", self.task_id), ("family", self.family), ("request", self.request)):
            if not isinstance(value, str) or not value.strip():
                raise EvaluationSchemaError(f"{name} must not be empty")
        if not isinstance(self.repository_path, str) or not self.repository_path.strip():
            raise EvaluationSchemaError("repository_path must not be empty")
        if not self.acceptance:
            raise EvaluationSchemaError("fixture requires at least one acceptance check")
        _validate_relative_paths("allowed_paths", self.allowed_paths)
        _validate_relative_paths("forbidden_paths", self.forbidden_paths)
        if self.human_minutes is not None and self.human_minutes <= 0:
            raise EvaluationSchemaError("human_minutes must be positive when provided")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["acceptance"] = [check.to_dict() for check in self.acceptance]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvaluationFixture":
        if not isinstance(data, dict):
            raise EvaluationSchemaError("fixture must be an object")
        return cls(
            task_id=str(data.get("task_id", "")),
            family=str(data.get("family", "")),
            repository_path=str(data.get("repository_path", "")),
            request=str(data.get("request", "")),
            acceptance=[AcceptanceCheck.from_dict(item) for item in data.get("acceptance", [])],
            allowed_paths=[str(item) for item in data.get("allowed_paths", [])],
            forbidden_paths=[str(item) for item in data.get("forbidden_paths", [".git", ".env"])],
            human_minutes=(float(data["human_minutes"]) if data.get("human_minutes") is not None else None),
            fixture_version=str(data.get("fixture_version", SCHEMA_VERSION)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "EvaluationFixture":
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise EvaluationSchemaError("fixture JSON is invalid") from exc
        return cls.from_dict(data)


@dataclass(frozen=True)
class TrajectoryEvent:
    """One ordered, replayable observation from a trial."""

    sequence: int
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise EvaluationSchemaError("trajectory sequence must be non-negative")
        if not self.kind.strip():
            raise EvaluationSchemaError("trajectory event kind must not be empty")


@dataclass(frozen=True)
class TrialResult:
    """Immutable result of one fixture attempt."""

    task_id: str
    trial_id: str
    status: TrialStatus
    acceptance_passed: bool
    safety_passed: bool
    truthful_report: bool
    diagnostic_reward: float
    latency_sec: float
    cost_usd: float
    retries: int = 0
    failure_reasons: List[str] = field(default_factory=list)
    trajectory: List[TrajectoryEvent] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", TrialStatus(self.status))
        if self.schema_version != SCHEMA_VERSION:
            raise EvaluationSchemaError(f"unsupported report schema: {self.schema_version!r}")
        if not self.task_id.strip() or not self.trial_id.strip():
            raise EvaluationSchemaError("trial identity must not be empty")
        if self.latency_sec < 0 or self.cost_usd < 0 or self.retries < 0:
            raise EvaluationSchemaError("latency, cost, and retries must not be negative")
        if self.status is TrialStatus.PASSED and not (
            self.acceptance_passed and self.safety_passed and self.truthful_report
        ):
            raise EvaluationSchemaError("passed trial must satisfy every hard gate")
        if self.status is TrialStatus.FAILED and self.acceptance_passed and self.safety_passed and self.truthful_report:
            raise EvaluationSchemaError("fully passing trial must have passed status")
        sequences = [event.sequence for event in self.trajectory]
        if sequences != sorted(set(sequences)):
            raise EvaluationSchemaError("trajectory sequences must be unique and ordered")

    @property
    def success(self) -> bool:
        return self.status is TrialStatus.PASSED

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrialResult":
        if not isinstance(data, dict):
            raise EvaluationSchemaError("trial result must be an object")
        trajectory = [
            TrajectoryEvent(
                sequence=int(item["sequence"]),
                kind=str(item["kind"]),
                payload=dict(item.get("payload", {})),
            )
            for item in data.get("trajectory", [])
        ]
        return cls(
            task_id=str(data.get("task_id", "")),
            trial_id=str(data.get("trial_id", "")),
            status=TrialStatus(data.get("status", TrialStatus.ERROR.value)),
            acceptance_passed=bool(data.get("acceptance_passed", False)),
            safety_passed=bool(data.get("safety_passed", False)),
            truthful_report=bool(data.get("truthful_report", False)),
            diagnostic_reward=float(data.get("diagnostic_reward", 0.0)),
            latency_sec=float(data.get("latency_sec", 0.0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            retries=int(data.get("retries", 0)),
            failure_reasons=[str(item) for item in data.get("failure_reasons", [])],
            trajectory=trajectory,
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class EvaluationReport:
    """Deterministic aggregate over one fixture's trials."""

    task_id: str
    trial_count: int
    pass_at_1: float
    all_trials_passed: bool
    safety_failure_rate: float
    mean_latency_sec: float
    mean_cost_usd: float
    mean_retries: float
    trials: List[TrialResult]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise EvaluationSchemaError(f"unsupported report schema: {self.schema_version!r}")
        if self.trial_count != len(self.trials) or self.trial_count <= 0:
            raise EvaluationSchemaError("report trial_count must match a non-empty trial list")
        if not 0.0 <= self.pass_at_1 <= 1.0 or not 0.0 <= self.safety_failure_rate <= 1.0:
            raise EvaluationSchemaError("report rates must be between zero and one")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["trials"] = [trial.to_dict() for trial in self.trials]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvaluationReport":
        if not isinstance(data, dict):
            raise EvaluationSchemaError("evaluation report must be an object")
        return cls(
            task_id=str(data.get("task_id", "")),
            trial_count=int(data.get("trial_count", 0)),
            pass_at_1=float(data.get("pass_at_1", 0.0)),
            all_trials_passed=bool(data.get("all_trials_passed", False)),
            safety_failure_rate=float(data.get("safety_failure_rate", 0.0)),
            mean_latency_sec=float(data.get("mean_latency_sec", 0.0)),
            mean_cost_usd=float(data.get("mean_cost_usd", 0.0)),
            mean_retries=float(data.get("mean_retries", 0.0)),
            trials=[TrialResult.from_dict(item) for item in data.get("trials", [])],
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )

    def to_json(self) -> str:
        return dumps(self)

    @classmethod
    def from_json(cls, value: str) -> "EvaluationReport":
        try:
            return cls.from_dict(json.loads(value))
        except json.JSONDecodeError as exc:
            raise EvaluationSchemaError("evaluation report JSON is invalid") from exc


@dataclass(frozen=True)
class RegressionComparison:
    """Comparison that cannot hide a hard-gate correctness regression."""

    baseline_pass_at_1: float
    candidate_pass_at_1: float
    delta_pass_at_1: float
    correctness_regression: bool
    safety_regression: bool

    @property
    def regressed(self) -> bool:
        return self.correctness_regression or self.safety_regression

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self) | {"regressed": self.regressed}


def aggregate_trials(trials: List[TrialResult]) -> EvaluationReport:
    """Aggregate trials deterministically without allowing reward to override success."""
    if not trials:
        raise EvaluationSchemaError("cannot aggregate an empty trial list")
    task_ids = {trial.task_id for trial in trials}
    if len(task_ids) != 1:
        raise EvaluationSchemaError("all trials in a report must share one task_id")
    count = len(trials)
    passed = sum(trial.success for trial in trials)
    safety_failures = sum(not trial.safety_passed for trial in trials)
    return EvaluationReport(
        task_id=trials[0].task_id,
        trial_count=count,
        pass_at_1=passed / count,
        all_trials_passed=passed == count,
        safety_failure_rate=safety_failures / count,
        mean_latency_sec=sum(trial.latency_sec for trial in trials) / count,
        mean_cost_usd=sum(trial.cost_usd for trial in trials) / count,
        mean_retries=sum(trial.retries for trial in trials) / count,
        trials=list(trials),
    )


def compare_reports(baseline: EvaluationReport, candidate: EvaluationReport) -> RegressionComparison:
    """Compare reports with correctness and safety treated as non-compensable."""
    if baseline.task_id != candidate.task_id:
        raise EvaluationSchemaError("cannot compare reports for different tasks")
    return RegressionComparison(
        baseline_pass_at_1=baseline.pass_at_1,
        candidate_pass_at_1=candidate.pass_at_1,
        delta_pass_at_1=candidate.pass_at_1 - baseline.pass_at_1,
        correctness_regression=candidate.pass_at_1 < baseline.pass_at_1,
        safety_regression=candidate.safety_failure_rate > baseline.safety_failure_rate,
    )


def _validate_relative_paths(field_name: str, paths: List[str]) -> None:
    for value in paths:
        if not isinstance(value, str) or not value.strip():
            raise EvaluationSchemaError(f"{field_name} entries must be non-empty strings")
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise EvaluationSchemaError(f"{field_name} must contain workspace-relative paths: {value!r}")


AgentCallback = Callable[[EvaluationFixture, Path, Callable[[str, Dict[str, Any]], None]], Mapping[str, Any]]


def make_orchestrator_agent(
    orchestrator_factory: Callable[[], Any],
    *,
    pre_planned_tasks: Optional[List[Dict[str, Any]]] = None,
    benchmark_mode: bool = False,
    budget_usd: Optional[float] = None,
    use_native_planner: bool = False,
) -> AgentCallback:
    """Adapt the real orchestrator to the evaluator callback contract.

    The orchestrator result is recorded as trajectory evidence only. The
    evaluator still runs independent acceptance and safety gates afterward.
    """
    def agent(fixture: EvaluationFixture, workspace: Path, emit: Callable[[str, Dict[str, Any]], None]) -> Mapping[str, Any]:
        emit("orchestrator_started", {"goal": fixture.request})
        if use_native_planner:
            try:
                from .fixed_provider import FIXED_MODEL, FIXED_PROVIDER, PLANNER_MODEL
            except ImportError:
                from fixed_provider import FIXED_MODEL, FIXED_PROVIDER, PLANNER_MODEL
            emit("planner_route_requested", {
                "provider": FIXED_PROVIDER,
                "planner_model": PLANNER_MODEL,
                "worker_model": FIXED_MODEL,
                "pre_planned_tasks_injected": False,
            })
        benchmark_python = (
            fixture.acceptance[0].command[0]
            if benchmark_mode and fixture.acceptance and fixture.acceptance[0].command
            else None
        )
        managed_env = {
            "AWOS_BENCHMARK_MODE": "1" if benchmark_mode else None,
            "AWOS_PLAN_REVIEW_FAIL_CLOSED": "1" if benchmark_mode else None,
            "AWOS_REPLAN_DISABLE": "1" if benchmark_mode else None,
            "AWOS_BUDGET_USD": str(budget_usd) if budget_usd is not None else None,
            "AWOS_TEST_PYTHON": benchmark_python,
            "PYTHONDONTWRITEBYTECODE": "1" if benchmark_mode else None,
        }
        if use_native_planner:
            # Native holdouts require the advanced Planner. Ambient cheap-only
            # routing must not silently change the model role being evaluated.
            managed_env.update({
                "AWOS_CHEAP_ONLY": None,
                "AWOS_PREMIUM_BUDGET": None,
                "AWOS_NATIVE_PLANNER_REQUIRED": "1",
            })
        previous_env = {key: os.environ.get(key) for key in managed_env}
        try:
            for key, value in managed_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            orchestrator = orchestrator_factory()
            executable_tasks = pre_planned_tasks
            if benchmark_mode and len(fixture.allowed_paths) == 1 and not use_native_planner:
                acceptance_command = (
                    " ".join(fixture.acceptance[0].command)
                    if fixture.acceptance and fixture.acceptance[0].command
                    else "focused acceptance command"
                )
                executable_tasks = [{
                    "task_id": 1,
                    "file": fixture.allowed_paths[0],
                    "action": fixture.request,
                    "acceptance_check": acceptance_command,
                    "evidence_ref": f"fixture:{fixture.task_id}",
                    "complexity": "low",
                }]
            result = orchestrator.execute_feature(
                goal=fixture.request,
                codebase_root=str(workspace),
                pre_planned_tasks=executable_tasks,
            )
            if use_native_planner:
                planner_label = getattr(orchestrator, "_planner_label", "unknown")
                native_route_valid = planner_label == "Planner"
                emit("planner_route_result", {
                    "planner_label": planner_label,
                    "planner_route_valid": native_route_valid,
                    "pre_planned_tasks_injected": executable_tasks is not None,
                    "orchestrator_success": bool(result.get("success")),
                })
                if not native_route_valid:
                    result = dict(result)
                    result["success"] = False
                    result["errors"] = list(result.get("errors", [])) + [
                        f"native Planner route invalid: {planner_label}"
                    ]
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        for entry in result.get("execution_log", []):
            emit("orchestrator_task", {
                "task_id": entry.get("task_id"),
                "status": entry.get("status"),
                "reason": entry.get("reason", ""),
            })
        emit("orchestrator_finished", {
            "success": bool(result.get("success")),
            "tasks_completed": int(result.get("tasks_completed", 0) or 0),
            "tasks_failed": int(result.get("tasks_failed", 0) or 0),
            "debug_session_id": result.get("debug_session_id"),
        })
        return {
            "success": bool(result.get("success")),
            "cost_usd": float(result.get("total_cost", 0.0) or 0.0),
            "retries": sum(1 for entry in result.get("execution_log", []) if entry.get("status") == "retry"),
        }

    return agent


class EvaluationRunner:
    """Run one fixture trial in a fresh workspace with executable hard gates."""

    def __init__(self, *, keep_workspace: bool = False) -> None:
        self.keep_workspace = keep_workspace

    def run_trial(
        self,
        fixture: EvaluationFixture,
        agent: AgentCallback,
        *,
        trial_id: str = "trial_1",
    ) -> TrialResult:
        source = Path(fixture.repository_path).resolve()
        if not source.is_dir():
            raise EvaluationSchemaError(f"repository_path is not a directory: {source}")

        started = time.monotonic()
        events: List[TrajectoryEvent] = []
        sequence = 0

        def emit(kind: str, payload: Dict[str, Any] | None = None) -> None:
            nonlocal sequence
            events.append(TrajectoryEvent(sequence, kind, payload or {}))
            sequence += 1

        emit("trial_started", {"task_id": fixture.task_id, "trial_id": trial_id})
        temp_context = tempfile.TemporaryDirectory(prefix="awos-eval-")
        workspace = Path(temp_context.name) / "workspace"
        shutil.copytree(source, workspace, symlinks=True)
        before = _snapshot_workspace(workspace)
        agent_report: Mapping[str, Any] = {}
        failure_reasons: List[str] = []
        try:
            agent_report = agent(fixture, workspace, emit) or {}
            if not isinstance(agent_report, Mapping):
                failure_reasons.append("malformed_agent_result")
                agent_report = {}
        except Exception as exc:  # the evaluator must report agent errors, not hide them
            failure_reasons.append("agent_error")
            emit("agent_error", {"error": type(exc).__name__, "detail": str(exc)[:500]})
        changed = _changed_paths(before, _snapshot_workspace(workspace))
        safety_passed = _changes_are_allowed(changed, fixture)
        if not safety_passed:
            failure_reasons.append("forbidden_or_unallowed_write")
        emit("workspace_checked", {"changed_paths": changed, "safety_passed": safety_passed})

        acceptance_passed = True
        for check in fixture.acceptance:
            outcome = _run_acceptance(check, workspace)
            emit("acceptance_finished", outcome)
            if check.required and not outcome["passed"]:
                acceptance_passed = False
                failure_reasons.append(f"acceptance_failed:{check.name}")
        claimed_success = bool(agent_report.get("success", False))
        expected_success = acceptance_passed and safety_passed and not failure_reasons
        truthful_report = claimed_success == expected_success
        if not truthful_report:
            failure_reasons.append("untruthful_agent_report")
        hard_success = acceptance_passed and safety_passed and truthful_report and not failure_reasons
        status = TrialStatus.PASSED if hard_success else TrialStatus.FAILED
        if "agent_error" in failure_reasons:
            status = TrialStatus.ERROR
        elapsed = time.monotonic() - started
        emit("trial_finished", {"status": status.value, "success": hard_success})
        result = TrialResult(
            task_id=fixture.task_id,
            trial_id=trial_id,
            status=status,
            acceptance_passed=acceptance_passed,
            safety_passed=safety_passed,
            truthful_report=truthful_report,
            diagnostic_reward=1.0 if hard_success else 0.0,
            latency_sec=elapsed,
            cost_usd=float(agent_report.get("cost_usd", 0.0) or 0.0),
            retries=int(agent_report.get("retries", 0) or 0),
            failure_reasons=sorted(set(failure_reasons)),
            trajectory=events,
        )
        if not self.keep_workspace:
            temp_context.cleanup()
        return result


def _snapshot_workspace(root: Path) -> Dict[str, str]:
    snapshot: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[relative] = "file:" + digest
    return snapshot


def _changed_paths(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _changes_are_allowed(changed: List[str], fixture: EvaluationFixture) -> bool:
    allowed = [Path(value) for value in fixture.allowed_paths]
    forbidden = [Path(value) for value in fixture.forbidden_paths]
    for value in changed:
        path = Path(value)
        if any(path == item or item in path.parents for item in forbidden):
            return False
        if not allowed or not any(path == item or item in path.parents for item in allowed):
            return False
    return True


def _run_acceptance(check: AcceptanceCheck, workspace: Path) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = "0"
        completed = subprocess.run(
            check.command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=check.timeout_sec,
            shell=False,
            check=False,
        )
        return {
            "name": check.name,
            "command": check.command,
            "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "timed_out": False,
            "duration_sec": round(time.monotonic() - started, 6),
            "output": (completed.stdout + completed.stderr)[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": check.name,
            "command": check.command,
            "passed": False,
            "returncode": None,
            "timed_out": True,
            "launch_error": False,
            "duration_sec": round(time.monotonic() - started, 6),
            "output": str(exc)[-4000:],
        }
    except TimeoutError as exc:
        return {
            "name": check.name,
            "command": check.command,
            "passed": False,
            "returncode": None,
            "timed_out": True,
            "launch_error": False,
            "duration_sec": round(time.monotonic() - started, 6),
            "output": str(exc)[-4000:],
        }
    except OSError as exc:
        return {
            "name": check.name,
            "command": check.command,
            "passed": False,
            "returncode": None,
            "timed_out": False,
            "launch_error": True,
            "duration_sec": round(time.monotonic() - started, 6),
            "output": f"{type(exc).__name__}: {exc}"[-4000:],
        }


def dumps(value: Any) -> str:
    """Serialize supported evaluation objects deterministically."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif isinstance(value, list):
        value = [item.to_dict() if hasattr(item, "to_dict") else item for item in value]
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
