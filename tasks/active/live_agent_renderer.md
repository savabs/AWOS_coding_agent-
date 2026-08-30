---
title: "Task: Live Agent Renderer"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/live-agent-renderer
  - layer/interface
---

# Task: Live Agent Renderer

**Status:** active  
**Phase:** 1  
**Research:** [[live_agent_renderer]]  
**Spec:** [[live_agent_renderer_spec]]  
**Started:** 2026-07-27  
**Completed:** —

## Context

The existing AWOS agent already emits events, SSE, manifests, diffs, debugger reports, and routing telemetry, but the developer cannot see the whole execution path in one place. This task creates a thin, programmer-oriented renderer over that existing infrastructure. It does not create a new agent runtime or expose hidden reasoning as a requirement.

## Steps

### Phase 1.1: Projection
- [x] 1.1.1: Define pure event-to-view projection in `gui/renderer_state.py` → verification: fixture events produce session/timeline/task/file/verification state.
- [x] 1.1.2: Test malformed and empty event streams → verification: projection returns honest idle/error state.

### Phase 1.2: Renderer shell
- [x] 1.2.1: Add `/renderer` route → verification: route source serves `gui/static/renderer.html`.
- [x] 1.2.2: Render programmer-oriented layout → verification: entry-point test finds session, timeline, tasks, files, evidence, and verification regions.

### Phase 1.3: Live execution
- [x] 1.3.1: Connect renderer to existing SSE → verification: renderer source uses `/api/agent/events` and bounded route smoke returns 200.
- [x] 1.3.2: Render parallel task lanes → verification: projection tests keep two task IDs isolated and renderer contains lane region.
- [x] 1.3.3: Render exploration/tool events → verification: projection tests and renderer timeline expose tool action/observation data.

### Phase 1.4: Proof
- [x] 1.4.1: Render saved session replay → verification: renderer loads `/api/sessions/<id>` manifest and events; manifest replay projection is tested.
- [x] 1.4.2: Render verification and debugger evidence → verification: renderer contains verification/evidence regions and consumes persisted event payloads.
- [x] 1.4.3: Run browser smoke proof → verification: deterministic Playwright renderer smoke passes with zero console errors.
- [ ] Live proof: open `/renderer` during a representative coding run and observe the full execution path.

### Phase 1.5: Cleanup
- [x] 1.5.1: Run focused tests and lint → verification: 34 GUI/renderer tests pass, scoped docs lint is clean, and `git diff --check` is clean.
- [ ] 1.5.2: Run quality gate → verification: task gate passes after live proof.
- [x] 1.5.3: Write checkpoint → verification: `docs/memory/checkpoint_2026-07-27_7.md` records renderer proof and capability gaps found.

## Live Proof Observations

### First verified capability gap: session state projection is inconsistent

Live session observed on 2026-07-27:

- UI/runtime session: `rs_869e55201279`
- Canonical session record: `.awos/sessions/rs_869e55201279.json` reports `completed`, one completed task, and the target repository.
- SSE stream: emitted `session_done`, `task_complete`, and a passing `test_result`.
- Debug evidence bundle: `.awos/debug/orch_04f06518/evidence.jsonl` records successful completion and `3 passed`.
- GUI manifest: `.awos/gui/rs_869e55201279/manifest.json` remains `running` with zero files.
- `GET /api/sessions/rs_869e55201279` therefore reports stale `running` state and no files, while `GET /api/sessions/history` reports completed.

The server implementation reads the GUI manifest for session detail and file routes, while the canonical runtime session record is updated separately. This makes the renderer/backend misleading after completion and is the first gap to fix. The live run also exposed two blocked shell attempts and one successful test run; the narrow benchmark goal did not exercise deep repository exploration or real parallel execution.

## Blockers

| Blocker | Blocking step | Resolution needed |
|---|---|---|
| Representative live coding run has not yet been observed in the renderer | Live proof | Start a bounded coding-agent run with a real goal, then inspect `/renderer` from session start through verification. |

## Decisions Made

| Decision | Rationale | Date |
|---|---|---|
| Build a thin renderer over existing telemetry | The agent already emits the data; the gap is observability, not another runtime | 2026-07-27 |
| Keep the first renderer developer-facing | It accelerates capability debugging without overloading the primary programmer UI | 2026-07-27 |
| Show observable actions/evidence, not hidden chain-of-thought | Trust and debugging require inspectable system behavior without making private reasoning a product contract | 2026-07-27 |
| Remove secret-derived startup logging | Renderer/server diagnostics must never print API-key material, even prefixes | 2026-07-27 |

## Related

- [[live_agent_renderer]]
- [[live_agent_renderer_spec]]
- [[coding_agent_capability_audit]]
- [[pivot_web_app_product]]
