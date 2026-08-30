---
title: "Research: Live Agent Renderer"
tags:
  - doc/research
  - phase/1
  - topic/live-agent-renderer
  - layer/interface
---

# Research: Live Agent Renderer

## Current Architecture

AWOS already emits structured execution telemetry through `scaffold/agent/gui_events.py`, persists it to `.awos/gui/<session_id>/events.jsonl`, writes session `manifest.json`, and streams events through `gui/server.py` SSE endpoints. `gui/static/agent.html` renders a partial event view inside a chat message. `tests/test_gui_events.py` covers event emission and `tests/test_gui_browser.py` provides browser-level screenshots and console/network checks.

Existing visible data includes session start, codebase indexing, plans, model routing, task start/complete, file edits, test results, tool calls, thinking, debugger findings, errors, and session completion.

## Verified Gaps

1. The current renderer is embedded in a conversation bubble rather than a persistent developer workspace.
2. Exploration is not presented as a first-class timeline/lane with files, symbols, searches, and observations.
3. Parallel tasks are not represented as isolated, simultaneously inspectable lanes.
4. Worktree, process, session, and verification state are not visible in one place.
5. Event replay and raw evidence inspection are fragmented across chat, debug reports, manifests, and filesystem artifacts.
6. Browser E2E exists, but there is no dedicated developer renderer acceptance surface.

## Design Constraints

- Reuse existing event bus, SSE, manifests, debug reports, and session APIs.
- Do not expose hidden chain-of-thought as a product requirement; show observable actions, summaries, tool calls, and evidence.
- Keep the surface simple and programmer-oriented, not dashboard-heavy.
- Make parallel tasks, worktrees, diffs, verification, and failures explicit.
- Keep the renderer useful for future autonomous workloads, not only CI.

## Summary

The highest-value next capability is a developer renderer that makes the existing agent execution observable as it happens. It should be a thin interface layer over current telemetry, with an initial vertical slice before adding new event types.

## Related

- [[live_agent_renderer_spec]]
- [[live_agent_renderer]]
- [[coding_agent_capability_audit]]
- [[VISION]]
