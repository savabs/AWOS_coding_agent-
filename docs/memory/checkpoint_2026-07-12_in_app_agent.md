---
title: "Checkpoint: In-App Agent Execution — Type a Goal, Click Run, Watch It Work"
tags:
  - doc/checkpoint
  - phase/4
  - topic/native-app
  - topic/agent-console
  - status/done
---

# In-App Agent Execution

> **Date:** July 12, 2026
> **Theme:** The app is now self-contained. No terminal needed.

---

## What Changed

**Problem:** To use AWOS, you had to:
1. Start the server (`python3 gui/server.py`)
2. Open the browser
3. Run `awos run "goal"` in another terminal
4. Watch in the browser

Three things to do before you could even start. Felt like a tool, not a product.

**Fix:** Added an in-app command bar. Now you:
1. Open the AWOS app
2. Type what you want
3. Press Enter (or click Run)
4. **Watch it happen** — all inside the app

---

## Changes

### 1. `gui/server.py` — New API endpoint
- `POST /api/agent/run` accepts `{goal: string, root: string?}`
- Spawns `python3 -m awos run "goal" --root <root>` as a background subprocess
- Returns `{status: "started", goal, root}` immediately
- The subprocess writes events to `.awos/gui/<session_id>/events.jsonl`
- The existing SSE endpoint streams them to the UI automatically

### 2. `gui/static/agent.html` — Command bar
- Apple-style input bar pinned at the bottom of the app
- Text input: "Describe what you want the agent to do..." with autofocus
- "▶ Run" button with hover/active states
- Enter key triggers run (Shift+Enter unaffected)
- When running: button shows "● Running", input disabled
- When `session_done` arrives: input re-enables and focuses
- `runGoal()` POSTs to `/api/agent/run`, clears stream, starts fresh session
- Error handling: button re-enables on failure

---

## Live Proof

### UI verification (7/7)
| Check | Status |
|-------|--------|
| Command bar exists | ✅ |
| Goal input exists | ✅ |
| Run button with "▶ Run" | ✅ |
| `runGoal()` function defined | ✅ |
| API endpoint reference | ✅ |
| Input autofocus | ✅ |
| Empty state updated | ✅ |

### API verification
```json
POST /api/agent/run {"goal": "list files", "root": "."}
→ {"status": "started", "goal": "list files", "root": "."}
```

---

## The Full User Flow (from app open to result)

```
1. Open AWOS app
   → Frameless dark window with 🧠 Reasoning sheet, sidebar, command bar
   
2. Type: "fix the failing tests" + press Enter
   → "● Running" appears on button
   → POST /api/agent/run starts the orchestrator
   
3. Watch events stream in:
   → 🧠 Reasoning sheet: model chain-of-thought
   → 📋 Plan: "Generated 4 tasks"
   → 🧠 Router: "DeepSeek (T2) — Best cost/quality"
   → 📝 file_edit: "auth.py modified"
   → ✅ test_result: "test_login passed (1.2s)"
   → ✓ task_complete: "Task 1 done · $0.0012"
   
4. Session completes:
   → "Session complete: 4/4 tasks · $0.0056 · 49.7s"
   → Input re-enables, ready for next goal
   → Dock stops bouncing
```

---

## Files Changed

| File | Change | Purpose |
|------|--------|---------|
| `gui/server.py` | +import subprocess, +POST /api/agent/run | Spawn `awos run` as subprocess |
| `gui/static/agent.html` | +command bar CSS + HTML + JS | In-app goal input + Run button + Enter handler + session_done reset |

---

## State at End

| Metric | Value |
|--------|-------|
| awos_app task | 7/7 steps done ✅ |
| UI markers | 7/7 pass |
| API test | `{"status":"started"}` ✅ |
| Ruff (server.py) | clean |

---

## Related

- [[awos_app]] — task file
- [[checkpoint_2026-07-12_awos_app]] — previous checkpoint (Electron + Apple design)
- [[checkpoint_2026-07-12_agent_console]] — agent console redesign
