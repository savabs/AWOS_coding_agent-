# Agent Interface Pathway (Active Task)

> **Created:** 2026-07-03
> **Corrected:** 2026-07-26
> **Phase:** 1 — Proper Coding Agent
> **Goal:** Make the app the right interface for talking to the coding agent.

---

## Context

This task is no longer about a CI-wedge web product, customer pilot flow, or pricing surface.

It is about making the app a strong interface to the coding agent so a user can:
- give goals
- inspect execution
- review diffs
- see verification
- understand failures
- understand what the system learned

The app exists to expose the coding agent clearly.

The interface should stay simple and programmer-oriented: users should feel like they are directing and inspecting engineering work, not operating an overloaded product dashboard. It must make parallel tasks understandable and actionable while preserving task isolation, file/worktree context, progress, diffs, and verification evidence.

This is an implementation of the existing AWOS agent, not a rewrite from scratch and not a copy of Cursor, Zed, Hermes, or another product. Those systems define a capability bar; AWOS must meet that bar through its own kernel, verification, recovery, observability, and failure-driven learning architecture.

---

## What stays the same

- Kernel loop: Plan → Execute → Verify → Reward → Learn
- Objective function: Quality × Speed ÷ Cost
- Transparent routing
- `.awos/` state
- Coding agent as App #1
- Deep, bounded repository exploration before edits
- Parallel task execution with explicit task/worktree state

---

## What changes

- no CI-wedge-first framing
- no agency / billing / pilot roadmap in this task
- no product copy that defines the app as anything other than the interface to the coding agent

---

## Implementation steps

### Phase 1a — Core interface
- [x] 1. Make `awos serve` a coding-agent dashboard, not a CI-only dashboard
- [~] 2. Show transparent routing, task flow, and execution state clearly
- [ ] 3. Show verification outcomes clearly
- [ ] 4. Show failure/retry state clearly

### Phase 1b — Workspace interaction
- [ ] 5. File editor integration
- [ ] 6. File references and diffs in the conversation flow
- [ ] 7. Workspace switching / management

### Phase 1c — Self-learning inspection
- [ ] 8. Show failure patterns and recoveries
- [ ] 9. Show what changed because of prior failures (prompt/tool/routing/verifier)
- [ ] 10. Add inspection surfaces for learned state without overselling it

### Phase 1d — Packaging
- [ ] 11. Keep Electron / app shell aligned to coding-agent-first identity
- [ ] 12. Keep web/desktop entry points consistent

---

## Deferred from this task

- billing
- customer pilots
- agency dashboard
- pricing pages
- CI-wedge positioning
- broad hosted GTM work

---

## Key decisions

1. The app is the interface to the coding agent
2. CI repair may appear as one workflow, not the whole app identity
3. Learning surfaces should show failure-driven improvement, not just generic savings/compounding language
4. UI simplicity and programmer ergonomics take priority over dashboard density
5. Parallel tasks must remain observable, isolated, and reviewable
6. Exploration evidence is part of the agent experience, not hidden implementation detail

---

## Related

- [[VISION]] — canonical identity
- [[recenter_self_improving_coding_agent]] — current alignment task
- [[TRANSPARENT_ROUTING]] — routing UX spec
- [[memories/repo/project_structure]] — current facts
