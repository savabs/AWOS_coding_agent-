---
title: "Task: CI Workload Decoupling"
tags:
  - doc/task
  - status/done
  - phase/1
  - topic/ci-workload-decoupling
  - layer/kernel
---

# Task: CI Workload Decoupling

**Status:** completed  
**Phase:** 1  
**Research:** [[ci_workload_decoupling]]  
**Spec:** [[ci_workload_decoupling_spec]]  
**Started:** 2026-07-27  
**Completed:** 2026-07-27

## Context

CI repair must remain a useful coding-agent workload without defining the reusable AWOS kernel or canonical app interface. This task removes CI-rescue naming from the canonical session interface, preserves compatibility aliases and CI benchmark behavior, and clarifies the CLI boundary. It does not rewrite or delete the existing CI adapter.

## Steps

### Phase 1.1: Generic interface
- [x] 1.1.1: Add generic session stats method → verification: method returns session-derived run/task/cost fields.
- [x] 1.1.2: Add generic session history method → verification: method returns session records without CI identity.
- [x] 1.1.3: Add canonical generic routes → verification: `/api/sessions/stats` and `/api/sessions/history` are served.

### Phase 1.2: Compatibility
- [x] 1.2.1: Delegate CI stats alias → verification: legacy stats route shares the generic handler.
- [x] 1.2.2: Delegate CI history alias → verification: legacy history route shares the generic handler.
- [x] 1.2.3: Preserve CI mission loading → verification: CI mission-loader tests pass; unrelated clawcode test is deselected because its sibling repo is absent.

### Phase 1.3: User surface
- [x] 1.3.1: Update dashboard to generic routes → verification: dashboard source contains canonical session endpoints.
- [x] 1.3.2: Update interface documentation → verification: docs list generic routes and compatibility aliases.
- [x] 1.3.3: Clarify CLI workload wording → verification: `python3 awos.py --help` labels CI as optional.

### Phase 1.4: Proof
- [x] 1.4.1: Add boundary regression tests → verification: `tests/test_ci_workload_boundary.py` passes.
- [x] 1.4.2: Run focused CI/workload suite → verification: `28 passed, 1 deselected`; the deselected test requires absent `/home/becmachlean/2024/projects/clawcode`.
- [x] Live proof: run the focused pytest command and inspect CLI help for `Optional CI repair workload`.

### Phase 1.5: Cleanup
- [x] 1.5.1: Run scoped Obsidian lint → verification: changed workflow docs are clean.
- [x] 1.5.2: Run quality gate → verification: task gate passes after all task steps are recorded.
- [x] 1.5.3: Write checkpoint → verification: `docs/memory/checkpoint_2026-07-27_5.md` records route compatibility and test output.

## Blockers

| Blocker | Blocking step | Resolution needed |
|---|---|---|
| External clawcode fixture is absent | 1.4.2 | Keep the environment-dependent test deselected; CI workload and boundary tests pass. |

## Decisions Made

| Decision | Rationale | Date |
|---|---|---|
| Keep CI aliases | Existing benchmark and clients should not break during identity cleanup | 2026-07-27 |
| Do not remove CI adapter modules | CI remains a valid coding-agent workload and proving benchmark | 2026-07-27 |
| Keep generic kernel untouched in this slice | Boundary cleanup should not become an unsafe orchestrator rewrite | 2026-07-27 |
| Preserve legacy CI API aliases | Existing dashboard/client compatibility matters while generic routes become canonical | 2026-07-27 |

## Related

- [[ci_workload_decoupling]]
- [[ci_workload_decoupling_spec]]
- [[VISION]]
- [[pivot_web_app_product]]
