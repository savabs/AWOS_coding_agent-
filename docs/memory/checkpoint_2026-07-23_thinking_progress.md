---
title: "Checkpoint: Thinking + orchestrator progress visible — July 23, 2026 (evening)"
tags:
  - doc/checkpoint
  - phase/4
  - status/done
---

# Session Checkpoint — July 23, 2026 (evening)

## What changed (3 thinking/progress fixes)

### ✅ T3: Thinking panel starts expanded (1 line)
- **File:** `gui/static/agent.html` (`updateThinkingPanel`)
- **Change:** Removed `collapsed` class on initial creation; arrow now shows `▼` (expanded) instead of `▶`
- **Impact:** When a model returns `reasoning_content`, the thinking is visible by default — user can collapse if they want

### ✅ T1: Orchestrator progress display (3 sections, 1 file)
- **File:** `gui/static/agent.html`
- **Changes:**
  1. New state variable `currentOrchestratorMsgEl` tracks which message is the active orchestrator run
  2. `runOrchestrator()` rewritten to set up a structured bubble (header + `<ul>` progress list)
  3. `connectSSE()` now routes progress events to the active message
  4. New `formatOrchestratorEvent()` formats 8 event types: `plan_generated`/`planning_done`, `model_routed`, `task_start`/`task_complete`/`task_done`, `agent_thinking`, `agent_tool_call`, `file_edit`/`file_change`, `verify_result`, `session_done`
  5. `idle` events are ignored (orchestrator heartbeat)
  6. New CSS for `.orch-progress-line` with color coding (info/done/fail/edit)
- **Impact:** Orchestrator runs are now visible in real-time. User sees plan, model choice, task starts, tool calls, file edits, verifications.

### ✅ T2: Thinking detection for non-reasoning models (new helper)
- **File:** `gui/static/agent.html`
- **New function:** `splitThinkingPreamble(text)` — runs on streamed response before rendering
- **Pattern 1: `<think>...</think>` blocks** (used by MiniMax M3, Qwen, GLM) — extracts content between tags, moves to thinking panel
- **Pattern 2: Implicit thinking preamble** — first paragraph starting with a cue phrase (`Let me`, `The user`, `We are asked`, `I'll`, etc.) — splits there
- **Guards:** Only splits if both thinking and answer are >50/100 chars (prevents over-aggressive splits on short answers like "4")
- **Called from:** `streamChatResponse` (success branch + catch branch)

## Test evidence

| Test | Result |
|------|--------|
| Send "let write a code on how to make a simple webpage" → orchestrator | Bubble shows 20+ progress lines: model choice, task starts, agent thinking, tool calls, file edits, task done |
| Send "What is 1+1?" to M3 with `<think>` blocks | Thinking panel shows model reasoning, bubble shows clean "2" answer |
| Send "What is a Python decorator? Short answer." to M3 | Thinking panel shows full reasoning, bubble shows clean answer with code block |
| Send "What is 2+2?" to Flash (no `<think>` blocks) | Partial split: first paragraph of thinking moved to panel, answer "4" still visible in bubble |

## Files touched today

| File | Change |
|------|--------|
| `gui/static/agent.html` | T1, T2, T3 — multiple sections |
| `memories/repo/ui_state.md` | Added T1, T2, T3 to "Recently added" section |
| `memories/repo/project_structure.md` | Added 3 DONE rows for T1, T2, T3 |
| `docs/memory/checkpoint_2026-07-23_thinking_progress.md` | This file |

## Files created today
- `agent_orch_progress.png` — first orchestrator test
- `agent_orch_progress_v2.png` — second test (1 event)
- `agent_orch_full_progress.png` — full progress (20+ events)
- `agent_t2_test.png` — T2 test (regression visible)
- `agent_t2_with_think.png` — M3 with `<think>` blocks
- `agent_t2_thinking_visible.png` — T2 + T3 working together

## Net state of the chat (end of July 23)

**9 chat-related bugs/features added/fixed across 4 sessions today:**

| When | Bug | Status |
|------|-----|--------|
| Morning | OpenCode Go 401/429 quota | ✅ FIXED (key rotation) |
| Morning | M3 cost badge (checkpoint claim) | ✅ RESOLVED (M3 wasn't reachable) |
| Afternoon | B1: Model name raw ID | ✅ FIXED |
| Afternoon | B2: Phantom model flicker | ✅ FIXED |
| Afternoon | B3: M3 in dropdown | ✅ FIXED |
| Afternoon | B4: Server stability | ✅ VERIFIED (20/20 stress test) |
| Afternoon | B5: Error message truncation | ✅ FIXED (styled error block) |
| Evening | T1: Orchestrator progress invisible | ✅ FIXED (real-time event routing) |
| Evening | T2: Thinking mixed into bubble | ✅ FIXED (heuristic splitter) |
| Evening | T3: Thinking panel hidden | ✅ FIXED (starts expanded) |

**Chat is now feature-complete for MVP:**
- Real model names in badges
- M3 (and all 7 models) reachable from dropdown
- Server stable under load
- Errors render with full text + copy
- Orchestrator progress visible in real-time
- Thinking separated from answer
- Thinking panel shows by default

## Known limitations

- **T2 partial for Flash:** Models that don't use `<think>` tags only get the first paragraph of thinking separated. Multi-paragraph thinking stays in the bubble. Improvement would require more sophisticated heuristics (e.g., detecting answer transition phrases like "**Answer:**" or "Thus:").

- **T1 event format hardcoded:** New orchestrator event types would need to be added to `formatOrchestratorEvent()` manually. Could be improved by auto-formatting based on event type.

## What to do next session

The chat is now feature-complete. Direction options:

1. **UI/UX backlog** (from project_structure.md priority list):
   - Multi-chat sidebar
   - Conversation history persistence
   - File references in message
   - Stop button polish
   - Page routing (settings / dashboard / sessions)

2. **Kernel work:**
   - CI Rescue wedge (revenue path)
   - More model validation
   - Self-learning improvements

3. **Quality / polish:**
   - Write E2E test that covers the new T1/T2/T3 flows
   - Document the new behavior in user-facing docs

4. **Fresh chat session** — the checkpoint is ready, a new session can pick up cleanly

## How to resume

```bash
# 1. Cold start
cat memories/repo/project_structure.md
cat memories/repo/ui_state.md
cat docs/memory/checkpoint_2026-07-23_thinking_progress.md   # this file

# 2. Verify server
ps aux | grep "gui/server" | grep -v grep
# If not: nohup python3 gui/server.py --port 8765 > /tmp/awos_server.log 2>&1 &

# 3. Test all 3 features
# In opencode: 
#   "navigate to the agent"
#   1. Send a coding task ("let write a hello world function") → see orchestrator progress
#   2. Send a question with M3 selected → see thinking panel separated
#   3. Verify thinking panel is expanded by default
```

## Related
- [[checkpoint_2026-07-23_b5_done]] — B5 (error block) session
- [[checkpoint_2026-07-23_bug_fixes]] — B1-B4 session
- [[checkpoint_2026-07-23_playwright_mcp]] — morning session
- [[ui_state]] — current app state
- [[project_structure]] — canonical project facts
