---
title: "Checkpoint: Agent Console — Transparent Back UI"
tags:
  - doc/checkpoint
  - phase/4
  - topic/agent-console
  - status/done
---

# Agent Console — Transparent Back UI

> **Session:** July 12, 2026
> **North star:** User can open a browser and *watch* the autonomous engineer working — like an automatic watch with a transparent back.

---

## What Changed

**Problem:** The web UI was branded "CI Rescue Agent" — narrow positioning that made AWOS look like a single-purpose tool. The user couldn't see the agent thinking, so the "transparent back" didn't exist.

**Fix:** Transformed `gui/static/agent.html` from a CI Rescue dashboard into a general **Agent Console** with a dedicated thinking panel showing the model's chain-of-thought in real-time.

---

## Changes

### `gui/static/agent.html`
1. **Rebrand:** Title → "AWOS Agent Console", logo → ⚡ AWOS, tagline → "Autonomous Engineering Runtime", empty state → "run `awos run`"
2. **Thinking panel:** Added collapsible "Agent Reasoning" panel that renders `agent_thinking` events in real-time. Dimmed italic monospace, auto-scroll, thought counter. This IS the transparent back.
3. **Stats sidebar:** Replaced CI Rescue metrics (Rescues/Tests Fixed/Savings) with general agent metrics (Tasks/Models Used/Cost/Efficiency). Tracks unique models per session.
4. **History:** Now fetches from `/api/sessions` (all agent sessions) instead of `/api/ci-rescue/history`
5. **Session replay:** `loadSession()` replays events from `/api/sessions/<id>/events` — historical sessions show the same transparent-back experience

### `scripts/demo_agent_interface.sh`
- Added automated verification of 8 success markers (title, thinking panel, branding, entry point, all 4 stats)

---

## Live Proof Results

| Check | Status |
|-------|--------|
| Title: "AWOS Agent Console" | ✅ |
| Thinking panel present in DOM | ✅ |
| No CI Rescue branding | ✅ |
| Shows "awos run" entry point | ✅ |
| Stat label: Tasks | ✅ |
| Stat label: Models | ✅ |
| Stat label: Cost | ✅ |
| Stat label: Efficiency | ✅ |

**All 8 markers observed.** See `scripts/demo_agent_interface.sh` for automated verification.

---

## Infrastructure Already in Place (not changed)

| Component | Status |
|-----------|--------|
| All 9 event types defined in `gui_events.py` | ✅ |
| All 9 emit helpers implemented | ✅ |
| All 9 events emitted from orchestrator | ✅ |
| SSE endpoint `/api/agent/events` tails events.jsonl | ✅ |
| `test_gui_events.py` — 20 tests pass | ✅ |
| Sessions API `/api/sessions` lists all runs | ✅ |
| Sessions events API `/api/sessions/<id>/events` | ✅ |

---

## State at End

| Metric | Value |
|--------|-------|
| Agent Console task | 5/5 steps done ✅ |
| gui_events tests | 20/20 pass |
| Ruff (gui/server.py, gui_events.py) | clean |
| Live proof markers | 8/8 observed |

---

## What It Unlocks

The transparent back exists. A user can now:
1. Run `awos run "fix failing tests"` + `awos serve`
2. Open `http://localhost:8765/agent`
3. **Watch** the agent plan, choose models, reason about code, edit files, run tests — all streaming in real-time
4. Click any past session and see the same event replay

CI Rescue is now one skill of many, not the product identity.

---

## Next

- **Routing transparency card** — a visual panel showing model choice + past success rate + override capability (next UX layer)
- **Agency pilot outreach** — the demo experience is now ready to show
- **PEI trend dashboard** — show "Your AI improved X% this month"

---

## Related

- [[agent_console]] — task file (5/5 done)
- [[agent_console_spec]] — spec
- [[checkpoint_2026-07-10_routing_repair]] — previous context engineering work
- [[gui_events]] — event system (unchanged, was already complete)
