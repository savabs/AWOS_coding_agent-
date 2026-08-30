"""
dag_executor.py — DAG-Based Parallel Task Executor for the Orchestrator.

Groups tasks into dependency-ordered "waves" and executes each wave
concurrently with ThreadPoolExecutor.  Tasks touching the same file are
kept sequential; tasks on different files run in parallel.

No new dependencies — uses stdlib concurrent.futures only.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

try:
    from .execution_graph import (
        ExecutionGraph,
        ExecutionGraphError,
        ExecutionNode,
        ExecutionNodeStatus,
    )
except ImportError:
    from execution_graph import (
        ExecutionGraph,
        ExecutionGraphError,
        ExecutionNode,
        ExecutionNodeStatus,
    )

logger = logging.getLogger(__name__)

MAX_WORKERS = 4  # cap concurrent LLM API calls


class DAGExecutor:
    """
    Execute a list of tasks in dependency-ordered parallel waves.

    Usage::

        dag = DAGExecutor(tasks)
        results = dag.execute(lambda task: orchestrator._execute_single_task(task, ...))
    """

    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self.tasks = tasks
        self.waves = self._build_waves(tasks)

    # ── Public API ─────────────────────────────────────────────────────────────

    def execute(
        self,
        task_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Run all tasks, respecting wave order.

        Args:
            task_fn: Callable(task) → result dict.  Must be thread-safe.

        Returns:
            List of result dicts (order matches task_fn completion order
            within a wave; waves are sequential).
        """
        all_results: list[dict[str, Any]] = []

        for wave_idx, wave in enumerate(self.waves):
            logger.info(
                "[DAG] Wave %d/%d — %d task(s): %s",
                wave_idx + 1,
                len(self.waves),
                len(wave),
                [t["task_id"] for t in wave],
            )

            if len(wave) == 1:
                all_results.append(task_fn(wave[0]))
            else:
                workers = min(MAX_WORKERS, len(wave))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(task_fn, task): task for task in wave}
                    for fut in as_completed(futures):
                        task = futures[fut]
                        try:
                            all_results.append(fut.result())
                        except Exception as exc:
                            logger.error(
                                "[DAG] Task %s raised an exception: %s",
                                task["task_id"],
                                exc,
                            )
                            all_results.append(
                                {
                                    "task_id": task["task_id"],
                                    "success": False,
                                    "task": task,
                                    "error": str(exc),
                                }
                            )

        return all_results

    def execute_graph(
        self,
        graph: ExecutionGraph,
        node_fn: Callable[[ExecutionNode], dict[str, Any]],
        *,
        on_wave_complete: Callable[[ExecutionGraph, list[dict[str, Any]]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a validated typed graph and update node runtime status.

        Unlike the legacy task-dictionary path, this method fails closed on
        cycles and recomputes ready waves after every wave so failed
        prerequisites cannot release ordinary descendants. Recovery nodes are
        released only by ``ExecutionGraph``'s explicit recovery semantics.
        ``on_wave_complete`` runs after node statuses and evidence are updated,
        allowing callers to durably checkpoint the graph before the next wave.
        """
        if not isinstance(graph, ExecutionGraph):
            raise ExecutionGraphError("execute_graph requires an ExecutionGraph")
        graph.validate()
        all_results: list[dict[str, Any]] = []

        while True:
            waves = graph.ready_waves()
            if not waves:
                break
            wave = waves[0]
            for node in wave:
                graph.mark_status(node.node_id, ExecutionNodeStatus.RUNNING)

            def run_node(node: ExecutionNode) -> dict[str, Any]:
                try:
                    result = node_fn(node)
                    if not isinstance(result, dict):
                        raise TypeError("graph node callback must return a result dict")
                    return {"node_id": node.node_id, **result}
                except Exception as exc:
                    logger.error("[DAG] Graph node %s raised an exception: %s", node.node_id, exc)
                    return {
                        "node_id": node.node_id,
                        "success": False,
                        "error": str(exc),
                    }

            if len(wave) == 1:
                wave_results = [run_node(wave[0])]
            else:
                workers = min(MAX_WORKERS, len(wave))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(run_node, node) for node in wave]
                    wave_results = [future.result() for future in futures]

            for result in wave_results:
                node_id = result["node_id"]
                success = bool(result.get("success", False))
                evidence_ref = result.get("evidence_ref")
                graph.mark_status(
                    node_id,
                    ExecutionNodeStatus.COMPLETED if success else ExecutionNodeStatus.FAILED,
                    evidence_ref=evidence_ref,
                )
            all_results.extend(wave_results)
            if on_wave_complete is not None:
                on_wave_complete(graph, wave_results)

        return all_results

    @property
    def wave_count(self) -> int:
        """Number of execution waves."""
        return len(self.waves)

    @property
    def parallelism_score(self) -> float:
        """
        Fraction of tasks that run in the largest wave.
        1.0 = all tasks in one wave (fully parallel).
        0.0 = empty task list.
        """
        if not self.tasks:
            return 0.0
        max_wave = max(len(w) for w in self.waves)
        return max_wave / len(self.tasks)

    # ── Wave Building ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_waves(tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """
        Group tasks into sequential waves using dependency analysis.

        Dependencies come from two sources:
          1. **Implicit** — tasks sharing the same ``file`` are ordered by
             task_id ascending (later task depends on earlier).
          2. **Explicit** — optional ``depends_on: [task_id, ...]`` field.

        Returns a list of waves; each wave is a list of tasks safe to run
        concurrently.
        """
        if not tasks:
            return []

        id_to_task: dict[Any, dict] = {t["task_id"]: t for t in tasks}
        deps: dict[Any, set] = {t["task_id"]: set() for t in tasks}

        # ── Implicit deps: same-file ordering ─────────────────────────────────
        by_file: dict[str, list] = {}
        for t in tasks:
            by_file.setdefault(t.get("file", ""), []).append(t["task_id"])

        for file_task_ids in by_file.values():
            ordered = sorted(file_task_ids, key=str)
            for i in range(1, len(ordered)):
                deps[ordered[i]].add(ordered[i - 1])

        # ── Explicit deps ──────────────────────────────────────────────────────
        for t in tasks:
            for dep_id in t.get("depends_on", []):
                if dep_id in id_to_task:
                    deps[t["task_id"]].add(dep_id)

        # ── Topological wave assignment ────────────────────────────────────────
        waves: list[list[dict]] = []
        completed: set = set()
        remaining: set = {t["task_id"] for t in tasks}

        while remaining:
            ready = [tid for tid in remaining if deps[tid].issubset(completed)]

            if not ready:
                # Cycle guard: force the lowest-id unblocked task forward
                forced = min(remaining, key=str)
                logger.warning("[DAG] Cycle detected; forcing task %s", forced)
                ready = [forced]

            wave_tasks = [id_to_task[tid] for tid in sorted(ready, key=str)]
            waves.append(wave_tasks)
            completed.update(ready)
            remaining -= set(ready)

        return waves
