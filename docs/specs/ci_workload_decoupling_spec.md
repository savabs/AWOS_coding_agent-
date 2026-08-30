---
title: "Spec: CI Workload Decoupling"
tags:
  - doc/spec
  - phase/1
  - topic/ci-workload-decoupling
  - layer/kernel
---

# Spec: CI Workload Decoupling

## Goal

Remove CI-rescue naming from the canonical session interface while preserving CI repair as an optional coding-agent workload and maintaining backward compatibility.

**Exit condition:** generic session endpoints and documentation work, legacy CI aliases remain functional, CI mission-loader tests pass, and no reusable kernel module gains CI assumptions.

## Research Reference

Research: [[ci_workload_decoupling]] — generic orchestration is already separate from CI adapters; the main identity leak is the canonical GUI statistics/history contract.

## Files Affected

**Modified files:**
- `gui/server.py` — add generic session stats/history methods and canonical routes; delegate legacy aliases.
- `gui/static/dashboard.js` — use generic session routes.
- `gui/AGENT_INTERFACE.md` — document generic routes and label CI aliases as compatibility endpoints.
- `awos.py` — clarify CI commands are optional workload adapters in CLI help without removing them.
- `tests/test_ci_workload_boundary.py` — add route/contract regression tests if server testing seam permits.

**Files explicitly NOT touched:**
- `scaffold/agent/orchestrator.py` and generic kernel contracts.
- CI discovery/planner/TUI modules and benchmark fixtures.
- Provider/routing implementation.
- `.awos/` runtime state.

## Implementation Steps

### Step 1: Add generic server contracts

Verification: generic session stats/history methods return the existing session-derived schema.

### Step 2: Add canonical generic routes

Verification: `/api/sessions/stats` and `/api/sessions/history` are handled by the server.

### Step 3: Preserve CI route aliases

Verification: `/api/ci-rescue/stats` and `/api/ci-rescue/history` return the same payloads as generic routes.

### Step 4: Update dashboard and interface documentation

Verification: dashboard fetches generic routes and docs identify CI routes as compatibility aliases.

### Step 5: Clarify CLI workload boundaries

Verification: CLI help describes `fix-ci` and CI mission flags as optional benchmark/workload workflows.

### Step 6: Run focused regression tests

Verification: `python3 -m pytest tests/test_ci_workload_boundary.py tests/test_mission_plan_loader.py tests/test_ci_discovery.py tests/test_ci_planner.py -q` passes.

## Interface Contract

Canonical endpoints:
- `GET /api/sessions/stats`
- `GET /api/sessions/history`

Compatibility endpoints:
- `GET /api/ci-rescue/stats`
- `GET /api/ci-rescue/history`

Both endpoint families return the same payloads during migration.

## Edge Cases

| Scenario | Expected behavior |
|---|---|
| No runtime sessions | Return zeroed stats and an empty history list. |
| Legacy CI route requested | Return the generic payload without error. |
| Existing CI mission selected | Preserve current mission configuration and plan loading. |
| Generic coding task selected | No CI-specific code is required in the orchestrator. |

## Live Proof

Run the focused test command and inspect CLI help for optional CI workload wording. Observe all tests passing and both endpoint families sharing the generic implementation.

## Related

- [[ci_workload_decoupling]]
- [[ci_workload_decoupling]]
- [[pivot_web_app_product]]
