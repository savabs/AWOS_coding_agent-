---
title: "Spec: Typed Execution Graph"
tags:
  - doc/spec
  - phase/1
  - topic/execution-graph
  - layer/orchestration
---

# Typed Execution Graph

## Goal

Create a deterministic, provider-free execution-graph contract where typed nodes represent edits, tests, barriers, and recovery operations; invalid dependencies fail closed; and ready nodes can be grouped into safe parallel waves.

Exit condition: focused graph tests pass and prove ordering, parallel waves, test barriers, failure blocking, recovery insertion, cycle rejection, and JSON round-trip.

## Research Reference

Research doc: [[execution_graph]] — the advanced Planner proposes graph work, while a deterministic compiler validates and canonicalizes executable structure.

## Files Affected

**New files:**
- `scaffold/agent/execution_graph.py` — typed node, graph, compiler, and validation contracts.
- `tests/test_execution_graph.py` — provider-free graph behavior tests.

**Modified files:**
- `scaffold/agent/dag_executor.py` — no change required in the first slice; the graph exposes compatible ready waves for later wiring.

**Files explicitly NOT touched:**
- `scaffold/agent/orchestrator.py` — production wiring follows after this contract is proven.
- `scaffold/agent/project_planner.py` — existing goal persistence remains separate from execution operations.

## Implementation Steps

### Step 1: Define typed execution nodes

Create `ExecutionNode` with ID, type, dependencies, file inputs/outputs, acceptance command, status, retry limit, and evidence metadata. Support node types `explore`, `edit`, `test`, `barrier`, `review`, `recovery`, and `final_verify`.

Verification: construct and serialize one node of every supported type.

### Step 2: Define graph validation

Create `ExecutionGraph` and reject duplicate IDs, unknown dependencies, invalid node types, and cycles. Keep validation deterministic and side-effect free.

Verification: invalid graphs raise `ExecutionGraphError` with actionable messages.

### Step 3: Implement ready-wave calculation

Return deterministic waves of pending nodes whose dependencies completed successfully. Do not release a node when a required dependency failed, blocked, or is still running. Nodes sharing a file are serialized unless explicitly marked read-only.

Verification: independent edit nodes share a wave; same-file nodes do not; downstream test nodes wait for all prerequisites.

### Step 4: Implement recovery insertion

Add a recovery node after a failed node, link it to the failed node, and optionally link a retry node to both recovery and the original prerequisites. Enforce a bounded retry count.

Verification: failed test → recovery → retry appears in order and cannot create a cycle.

### Step 5: Implement persistence and focused tests

Implement JSON round-trip and tests for all edge cases in `tests/test_execution_graph.py`.

Verification: `python3 -m pytest tests/test_execution_graph.py -q` passes without credentials or network access.

## Interface Contract

```python
class ExecutionGraph:
    def add_node(self, node: ExecutionNode) -> None: ...
    def validate(self) -> None: ...
    def ready_waves(self) -> list[list[ExecutionNode]]: ...
    def add_recovery_node(self, failed_node_id: str, node: ExecutionNode) -> None: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionGraph": ...
```

Statuses: `pending`, `ready`, `running`, `completed`, `failed`, `blocked`, `skipped`.

## Edge Cases

| Scenario | Expected behavior |
|---|---|
| Empty graph | Valid; ready waves are empty |
| Duplicate node ID | Raise `ExecutionGraphError` |
| Unknown dependency | Raise `ExecutionGraphError` |
| Dependency cycle | Raise `ExecutionGraphError` |
| Failed prerequisite | Downstream node is not ready |
| Same-file edits | Serialized deterministically |
| Required test barrier | Downstream nodes wait for test completion |
| Recovery insertion | Bounded, acyclic recovery path |
| Malformed JSON | Raise `ExecutionGraphError` |

## Testing Plan

`tests/test_execution_graph.py` covers node serialization, graph validation, cycle detection, deterministic waves, same-file serialization, test barriers, failed prerequisite blocking, recovery insertion, retry bounds, and round-trip persistence.

## Rollback Plan

The first slice is additive and not wired into production execution. If it fails validation, remove the new module and tests; existing ProjectPlanner and DAGExecutor behavior remains unchanged.

## Related

- [[execution_graph]]
- [[execution_graph_task]]
- [[project_planner_spec]]
