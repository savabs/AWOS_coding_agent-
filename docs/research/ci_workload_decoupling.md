---
title: "Research: CI Workload Decoupling"
tags:
  - doc/research
  - phase/1
  - topic/ci-workload-decoupling
  - layer/kernel
---

# Research: CI Workload Decoupling

## Current Architecture

AWOS documents the orchestrator as a domain-agnostic kernel with coding-agent app layers. The CLI currently exposes both generic worker/mission commands and CI-specific commands. The web server exposes generic session data through legacy `/api/ci-rescue/*` routes.

Relevant files:
- `scaffold/agent/__init__.py` — states kernel/app boundary.
- `scaffold/agent/orchestrator.py` — generic plan/execute/verify/learn loop.
- `awos.py` — generic worker/mission CLI plus CI-specific mission flags and `cmd_fix_ci` adapter.
- `gui/server.py` — session statistics/history currently named CI rescue, plus routing/debug APIs.
- `gui/static/dashboard.js` — consumes the legacy CI route names.
- `gui/AGENT_INTERFACE.md` — documents the legacy routes.
- `tests/test_mission_plan_loader.py` — protects the CI benchmark workload configuration.

## Observations

1. The generic orchestrator does not need to become CI-specific.
2. CI-specific discovery/planning/TUI modules are useful as a coding-agent workload adapter and should remain available.
3. The CLI's `cmd_fix_ci` is an explicit optional workflow, not proof that the kernel is CI-only.
4. The dashboard's canonical statistics/history routes are still named `ci-rescue`, which leaks the old product framing.
5. Existing clients may depend on the old routes, so aliases are safer than removal.
6. Mission configuration already supports a generic `--config` path; CI flags can remain compatibility/workload selectors.

## Risks

- Removing CI flags or modules could break benchmark proofs and existing users.
- Renaming response fields could break the dashboard.
- Moving CI logic into the kernel would worsen the boundary; the adapter must call generic orchestration.
- A broad rewrite would mix this boundary cleanup with unrelated UI and routing work.

## Summary

The safe change is a boundary cleanup: make session statistics/history generic at the server and dashboard contract level, document CI as an optional adapter, and preserve CI command/route aliases for compatibility. Do not delete CI workload code or alter the generic orchestrator in this slice.

## Related

- [[ci_workload_decoupling_spec]]
- [[ci_workload_decoupling]]
- [[VISION]]
- [[pivot_web_app_product]]
