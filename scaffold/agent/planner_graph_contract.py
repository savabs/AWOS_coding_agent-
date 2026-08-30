"""Provider-free contract for advanced Planner execution-graph responses."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from graphlib import TopologicalSorter
from typing import Any

from .execution_graph import ExecutionGraph, ExecutionGraphError, ExecutionNodeType

GRAPH_VERSION = "1"
MAX_GRAPH_NODES = 200
MAX_RETRY_LIMIT = 3

_PLANNER_HINT_FIELDS = frozenset(
    {
        "function_signature",
        "interface_contract",
        "constraints",
        "must_not",
        "example_call",
    }
)
_RUNTIME_NODE_FIELDS = frozenset(
    {
        "node_id",
        "type",
        "description",
        "depends_on",
        "input_files",
        "output_files",
        "acceptance_check",
        "evidence_ref",
        "parallel_safe",
        "retry_limit",
        "complexity",
    }
)
_NODE_FIELDS = _RUNTIME_NODE_FIELDS | _PLANNER_HINT_FIELDS | {"planner_metadata"}
_MAX_HINT_TEXT = 2000
_MAX_HINT_ITEMS = 20
_MAX_HINT_ITEM_TEXT = 500
_MAX_METADATA_DEPTH = 4
_MAX_METADATA_KEYS = 50
_MAX_METADATA_LIST_ITEMS = 50
_MAX_METADATA_STRING = 4000
_MAX_METADATA_BYTES = 12000
_NODE_TYPES = frozenset(item.value for item in ExecutionNodeType)
_COMPLEXITIES = frozenset({"low", "medium", "high"})


class PlannerGraphContractError(ValueError):
    """Raised when an advanced Planner graph is invalid or unsafe."""


def _require_string(value: Any, field: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlannerGraphContractError(f"{label} requires non-empty {field}")
    return value.strip()


def _paths(value: Any, field: str, label: str) -> list[str]:
    if not isinstance(value, list):
        raise PlannerGraphContractError(f"{label} {field} must be a list")
    result: list[str] = []
    for path in value:
        path_text = _require_string(path, field, label).replace("\\", "/")
        parts = tuple(part for part in path_text.split("/") if part not in ("", "."))
        if "\x00" in path_text or path_text.startswith("/") or ".." in parts:
            raise PlannerGraphContractError(
                f"{label} {field} contains unsafe workspace path: {path!r}"
            )
        result.append(path_text)
    return list(dict.fromkeys(result))


def planner_graph_json_schema() -> dict[str, Any]:
    """Return the JSON Schema sent to the structured advanced Planner call."""
    node_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "node_id",
            "type",
            "description",
            "depends_on",
            "input_files",
            "output_files",
            "acceptance_check",
            "evidence_ref",
            "parallel_safe",
            "retry_limit",
            "complexity",
        ],
        "properties": {
            "node_id": {"type": "string", "minLength": 1},
            "type": {"type": "string", "enum": sorted(_NODE_TYPES)},
            "description": {"type": "string", "minLength": 1},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "input_files": {"type": "array", "items": {"type": "string"}},
            "output_files": {"type": "array", "items": {"type": "string"}},
            "acceptance_check": {"type": ["string", "null"]},
            "evidence_ref": {"type": ["string", "null"]},
            "parallel_safe": {"type": "boolean"},
            "retry_limit": {"type": "integer", "minimum": 0, "maximum": MAX_RETRY_LIMIT},
            "complexity": {"type": "string", "enum": sorted(_COMPLEXITIES)},
            "function_signature": {"type": ["string", "null"], "maxLength": _MAX_HINT_TEXT},
            "interface_contract": {"type": ["string", "null"], "maxLength": _MAX_HINT_TEXT},
            "constraints": {
                "type": "array",
                "maxItems": _MAX_HINT_ITEMS,
                "items": {"type": "string", "maxLength": _MAX_HINT_ITEM_TEXT},
            },
            "must_not": {
                "type": "array",
                "maxItems": _MAX_HINT_ITEMS,
                "items": {"type": "string", "maxLength": _MAX_HINT_ITEM_TEXT},
            },
            "example_call": {"type": ["string", "null"], "maxLength": _MAX_HINT_TEXT},
            "planner_metadata": {
                "type": "object",
                "maxProperties": _MAX_METADATA_KEYS,
                "additionalProperties": True,
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["graph_version", "nodes"],
        "properties": {
            "graph_version": {"type": "string", "const": GRAPH_VERSION},
            "nodes": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_GRAPH_NODES,
                "items": node_schema,
            },
        },
    }


def _normalize_metadata(value: Any, label: str, depth: int = 0) -> Any:
    """Validate bounded JSON-like Planner metadata without interpreting it."""
    if depth > _MAX_METADATA_DEPTH:
        raise PlannerGraphContractError(
            f"{label} planner_metadata exceeds depth {_MAX_METADATA_DEPTH}"
        )
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlannerGraphContractError(f"{label} planner_metadata contains non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_STRING:
            raise PlannerGraphContractError(
                f"{label} planner_metadata string exceeds {_MAX_METADATA_STRING} characters"
            )
        return value
    if isinstance(value, list):
        if len(value) > _MAX_METADATA_LIST_ITEMS:
            raise PlannerGraphContractError(
                f"{label} planner_metadata list exceeds {_MAX_METADATA_LIST_ITEMS} items"
            )
        return [_normalize_metadata(item, label, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > _MAX_METADATA_KEYS:
            raise PlannerGraphContractError(
                f"{label} planner_metadata object exceeds {_MAX_METADATA_KEYS} keys"
            )
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 80:
                raise PlannerGraphContractError(
                    f"{label} planner_metadata contains an invalid key"
                )
            normalized[key.strip()] = _normalize_metadata(item, label, depth + 1)
        return normalized
    raise PlannerGraphContractError(
        f"{label} planner_metadata contains unsupported value type: {type(value).__name__}"
    )


def _validate_known_hints(metadata: dict[str, Any], label: str) -> None:
    """Keep currently rendered worker hints typed inside the extensible envelope."""
    for field in sorted(_PLANNER_HINT_FIELDS & metadata.keys()):
        value = metadata[field]
        if field in {"constraints", "must_not"}:
            if not isinstance(value, list) or len(value) > _MAX_HINT_ITEMS:
                raise PlannerGraphContractError(
                    f"{label} planner_metadata.{field} must be a bounded list"
                )
            if any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > _MAX_HINT_ITEM_TEXT
                for item in value
            ):
                raise PlannerGraphContractError(
                    f"{label} planner_metadata.{field} must contain bounded non-empty strings"
                )
        elif value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > _MAX_HINT_TEXT
        ):
            raise PlannerGraphContractError(
                f"{label} planner_metadata.{field} must be null or a bounded non-empty string"
            )


def _planner_metadata(raw_node: dict[str, Any], label: str) -> dict[str, Any]:
    """Normalize extensible metadata and migrate legacy top-level hints."""
    raw_metadata = raw_node.get("planner_metadata", {})
    if not isinstance(raw_metadata, dict):
        raise PlannerGraphContractError(f"{label} planner_metadata must be an object")
    metadata = _normalize_metadata(raw_metadata, label)
    for field in sorted(_PLANNER_HINT_FIELDS):
        if field in raw_node:
            if field in metadata:
                raise PlannerGraphContractError(
                    f"{label} duplicates planner metadata field: {field}"
                )
            metadata[field] = _normalize_metadata(raw_node[field], label)
    _validate_known_hints(metadata, label)
    try:
        encoded_size = len(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    except (TypeError, ValueError) as exc:
        raise PlannerGraphContractError(f"{label} planner_metadata is not JSON-serializable") from exc
    if encoded_size > _MAX_METADATA_BYTES:
        raise PlannerGraphContractError(
            f"{label} planner_metadata exceeds {_MAX_METADATA_BYTES} bytes"
        )
    return metadata


def planner_response_json_schema() -> dict[str, Any]:
    """Return the top-level schema for an advanced Planner response."""
    graph_schema = planner_graph_json_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["execution_graph", "reasoning"],
        "properties": {
            "execution_graph": graph_schema,
            "reasoning": {"type": "string"},
        },
    }


def validate_planner_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a native Planner graph without model/provider code."""
    if not isinstance(graph, dict):
        raise PlannerGraphContractError("execution_graph must be an object")
    if set(graph) != {"graph_version", "nodes"}:
        raise PlannerGraphContractError(
            "execution_graph must contain only graph_version and nodes"
        )
    if graph.get("graph_version") != GRAPH_VERSION:
        raise PlannerGraphContractError(
            f"unsupported execution graph version: {graph.get('graph_version')!r}"
        )
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise PlannerGraphContractError("execution_graph nodes must be a non-empty list")
    if len(nodes) > MAX_GRAPH_NODES:
        raise PlannerGraphContractError(f"execution graph exceeds {MAX_GRAPH_NODES} nodes")

    normalized: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw_node in enumerate(nodes, start=1):
        label = f"planner node {index}"
        if not isinstance(raw_node, dict):
            raise PlannerGraphContractError(f"{label} must be an object")
        unknown = set(raw_node) - _NODE_FIELDS
        missing = _RUNTIME_NODE_FIELDS - set(raw_node)
        if unknown or missing:
            raise PlannerGraphContractError(
                f"{label} schema mismatch; unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        node_id = _require_string(raw_node["node_id"], "node_id", label)
        if node_id in node_ids:
            raise PlannerGraphContractError(f"duplicate planner node_id: {node_id}")
        node_ids.add(node_id)
        node_type = _require_string(raw_node["type"], "type", label)
        if node_type not in _NODE_TYPES:
            raise PlannerGraphContractError(f"{label} has unknown node type: {node_type!r}")
        description = _require_string(raw_node["description"], "description", label)
        dependencies = raw_node["depends_on"]
        if not isinstance(dependencies, list) or any(
            not isinstance(item, str) or not item.strip() for item in dependencies
        ):
            raise PlannerGraphContractError(f"{label} depends_on must contain node IDs")
        dependencies = list(dict.fromkeys(item.strip() for item in dependencies))
        if node_id in dependencies:
            raise PlannerGraphContractError(f"{label} cannot depend on itself")
        input_files = _paths(raw_node["input_files"], "input_files", label)
        output_files = _paths(raw_node["output_files"], "output_files", label)
        acceptance_check = raw_node["acceptance_check"]
        if acceptance_check is not None:
            acceptance_check = _require_string(acceptance_check, "acceptance_check", label)
        evidence_ref = raw_node["evidence_ref"]
        if evidence_ref is not None:
            evidence_ref = _require_string(evidence_ref, "evidence_ref", label)
        parallel_safe = raw_node["parallel_safe"]
        if not isinstance(parallel_safe, bool):
            raise PlannerGraphContractError(f"{label} parallel_safe must be boolean")
        retry_limit = raw_node["retry_limit"]
        if (
            isinstance(retry_limit, bool)
            or not isinstance(retry_limit, int)
            or retry_limit < 0
            or retry_limit > MAX_RETRY_LIMIT
        ):
            raise PlannerGraphContractError(
                f"{label} retry_limit must be an integer from 0 to {MAX_RETRY_LIMIT}"
            )
        complexity = raw_node["complexity"]
        if complexity not in _COMPLEXITIES:
            raise PlannerGraphContractError(f"{label} has invalid complexity: {complexity!r}")

        planner_metadata = _planner_metadata(raw_node, label)

        if node_type in {ExecutionNodeType.EDIT.value, ExecutionNodeType.RECOVERY.value}:
            if not output_files:
                raise PlannerGraphContractError(
                    f"{label} type={node_type} requires output_files"
                )
        if node_type == ExecutionNodeType.TEST.value and not acceptance_check:
            raise PlannerGraphContractError(f"{label} test nodes require acceptance_check")
        if node_type in {
            ExecutionNodeType.BARRIER.value,
            ExecutionNodeType.FINAL_VERIFY.value,
        } and parallel_safe:
            raise PlannerGraphContractError(
                f"{label} type={node_type} cannot be marked parallel_safe"
            )
        if node_type in {
            ExecutionNodeType.EXPLORE.value,
            ExecutionNodeType.REVIEW.value,
            ExecutionNodeType.TEST.value,
            ExecutionNodeType.BARRIER.value,
            ExecutionNodeType.FINAL_VERIFY.value,
        } and output_files:
            raise PlannerGraphContractError(
                f"{label} read-only type={node_type} cannot declare output_files"
            )

        normalized.append(
            {
                "node_id": node_id,
                "type": node_type,
                "description": description,
                "depends_on": dependencies,
                "input_files": input_files,
                "output_files": output_files,
                "acceptance_check": acceptance_check,
                "evidence_ref": evidence_ref,
                "parallel_safe": parallel_safe,
                "retry_limit": retry_limit,
                "complexity": complexity,
                "planner_metadata": planner_metadata,
            }
        )

    dependency_graph = {
        node["node_id"]: set(node["depends_on"])
        for node in normalized
    }
    unknown = sorted(
        dependency for dependencies in dependency_graph.values() for dependency in dependencies
        if dependency not in node_ids
    )
    if unknown:
        raise PlannerGraphContractError(
            f"unknown planner dependencies: {', '.join(dict.fromkeys(unknown))}"
        )
    try:
        tuple(TopologicalSorter(dependency_graph).static_order())
    except ValueError as exc:
        raise PlannerGraphContractError("execution graph contains a dependency cycle") from exc
    return {"graph_version": GRAPH_VERSION, "nodes": normalized}


def project_legacy_plan(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Project edit/recovery graph nodes into the current task-list contract."""
    normalized = validate_planner_graph(graph)
    tasks: list[dict[str, Any]] = []
    for node in normalized["nodes"]:
        if node["type"] not in {ExecutionNodeType.EDIT.value, ExecutionNodeType.RECOVERY.value}:
            continue
        planner_metadata = dict(node.get("planner_metadata", {}))
        task = {
            "task_id": node["node_id"],
            "task_type": "edit_file",
            "file": node["output_files"][0],
            "action": node["description"],
            "complexity": node["complexity"],
            "depends_on": list(node["depends_on"]),
            "acceptance_check": node["acceptance_check"],
            "evidence_ref": node["evidence_ref"],
            "node_type": node["type"],
            "parallel_safe": node["parallel_safe"],
            "retry_limit": node["retry_limit"],
            "planner_metadata": planner_metadata,
        }
        for field in _PLANNER_HINT_FIELDS:
            if field in planner_metadata:
                task[field] = planner_metadata[field]
        tasks.append(task)
    return tasks


def graph_from_legacy_plan(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize an old Planner task list into the native graph contract."""
    try:
        graph = ExecutionGraph.from_plan_tasks(tasks)
    except (ExecutionGraphError, TypeError, ValueError) as exc:
        raise PlannerGraphContractError(str(exc)) from exc
    nodes = []
    for node in graph.nodes.values():
        planner_task = dict(node.metadata.get("planner_task", {}))
        planner_metadata = dict(planner_task.get("planner_metadata", {}))
        for field in _PLANNER_HINT_FIELDS:
            if field in planner_task:
                if field in planner_metadata:
                    raise PlannerGraphContractError(
                        f"legacy task duplicates planner metadata field: {field}"
                    )
                planner_metadata[field] = planner_task[field]
        nodes.append(
            {
                "node_id": node.node_id,
                "type": ExecutionNodeType(node.node_type).value,
                "description": node.description,
                "depends_on": list(node.depends_on),
                "input_files": list(node.input_files),
                "output_files": list(node.output_files),
                "acceptance_check": node.acceptance_check,
                "evidence_ref": node.evidence_ref,
                "parallel_safe": not node.writes_files,
                "retry_limit": node.retry_limit,
                "complexity": str(planner_task.get("complexity", "medium")),
                "planner_metadata": planner_metadata,
            }
        )
    return validate_planner_graph({"graph_version": GRAPH_VERSION, "nodes": nodes})


def normalize_planner_response(result: dict[str, Any]) -> dict[str, Any]:
    """Return a native graph plus compatibility plan from either response format."""
    if not isinstance(result, dict):
        raise PlannerGraphContractError("Planner response must be an object")
    response = deepcopy(result)
    if "execution_graph" in response:
        graph = validate_planner_graph(response["execution_graph"])
        response["execution_graph"] = graph
        response["plan"] = project_legacy_plan(graph)
    elif isinstance(response.get("plan"), list):
        graph = graph_from_legacy_plan(response["plan"])
        response["execution_graph"] = graph
        response["plan"] = project_legacy_plan(graph)
    else:
        raise PlannerGraphContractError("Planner response requires execution_graph or plan")
    response["total_tasks"] = len(response["plan"])
    return response
