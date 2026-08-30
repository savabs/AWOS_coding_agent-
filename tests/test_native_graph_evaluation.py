"""Large-project, provider-free evaluation for native graph orchestration."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scaffold.agent.dag_executor import DAGExecutor
from scaffold.agent.evaluation_v1 import (
    AcceptanceCheck,
    EvaluationFixture,
    EvaluationRunner,
    TrialStatus,
)
from scaffold.agent.execution_graph import (
    ExecutionGraph,
    ExecutionNode,
    ExecutionNodeStatus,
    ExecutionNodeType,
)
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.runtime_session import RuntimeSessionStore


def _node(node_id: str, node_type: str, **overrides) -> dict:
    node = {
        "node_id": node_id,
        "type": node_type,
        "description": f"Run {node_id}",
        "depends_on": [],
        "input_files": [],
        "output_files": [],
        "acceptance_check": None,
        "evidence_ref": None,
        "parallel_safe": True,
        "retry_limit": 0,
        "complexity": "low",
    }
    node.update(overrides)
    return node


def _large_graph() -> dict:
    edit_nodes = [
        _node(
            f"edit_{index}",
            "edit",
            depends_on=["explore"],
            output_files=[f"project/mod_{index}.py"],
        )
        for index in range(10)
    ]
    return {
        "graph_version": "1",
        "nodes": [
            _node("explore", "explore", parallel_safe=True),
            *edit_nodes,
            _node(
                "barrier",
                "barrier",
                depends_on=[node["node_id"] for node in edit_nodes],
                parallel_safe=False,
            ),
            _node(
                "tests",
                "test",
                depends_on=["barrier"],
                input_files=["project"],
                acceptance_check="pytest",
                parallel_safe=False,
            ),
            _node(
                "final_verify",
                "final_verify",
                depends_on=["tests"],
                acceptance_check="pytest",
                parallel_safe=False,
            ),
        ],
    }


def _make_repository(root: Path) -> None:
    package = root / "project"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for index in range(10):
        (package / f"mod_{index}.py").write_text(
            f"VALUE = {index}\n", encoding="utf-8"
        )


def _make_orchestrator() -> Orchestrator:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.execution_log = []
    orchestrator.integration_reviewer = Mock()
    return orchestrator


def test_native_graph_large_project_evaluation(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _make_repository(repository)
    acceptance_code = (
        "import importlib; "
        "values = [importlib.import_module(f'project.mod_{i}').VALUE for i in range(10)]; "
        "assert sum(values) == 55"
    )
    fixture = EvaluationFixture(
        task_id="native_graph_large_project_001",
        family="native_graph_orchestration",
        repository_path=str(repository),
        request="Update all ten project modules through the execution graph.",
        acceptance=[
            AcceptanceCheck(
                command=[sys.executable, "-c", acceptance_code],
                timeout_sec=30,
                name="all_modules_updated",
            )
        ],
        allowed_paths=["project"],
    )
    active_workers = 0
    max_active_workers = 0
    lock = threading.Lock()

    def agent(_fixture, workspace: Path, emit):
        orchestrator = _make_orchestrator()
        graph = orchestrator._restore_native_execution_graph(_large_graph())
        started = time.perf_counter()
        emit("graph_started", {"node_count": len(graph.nodes)})

        def execute_task(task: dict, _ctx: dict) -> dict:
            nonlocal active_workers, max_active_workers
            with lock:
                active_workers += 1
                max_active_workers = max(max_active_workers, active_workers)
            try:
                time.sleep(0.01)
                path = workspace / task["file"]
                index = int(path.stem.split("_")[-1])
                path.write_text(f"VALUE = {index + 1}\n", encoding="utf-8")
                emit("edit_completed", {"node_id": task["task_id"]})
                return {"task_id": task["task_id"], "task": task, "success": True}
            finally:
                with lock:
                    active_workers -= 1

        orchestrator._execute_single_task = Mock(side_effect=execute_task)
        fake_test_result = SimpleNamespace(
            no_tests_found=True, timed_out=False, failed=0, errors=0, pass_rate=1.0
        )
        with patch("scaffold.agent.orchestrator.TestRunner") as runner_cls:
            runner_cls.return_value.run.return_value = fake_test_result
            results = orchestrator._run_native_graph_batch(
                graph,
                {"codebase_root": str(workspace), "exploration": {}},
            )

        elapsed = time.perf_counter() - started
        emit("graph_finished", {"elapsed_sec": elapsed, "result_count": len(results)})
        assert len(results) == 14
        assert all(item["success"] for item in results)
        assert max_active_workers >= 2
        return {
            "success": True,
            "cost_usd": 10 * 0.001,
            "retries": 0,
            "telemetry": {"max_parallel_edits": max_active_workers},
        }

    result = EvaluationRunner().run_trial(fixture, agent)

    assert result.status is TrialStatus.PASSED, result.failure_reasons
    assert result.acceptance_passed is True
    assert result.safety_passed is True
    assert result.truthful_report is True
    assert result.latency_sec > 0
    assert result.cost_usd == 0.01
    assert [event.kind for event in result.trajectory] == [
        "trial_started",
        "graph_started",
        *(["edit_completed"] * 10),
        "graph_finished",
        "workspace_checked",
        "acceptance_finished",
        "trial_finished",
    ]


def test_native_graph_evaluation_proves_explicit_recovery_and_resume(tmp_path) -> None:
    failed = ExecutionNode(
        node_id="intentional_failure",
        node_type=ExecutionNodeType.BARRIER,
        description="Synthetic failure used by the recovery proof",
        read_only=True,
    )
    recovery = ExecutionNode(
        node_id="explicit_recovery",
        node_type=ExecutionNodeType.RECOVERY,
        description="Repair the intentional failure",
        output_files=["project/recovery.py"],
    )
    graph = ExecutionGraph([failed])
    failed_results = DAGExecutor([]).execute_graph(
        graph, lambda _node: {"success": False, "error": "intentional evaluation failure"}
    )
    assert failed_results[0]["success"] is False
    failed_node = graph.get_node("intentional_failure")
    assert failed_node is not None
    assert failed_node.status == ExecutionNodeStatus.FAILED

    graph.add_recovery_node("intentional_failure", recovery)
    recovery_results = DAGExecutor([]).execute_graph(
        graph, lambda _node: {"success": True, "evidence_ref": "proof:recovery"}
    )
    assert recovery_results[0]["node_id"] == "explicit_recovery"
    recovery_node = graph.get_node("explicit_recovery")
    assert recovery_node is not None
    assert recovery_node.status == ExecutionNodeStatus.COMPLETED

    resumable = ExecutionGraph(
        [
            ExecutionNode(
                node_id="completed_wave",
                node_type=ExecutionNodeType.EXPLORE,
                description="Completed before pause",
                read_only=True,
            ),
            ExecutionNode(
                node_id="pending_wave",
                node_type=ExecutionNodeType.BARRIER,
                description="Runs after resume",
                depends_on=["completed_wave"],
                read_only=True,
            ),
        ]
    )
    resumable.mark_status("completed_wave", ExecutionNodeStatus.COMPLETED)
    store = RuntimeSessionStore(sessions_dir=str(tmp_path / "sessions"))
    session = store.create("native graph resume", str(tmp_path))
    session.execution_graph = resumable.to_dict()
    store.save(session)
    persisted = store.load(session.session_id)
    assert persisted.execution_graph is not None
    restored = ExecutionGraph.from_dict(persisted.execution_graph)
    completed_node = restored.get_node("completed_wave")
    assert completed_node is not None
    assert completed_node.status == ExecutionNodeStatus.COMPLETED
    resumed_results = DAGExecutor([]).execute_graph(restored, lambda _node: {"success": True})
    assert [item["node_id"] for item in resumed_results] == ["pending_wave"]
