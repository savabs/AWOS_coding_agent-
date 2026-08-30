"""Provider-free tests for the live adapter's native Planner mode."""

from __future__ import annotations

from scaffold.agent.evaluation_v1 import AcceptanceCheck, EvaluationFixture, make_orchestrator_agent


def _fixture() -> EvaluationFixture:
    return EvaluationFixture(
        task_id="native_adapter_001",
        family="adapter",
        repository_path="repository",
        request="Plan and implement the project change.",
        acceptance=[AcceptanceCheck(command=["python3", "-c", "pass"])],
        allowed_paths=["src/app.py"],
    )


def test_native_planner_mode_does_not_inject_a_synthetic_task(tmp_path) -> None:
    captured = {}

    class FakeOrchestrator:
        _planner_label = "Planner"

        def execute_feature(self, **kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "total_cost": 0.02,
                "tasks_completed": 2,
                "tasks_failed": 0,
                "execution_log": [],
            }

    events: list[tuple[str, dict]] = []
    agent = make_orchestrator_agent(
        FakeOrchestrator,
        benchmark_mode=True,
        budget_usd=1.0,
        use_native_planner=True,
    )
    report = agent(_fixture(), tmp_path, lambda kind, payload: events.append((kind, payload)))

    assert captured["pre_planned_tasks"] is None
    assert report == {"success": True, "cost_usd": 0.02, "retries": 0}
    route = next(payload for kind, payload in events if kind == "planner_route_requested")
    assert route == {
        "provider": "opencode",
        "planner_model": "qwen3.7-plus",
        "worker_model": "deepseek-v4-pro",
        "pre_planned_tasks_injected": False,
    }
    result = next(payload for kind, payload in events if kind == "planner_route_result")
    assert result == {
        "planner_label": "Planner",
        "planner_route_valid": True,
        "pre_planned_tasks_injected": False,
        "orchestrator_success": True,
    }


def test_native_planner_mode_preserves_explicit_caller_tasks(tmp_path) -> None:
    captured = {}

    class FakeOrchestrator:
        _planner_label = "Planner"

        def execute_feature(self, **kwargs):
            captured.update(kwargs)
            return {"success": False, "total_cost": 0.0, "execution_log": []}

    explicit = [{"task_id": "explicit", "file": "src/app.py", "action": "edit", "complexity": "low"}]
    agent = make_orchestrator_agent(
        FakeOrchestrator,
        pre_planned_tasks=explicit,
        benchmark_mode=True,
        use_native_planner=True,
    )
    agent(_fixture(), tmp_path, lambda *_: None)

    assert captured["pre_planned_tasks"] == explicit


def test_native_planner_mode_overrides_ambient_cheap_only_flags(tmp_path, monkeypatch) -> None:
    observed = {}

    class FakeOrchestrator:
        _planner_label = "Planner"

        def execute_feature(self, **kwargs):
            observed["cheap_only"] = __import__("os").environ.get("AWOS_CHEAP_ONLY")
            observed["premium_budget"] = __import__("os").environ.get("AWOS_PREMIUM_BUDGET")
            observed["native_required"] = __import__("os").environ.get("AWOS_NATIVE_PLANNER_REQUIRED")
            return {"success": True, "total_cost": 0.0, "execution_log": []}

    monkeypatch.setenv("AWOS_CHEAP_ONLY", "true")
    monkeypatch.setenv("AWOS_PREMIUM_BUDGET", "0")
    agent = make_orchestrator_agent(FakeOrchestrator, use_native_planner=True)
    agent(_fixture(), tmp_path, lambda *_: None)

    assert observed == {"cheap_only": None, "premium_budget": None, "native_required": "1"}


def test_native_planner_mode_marks_cheap_route_invalid(tmp_path) -> None:
    events: list[tuple[str, dict]] = []

    class FakeOrchestrator:
        _planner_label = "CheapPlanner"

        def execute_feature(self, **kwargs):
            return {"success": True, "total_cost": 0.0, "execution_log": []}

    agent = make_orchestrator_agent(FakeOrchestrator, use_native_planner=True)
    report = agent(_fixture(), tmp_path, lambda kind, payload: events.append((kind, payload)))

    route = next(payload for kind, payload in events if kind == "planner_route_result")
    assert route["planner_route_valid"] is False
    assert report["success"] is False
