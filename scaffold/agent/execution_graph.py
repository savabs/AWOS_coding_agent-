"""Typed, deterministic execution graphs for AWOS project orchestration.

This module is the contract between the advanced Planner and runtime execution.
It is intentionally provider-free and does not execute commands or edit files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from graphlib import TopologicalSorter
from typing import Any


class ExecutionGraphError(ValueError):
    """Raised when an execution graph is malformed or unsafe to schedule."""


class ExecutionNodeType(str, Enum):
    EXPLORE = "explore"
    EDIT = "edit"
    TEST = "test"
    BARRIER = "barrier"
    REVIEW = "review"
    RECOVERY = "recovery"
    FINAL_VERIFY = "final_verify"


class ExecutionNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


_WRITE_NODE_TYPES = frozenset(
    {ExecutionNodeType.EDIT, ExecutionNodeType.RECOVERY}
)
_TERMINAL_SUCCESS = frozenset(
    {ExecutionNodeStatus.COMPLETED, ExecutionNodeStatus.SKIPPED}
)
_TERMINAL_FAILURE = frozenset(
    {ExecutionNodeStatus.FAILED, ExecutionNodeStatus.BLOCKED}
)


@dataclass
class ExecutionNode:
    """One concrete operation in a project execution graph."""

    node_id: str
    node_type: ExecutionNodeType | str
    description: str
    depends_on: list[str] = field(default_factory=list)
    input_files: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    acceptance_check: str | None = None
    evidence_ref: str | None = None
    status: ExecutionNodeStatus | str = ExecutionNodeStatus.PENDING
    retry_limit: int = 0
    retry_count: int = 0
    read_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.node_id = str(self.node_id).strip()
        self.description = str(self.description).strip()
        if not self.node_id:
            raise ExecutionGraphError("execution node_id must not be empty")
        if not self.description:
            raise ExecutionGraphError(
                f"execution node {self.node_id!r} requires a description"
            )
        try:
            self.node_type = ExecutionNodeType(self.node_type)
        except (TypeError, ValueError) as exc:
            raise ExecutionGraphError(
                f"unknown execution node type: {self.node_type!r}"
            ) from exc
        try:
            self.status = ExecutionNodeStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ExecutionGraphError(
                f"unknown execution node status: {self.status!r}"
            ) from exc
        if self.retry_limit < 0 or self.retry_count < 0:
            raise ExecutionGraphError("retry limits and counts must be non-negative")
        if self.retry_count > self.retry_limit:
            raise ExecutionGraphError("retry_count cannot exceed retry_limit")
        self.depends_on = list(dict.fromkeys(str(item) for item in self.depends_on))
        self.input_files = list(dict.fromkeys(str(item) for item in self.input_files))
        self.output_files = list(dict.fromkeys(str(item) for item in self.output_files))

    @property
    def writes_files(self) -> bool:
        """Whether this node can mutate a workspace file."""
        return not self.read_only and self.node_type in _WRITE_NODE_TYPES

    @property
    def touched_files(self) -> frozenset[str]:
        """Files used for conservative same-file conflict detection."""
        return frozenset(self.input_files) | frozenset(self.output_files)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["node_type"] = ExecutionNodeType(self.node_type).value
        data["status"] = ExecutionNodeStatus(self.status).value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionNode":
        if not isinstance(data, dict):
            raise ExecutionGraphError("execution node data must be an object")
        try:
            return cls(**data)
        except TypeError as exc:
            raise ExecutionGraphError(
                f"invalid execution node fields: {sorted(data)}"
            ) from exc


class ExecutionGraph:
    """A validated execution DAG with deterministic ready-wave scheduling."""

    def __init__(self, nodes: list[ExecutionNode] | None = None) -> None:
        self.nodes: dict[str, ExecutionNode] = {}
        for node in nodes or []:
            self.add_node(node)

    def add_node(self, node: ExecutionNode) -> None:
        if not isinstance(node, ExecutionNode):
            raise ExecutionGraphError("graph nodes must be ExecutionNode instances")
        if node.node_id in self.nodes:
            raise ExecutionGraphError(f"duplicate execution node: {node.node_id}")
        self.nodes[node.node_id] = node
        try:
            self.validate()
        except Exception:
            self.nodes.pop(node.node_id, None)
            raise

    def get_node(self, node_id: str) -> ExecutionNode | None:
        return self.nodes.get(node_id)

    def validate(self) -> None:
        """Fail closed on unknown dependencies or cycles."""
        graph = {
            node_id: set(node.depends_on)
            for node_id, node in self.nodes.items()
        }
        unknown = sorted(
            dependency
            for dependencies in graph.values()
            for dependency in dependencies
            if dependency not in self.nodes
        )
        if unknown:
            raise ExecutionGraphError(
                f"unknown execution dependencies: {', '.join(dict.fromkeys(unknown))}"
            )
        try:
            tuple(TopologicalSorter(graph).static_order())
        except ValueError as exc:
            raise ExecutionGraphError("execution graph contains a dependency cycle") from exc

    def ready_waves(self) -> list[list[ExecutionNode]]:
        """Return deterministic waves that are safe to run concurrently.

        This is a planning operation: node statuses are not mutated. A normal
        node requires successful predecessors. A recovery node is deliberately
        allowed after a failed predecessor so failure handling remains visible in
        the graph.
        """
        self.validate()
        virtual_done = {
            node_id
            for node_id, node in self.nodes.items()
            if node.status in _TERMINAL_SUCCESS
        }
        remaining = {
            node_id
            for node_id, node in self.nodes.items()
            if node.status == ExecutionNodeStatus.PENDING
        }
        waves: list[list[ExecutionNode]] = []

        while remaining:
            eligible: list[ExecutionNode] = []
            for node_id in sorted(remaining):
                node = self.nodes[node_id]
                if self._dependencies_ready(node, virtual_done):
                    eligible.append(node)
            if not eligible:
                break

            selected: list[ExecutionNode] = []
            for node in eligible:
                if any(self._conflicts(node, other) for other in selected):
                    continue
                selected.append(node)

            if not selected:
                # The first eligible node is always allowed to progress; this
                # prevents a conflict policy from creating an infinite loop.
                selected = [eligible[0]]

            waves.append(selected)
            selected_ids = {node.node_id for node in selected}
            remaining -= selected_ids
            virtual_done.update(selected_ids)

        return waves

    def mark_status(
        self,
        node_id: str,
        status: ExecutionNodeStatus | str,
        *,
        evidence_ref: str | None = None,
    ) -> None:
        """Update runtime state without allowing unknown node IDs."""
        node = self.nodes.get(node_id)
        if node is None:
            raise ExecutionGraphError(f"unknown execution node: {node_id}")
        try:
            node.status = ExecutionNodeStatus(status)
        except (TypeError, ValueError) as exc:
            raise ExecutionGraphError(f"unknown execution node status: {status!r}") from exc
        if evidence_ref is not None:
            node.evidence_ref = evidence_ref

    def add_recovery_node(self, failed_node_id: str, node: ExecutionNode) -> None:
        """Insert an acyclic recovery node that unlocks after a failure."""
        failed = self.nodes.get(failed_node_id)
        if failed is None:
            raise ExecutionGraphError(f"unknown failed node: {failed_node_id}")
        if failed.status != ExecutionNodeStatus.FAILED:
            raise ExecutionGraphError(
                f"recovery requires failed node status: {failed_node_id}"
            )
        if node.node_type != ExecutionNodeType.RECOVERY:
            raise ExecutionGraphError("recovery nodes must use node_type='recovery'")
        if node.node_id in self.nodes:
            raise ExecutionGraphError(f"duplicate execution node: {node.node_id}")
        node.depends_on = list(dict.fromkeys([*node.depends_on, failed_node_id]))
        self.nodes[node.node_id] = node
        try:
            self.validate()
        except Exception:
            self.nodes.pop(node.node_id, None)
            raise

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"nodes": [node.to_dict() for node in self.nodes.values()]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionGraph":
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            raise ExecutionGraphError("execution graph JSON requires a nodes list")
        try:
            return cls([ExecutionNode.from_dict(item) for item in data["nodes"]])
        except (TypeError, KeyError) as exc:
            raise ExecutionGraphError("invalid execution graph JSON") from exc

    @classmethod
    def from_plan_tasks(cls, tasks: list[dict[str, Any]]) -> "ExecutionGraph":
        """Compile current Planner task dictionaries into typed execution nodes.

        This compatibility compiler keeps model output as a simple task list while
        making the runtime contract explicit. Planner task IDs remain available in
        node metadata, and dependencies are normalized to the stable ``task:<id>``
        node IDs. Same-file write ordering is handled by ``ready_waves`` using the
        files carried by each node.
        """
        if not isinstance(tasks, list):
            raise ExecutionGraphError("planner tasks must be a list")

        node_specs: list[ExecutionNode] = []
        task_node_ids: dict[str, str] = {}
        for index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                raise ExecutionGraphError(f"planner task {index} must be an object")
            if "task_id" not in task:
                raise ExecutionGraphError(f"planner task {index} requires task_id")
            task_id = str(task["task_id"]).strip()
            if not task_id:
                raise ExecutionGraphError(f"planner task {index} requires non-empty task_id")
            node_id = f"task:{task_id}"
            if task_id in task_node_ids:
                raise ExecutionGraphError(f"duplicate planner task_id: {task_id}")
            task_node_ids[task_id] = node_id

            description = str(task.get("action", "")).strip()
            if not description:
                raise ExecutionGraphError(f"planner task {task_id} requires action")
            raw_type = task.get("node_type", task.get("type", ExecutionNodeType.EDIT.value))
            try:
                node_type = ExecutionNodeType(raw_type)
            except (TypeError, ValueError) as exc:
                raise ExecutionGraphError(
                    f"planner task {task_id} has unknown node type: {raw_type!r}"
                ) from exc

            files = task.get("files")
            if files is not None and (not isinstance(files, list) or len(files) > 1):
                raise ExecutionGraphError(
                    f"planner task {task_id} must identify at most one file"
                )
            file_value = task.get("file")
            if files and file_value is None:
                file_value = files[0]
            if file_value is not None and not isinstance(file_value, str):
                raise ExecutionGraphError(f"planner task {task_id} file must be a string")
            touched = [file_value] if file_value else []
            read_only = bool(task.get("read_only", node_type in {
                ExecutionNodeType.EXPLORE,
                ExecutionNodeType.REVIEW,
                ExecutionNodeType.TEST,
                ExecutionNodeType.BARRIER,
                ExecutionNodeType.FINAL_VERIFY,
            }))
            raw_dependencies = task.get("depends_on", [])
            if not isinstance(raw_dependencies, list):
                raise ExecutionGraphError(
                    f"planner task {task_id} depends_on must be a list"
                )
            dependencies = [
                task_node_ids.get(str(dependency), f"task:{dependency}")
                for dependency in raw_dependencies
            ]
            node_specs.append(
                ExecutionNode(
                    node_id=node_id,
                    node_type=node_type,
                    description=description,
                    depends_on=dependencies,
                    input_files=touched,
                    output_files=[] if read_only else touched,
                    acceptance_check=task.get("acceptance_check"),
                    evidence_ref=task.get("evidence_ref"),
                    retry_limit=int(task.get("retry_limit", 0)),
                    read_only=read_only,
                    metadata={"task_id": task["task_id"], "planner_task": dict(task)},
                )
            )

        return cls(node_specs)

    def _dependencies_ready(
        self,
        node: ExecutionNode,
        virtual_done: set[str],
    ) -> bool:
        if node.node_type == ExecutionNodeType.RECOVERY:
            return all(
                dependency in virtual_done
                or self.nodes[dependency].status in _TERMINAL_FAILURE
                for dependency in node.depends_on
            )
        return all(dependency in virtual_done for dependency in node.depends_on)

    @staticmethod
    def _conflicts(left: ExecutionNode, right: ExecutionNode) -> bool:
        if not left.writes_files or not right.writes_files:
            return False
        return bool(left.touched_files & right.touched_files)
