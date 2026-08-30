---
title: "Research: Typed Execution Graph"
tags:
  - doc/research
  - phase/1
  - topic/execution-graph
  - layer/orchestration
---

# Typed Execution Graph

## Current Architecture

AWOS has `ProjectPlanner` for persistent goal/task state, `DAGExecutor` for dependency waves, `RuntimeSession` for pause/resume, and `Verifier` for evidence. The missing layer is a canonical graph that represents edits, tests, barriers, and recovery—not only goal tasks.

## Observations

- `DAGExecutor` already uses dependency waves and a four-worker cap.
- Planner task dictionaries already carry file, action, acceptance, and evidence fields.
- ProjectPlanner persists task status but does not yet model typed execution operations.
- Tests must be first-class graph nodes so downstream work can be gated on evidence.
- Recovery must add explicit nodes rather than silently retrying outside the graph.

## Proposed Boundary

The advanced Planner proposes a graph. A deterministic graph compiler validates and canonicalizes it. The executor runs ready waves. Runtime evidence updates node status and can trigger validated recovery nodes.

The first implementation slice is provider-free and stdlib-only: typed nodes, dependency validation, deterministic ready waves, barrier semantics through dependencies, JSON round-tripping, and recovery-node insertion. It does not yet replace the production orchestrator loop.

## Risks

- Cycles or missing dependencies must fail closed.
- Parallel edits can conflict; same-file nodes must be serialized.
- A failed required test must not unlock downstream nodes.
- Mutable graph state needs deterministic serialization for resume and audit.
- The graph must not expose hidden model reasoning; only actions, evidence, and status are stored.

## External Sources

| Source | URL | Notes |
|---|---|---|
| Python `graphlib` docs | https://docs.python.org/3/library/graphlib.html | `TopologicalSorter` defines dependency ordering and ready-node processing suitable for parallel waves. |
| ExoFlow, USENIX OSDI 2023 | https://www.usenix.org/conference/osdi23/presentation/zhuang | Separates execution from recovery and treats checkpointing as a durable workflow concern. |
| Hatchet DAG documentation | https://docs.hatchet.run/v1/directed-acyclic-graphs | Documents persisted task status, parallel independent tasks, joins, and retries as workflow concepts. |

## Summary

Build a small, explicit execution-graph contract above ProjectPlanner: the advanced Planner proposes typed operations, the compiler validates them, and the runtime can execute safe ready waves while tests and recovery remain visible nodes.

## Related

- [[execution_graph_spec]]
- [[execution_graph_task]]
- [[project_planner]]
- [[dag_executor_task]]
