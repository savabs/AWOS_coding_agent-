"""Provider-free integration proof for persistent multi-session orchestration."""

from __future__ import annotations

from pathlib import Path

from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.project_planner import ProjectPlanner


def _task(task_id: int, action: str) -> dict:
    return {
        "task_id": task_id,
        "file": f"src/module_{task_id}.py",
        "action": action,
        "complexity": "low",
    }


def test_interrupted_project_resumes_only_pending_tasks(tmp_path: Path) -> None:
    """A second session reloads the same goal and skips completed work."""
    goals_dir = tmp_path / ".awos" / "goals"
    description = "Build a multi-module authentication subsystem"
    tasks = [
        _task(1, "Create the authentication model"),
        _task(2, "Implement the authentication service"),
        _task(3, "Add authentication service tests"),
    ]

    first = ProjectPlanner(str(goals_dir))
    graph = first.load_or_create_goal(description, session_id="session-1")
    first.ensure_task_nodes(graph, tasks, session_id="session-1")

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.project_planner = first
    orchestrator._active_goal_graph = graph
    orchestrator._update_task_node(1, "completed", "session-1")

    second = ProjectPlanner(str(goals_dir))
    resumed = second.load_or_create_goal(description, session_id="session-2")
    pending_task_ids = {
        node.tasks[0]["task_id"]
        for node in resumed.pending_nodes()
        if node.tasks
    }

    assert pending_task_ids == {2, 3}
    assert second.find_node_for_task(resumed, 1).status == "completed"


def test_resumed_failure_history_survives_reload(tmp_path: Path) -> None:
    """A failed task remains observable after a new planner process loads the goal."""
    goals_dir = tmp_path / ".awos" / "goals"
    planner = ProjectPlanner(str(goals_dir))
    graph = planner.load_or_create_goal(
        "Build a multi-module billing subsystem", session_id="session-1"
    )
    planner.ensure_task_nodes(
        graph,
        [_task(7, "Implement the billing adapter")],
        session_id="session-1",
    )

    node = planner.find_node_for_task(graph, 7)
    assert node is not None
    planner.update_node_status(
        graph,
        node.goal_id,
        "failed",
        session_id="session-1",
        task_id="7",
        critique_id="critique-timeout",
    )

    reloaded = ProjectPlanner(str(goals_dir)).load_or_create_goal(
        "Build a multi-module billing subsystem", session_id="session-2"
    )
    failed = reloaded.get_node(node.goal_id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_count == 1
    assert failed.critique_ids == ["critique-timeout"]
