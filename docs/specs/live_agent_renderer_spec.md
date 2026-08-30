---
title: "Spec: Live Agent Renderer"
tags:
  - doc/spec
  - phase/1
  - topic/live-agent-renderer
  - layer/interface
---

# Spec: Live Agent Renderer

## Goal

Provide a dedicated programmer-facing renderer that shows the existing coding agent's live execution, evidence, task lanes, diffs, and verification without changing the agent runtime.

**Exit condition:** a developer can open the renderer, observe a live run from session start through completion/failure, inspect parallel task state and evidence, and replay a saved session with no console errors.

## Research Reference

Research: [[live_agent_renderer]] — telemetry already exists but is fragmented across chat, SSE, manifests, debug reports, and filesystem state.

## Files Affected

**New files:**
- `gui/renderer_state.py` — pure event/manifest-to-view projection.
- `gui/static/renderer.html` — dedicated developer renderer.
- `tests/test_live_agent_renderer.py` — projection and renderer contract tests.

**Modified files:**
- `gui/server.py` — serve renderer route and expose only required workload-neutral session/evidence data if missing.
- `tests/test_gui_browser.py` — add renderer smoke checks if the existing browser harness supports it.
- `gui/AGENT_INTERFACE.md` — document developer renderer entry point.

**Files explicitly NOT touched:**
- `scaffold/agent/orchestrator.py`, worker, planner, verifier, and routing logic in the first vertical slice.
- `.awos/` runtime artifacts.
- Existing chat renderer behavior except for a link/navigation entry if required.

## Implementation Steps

### Step 1: Define the renderer data projection

Verification: a saved `events.jsonl` plus manifest can be projected into session, timeline, task-lane, file, and verification sections by a pure testable function.

### Step 2: Serve the renderer entry point

Verification: `GET /renderer` returns `renderer.html` with no 404.

### Step 3: Render live event updates

Verification: fixture events update the timeline and current task state without page reload.

### Step 4: Render parallel task lanes

Verification: events for two task IDs produce two isolated visible lanes with status and current action.

### Step 5: Render evidence and file state

Verification: file edits, diffs, debugger findings, tests, and final outcome are visible from fixture data.

### Step 6: Add browser smoke proof

Verification: browser test opens `/renderer`, injects/loads fixture state, and reports no console or network errors.

## Interface Contract

Renderer route: `GET /renderer`

Minimum projected state:
- session identity, goal, status, elapsed time
- live event timeline
- current model/provider/routing reason
- exploration/tool actions and observations
- task lanes with status and worktree/task identity when available
- files and diff summary
- verification/test outcomes
- debugger evidence and final result

## Edge Cases

| Scenario | Expected behavior |
|---|---|
| No active session | Show an idle state and explain how to start a coding task. |
| Event file missing or malformed | Show an honest unavailable/error state; do not crash the renderer. |
| Session completes before page opens | Load and replay saved manifest/events. |
| Two tasks run concurrently | Keep task state isolated by session/task ID. |
| Sensitive tool output | Truncate or redact according to existing event contracts. |

## Live Proof

Run the browser renderer smoke test and observe the renderer load, event timeline, task lanes, evidence panel, and zero console/network errors.

## Related

- [[live_agent_renderer]]
- [[live_agent_renderer]]
- [[coding_agent_capability_audit]]
- [[pivot_web_app_product]]
