---
title: AWOS Agent UI — Current State
tags:
  - doc/memory
  - phase/4
  - topic/agent-ui
  - topic/snapshot
---

> **Canonical owner for "what the app looks like right now."**
> All agents read this on session start instead of re-reading `gui/static/agent.html`.
> Last refreshed: 2026-07-23 (live test) + 2026-07-23 (source). Update whenever UI changes.

---

## 1. File Map (Agent Console surfaces)

| File | Lines | Role |
|------|-------|------|
| `gui/static/agent.html` | 582 | **Main agent UI** — chat-only view (no sidebar yet) |
| `gui/static/dashboard.html` | ~250 | Dashboard with chat sessions sidebar + chat view (multi-chat, mostly wired but unpolished) |
| `gui/static/index.html` | 215 | Entry point — redirects to `/agent` |
| `gui/static/chat.js` | 422 | Dashboard chat wiring (legacy, multi-chat logic) |
| `gui/static/dashboard.js` | 244 | Dashboard stats + routing performance |
| `gui/static/app.js` | ~150 | Legacy app init |
| `gui/static/style.css` | 488 | Shared styles |
| `gui/static/shared.js` | 110 | Shared JS helpers |
| `gui/server.py` | 795 | Python HTTP server, SSE chat, agent events |
| `scaffold/agent/gui_chat.py` | 1372 | SSE chat service (modes: qa, plan, agent) |
| `scripts/e2e_test.js` | 134 | Playwright e2e (22/23 passing) |
| `scripts/visual_verify.js` | — | Headless Chrome screenshot + DOM dump |
| `scripts/visual_proof.js` | — | DOM inspection script |

---

## 2. What the user sees — Agent Console (`/agent`)

### Titlebar (38px, draggable)
- Left: ⚡ icon + "AWOS" name + status dot (`tb-dot`)
  - idle: gray (`var(--faint)`)
  - running: blue pulsing animation (`var(--accent)` + 1.5s pulse)
  - done: green for 5s then back to gray
- Right: ⌘K button → opens Command Palette

### Empty state (first load)
- Centered ⚡ icon (48px, 30% opacity)
- "AWOS Agent" h2
- Subtitle: "Ask anything about your codebase or run autonomous tasks."
- 6 quick-action buttons: Kernel architecture / Model routing / Project structure / Budget system / Orchestrator / Ask anything...
- Clicking a button auto-fills prompt + sends

### Conversation area (`#conv`)
- Max-width 680px, centered
- Padding 24px horizontal
- Scrolls vertically; auto-scrolls to bottom on new content
- User messages: right-aligned, blue (`var(--accent)`) bubble, 👤 avatar
- Assistant messages: left-aligned, surface bubble, 🤖 avatar
- **Streaming assistant message** has:
  - Pulsing blue dot indicator (3 dots, 0.2s/0.4s delay)
  - Blue border on bubble (`var(--accent)`)
  - Meta shows "thinking..." then model name when usage event arrives

### Message meta line (below bubble, 11px, `--faint`)
Pieces shown in order when available:
1. Green dot
2. Model name (e.g., "DeepSeek V4 Pro") — `class="model"`
3. Elapsed time (e.g., "1234ms") — `class="time"`
4. TTFB (e.g., "TTFB 1500ms") — `class="time"`
5. Tokens (e.g., "1.2k in 0.3k out") — `class="time"`
6. Cost (e.g., "$0.0012", green) — `class="cost"`
7. Files edited (e.g., "📄 foo.py, bar.py") — `class="files"`

### Message actions (hover-only, 11px)
- 📋 Copy — copies full text
- 🔄 Regenerate — re-sends original question
- 🗑 Delete (red on hover) — removes message from DOM only (no DB)

### Thinking panel (collapsible, inside assistant message)
- Toggle: "💭 Thinking ▶" / "💭 [Model] thinking ▼"
- Panel: 200px max-height, scrollable, monospace 11px, blue left border
- Auto-expands during streaming, collapses when done

### Input bar (bottom, fixed)
- `<select id="model-select">` on left:
  - 🤖 Auto (best match) — default empty value, lets AWOS escalate
  - Cheapest: DeepSeek V4 Flash — $0.14/MTok
  - Balanced: DeepSeek V4 Pro / MiMo V2.5 Pro — $0.44/MTok / **MiniMax M3 — $0.30/$1.20 per MTok** (added 2026-07-23)
  - Powerful: Grok 4.5 / GLM-5.2 — $2.00/$1.40
- `<input id="prompt-input">` — flex 1, placeholder "Ask a question or describe a task…"
- `<button id="prompt-btn">` — Send (blue) → Stop (red ■) when running

### Command palette (⌘K)
- 500px wide, frosted glass (rgba(37,37,41,.96) + 30px blur)
- Centered vertically 18% from top
- Input + 2 items: "⚡ Ask or run a task" / "💬 Clear conversation"
- Esc closes

---

## 3. Routing Logic (question vs task)

In `agent.html` `send()`:
```js
const QUESTION_RE = /^(explain|what|how|why|describe|tell me|show me|who|where|when|can you|is there)\b/i;
if (QUESTION_RE.test(msg)) {
  await streamChatResponse(msg, msgEl, t0, abortController.signal);
} else {
  await runOrchestrator(msg, msgEl, t0);
}
```

- **Starts with question word** → `streamChatResponse()` → POST `/api/chat/stream` (mode=qa)
- **Starts with verb/imperative** → `runOrchestrator()` → POST `/api/agent/run` → SSE `/api/agent/events`

---

## 4. SSE Event Flow (chat mode)

Frontend listens for these `event:` types in `/api/chat/stream`:

| Event | Frontend handler | Effect |
|-------|------------------|--------|
| `start` | (initial) | Sets `chat_id` |
| `status` | `updateMessageMeta(msgEl, model, message, files, lastUsage)` | Updates meta line w/ status text |
| `thinking` | `updateThinkingPanel(msgEl, text)` | Appends to thinking panel |
| `token` | debounced 30ms → `updateMessageBubble(msgEl, answer)` | Renders markdown + adds copy buttons |
| `usage` | `updateMessageMeta(msgEl, modelUsed, '', files, lastUsage)` | Renders model/TTFB/tokens/cost |
| `error` | throws → caught → `Failed: ${msg}` | Shows error in bubble |
| `done` / `cancelled` | (terminal) | Saves to history |

**Debounce:** Frontend batches token events with 30ms timeout before re-rendering markdown.

---

## 5. Model Tiers (OpenCode Go)

Defined in `_OC_MODEL_TIERS` (gui_chat.py:1010):

| Tier | Model ID | Display name | $in / $out | Protocol | TTFB |
|------|----------|--------------|------------|----------|------|
| T0 | `deepseek-v4-flash` | DeepSeek V4 Flash | 0.14 / 0.28 | openai | 1.5s |
| T1 | `deepseek-v4-pro` | DeepSeek V4 Pro | 0.435 / 0.87 | openai | 1.5s |
| T2 | `mimo-v2.5-pro` | MiMo V2.5 Pro | 0.435 / 0.87 | openai | 2.8s |
| T3 | `minimax-m3` | MiniMax M3 | 0.30 / 1.20 | openai | — |
| T4 | `grok-4.5` | Grok 4.5 | 2.00 / 6.00 | openai | 2.9s |
| T5 | `glm-5.2` | GLM-5.2 | 1.40 / 4.40 | openai | 3.5s |
| T6 | `minimax-m2.7` | MiniMax M2.7 | 0.30 / 1.20 | anthropic | 3.0s |
| T7 | `qwen3.7-plus` | Qwen3.7 Plus | 0.40 / 1.60 | anthropic | 8.5s |
| T8 | `kimi-k2.7-code` | Kimi K2.7 Code | 0.95 / 4.00 | openai | DOWN |

**Routing:** `force_model` from `#model-select` skips escalation; only that one model tried (30s timeout). Otherwise: start at T0, escalate on failure.

---

## 6. State Variables (frontend)

```js
let running = false;          // is a query in flight?
let abortController = null;   // for cancel
let conversation = [];        // {role, content}
let eventSource = null;       // SSE for agent mode
let activeSession = false;    // orchestrator running
let thinkingCount = 0;        // thinking tokens
let currentModel = null;      // set by usage event
let currentTime = 0;          // elapsed
```

**localStorage:** `awos-history2` — last 20 messages (goal + time), no model/response.

---

## 7. Known Bugs (refreshed 2026-07-23 by live test + fixes)

### Resolved (2026-07-23)
- ✅ **OpenCode Go 401 on chat** — was actually 429 (quota exhausted), not 401. Resolved by `load_dotenv(override=True)` in `gui/server.py:45` + API key rotation.
- ✅ **OpenCode Go 429 quota exhausted** — FIXED by API key rotation. Live test: Flash returns "4" in 2091ms, $0.0001.
- ✅ **B1: Model badge shows raw ID** — FIXED. `gui_chat.py:1069` now uses `name` from `_OC_MODEL_TIERS` tuple. Badge shows `DeepSeek V4 Flash` instead of `ocg:deepseek-v4-flash`.
- ✅ **B2: Phantom model names during streaming** — FIXED. `agent.html:420, 436` now start with empty `''` instead of hardcoded placeholders. No more flicker.
- ✅ **B3: M3 cost badge missing** — RESOLVED. M3 was always working; it just wasn't reachable from UI. Added M3 to `#model-select` dropdown (`agent.html:160`). Live test: 787 in + 83 out = `$0.0003` (math: 0.787×0.30 + 0.083×1.20 = 0.0003357 ✓).
- ✅ **B4: Server stability** — VERIFIED. 20/20 queries successful in stress test. First 3: ~2s each (cold), last 3: ~20ms (response cache). Server PID 15691 still alive after test.

### Active
(none — all known chat bugs fixed as of 2026-07-23 16:30)

### Recently added (2026-07-23 16:30): Thinking visibility + orchestrator progress

**T1 — Orchestrator progress display (FIXED):**
- Bubbles for orchestrator runs now show a live progress list
- Each event (`plan_generated`, `model_routed`, `task_start`, `task_complete`, `agent_thinking`, `agent_tool_call`, `verify_result`, `session_done`, `file_edit`) is formatted and appended in real-time
- Color-coded: blue for info, green for done, red for fail, yellow for edit
- Initial set of event types came from `_format_agent_event` in gui_chat.py; expanded to match actual events emitted (8 types)
- Location: `agent.html` — `runOrchestrator()`, `appendOrchestratorProgress()`, `formatOrchestratorEvent()`, modified `connectSSE()`

**T2 — Thinking detection in non-reasoning models (FIXED):**
- `splitThinkingPreamble()` runs on streamed response before rendering
- Pattern 1: `<think>...</think>` block (used by MiniMax M3, Qwen) → moves content into thinking panel, leaves answer in bubble
- Pattern 2: First paragraph starts with a thinking cue phrase (Let me, I'll, The user, We are asked, etc.) → splits there
- Guards: only splits if answer is >100 chars and thinking is >50 chars (prevents over-aggressive splits on short answers like "4")
- Result: M3 (with `<think>` blocks) gets perfect split; Flash (no tags) gets partial split at first paragraph
- Location: `agent.html` — `splitThinkingPreamble()`, called in `streamChatResponse` and catch branch

**T3 — Thinking panel starts expanded (FIXED):**
- `updateThinkingPanel()` no longer adds `collapsed` class on creation
- Default state: `💭 Thinking ▼` (expanded)
- User can still click to collapse
- Location: `agent.html` — `updateThinkingPanel()`

---

## 8. Test Status

- `scripts/e2e_test.js` — **22/23 passing** (Playwright + headless Chrome)
  - Tests: page load, key elements, model selector, auto-routing, forced model, copy button, no console errors
  - **Failing:** cost badge when M3 is routed (untestable from UI — M3 not in dropdown)
- `scripts/stress_test_chat.py` — server stability (20+ queries without crash)
- `scripts/profile_chat_speed.py` — chat pipeline profiler
- `scripts/visual_verify.js` — screenshot + DOM dump
- **NEW: Playwright MCP** — installed 2026-07-23, see §11

---

## 9. How to Run

```bash
# Start server
nohup python3 gui/server.py --port 8765 > /tmp/awos_server.log 2>&1 &
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/agent   # expect: 200

# Open in Playwright MCP (from inside opencode)
# Just say to the agent: "navigate to the agent" — it has @playwright/mcp as a tool
```

Server: `http://127.0.0.1:8765/agent`

---

## 11. Playwright MCP (NEW 2026-07-23)

**Status:** Installed in `opencode.json` under `mcp.playwright` (alongside `awos` and `context7`).
**Config:** headless, isolated, system Chrome at `/usr/bin/google-chrome`, console-level info.

**Gives the agent direct eyes on the app:**
- `browser_navigate(url)` — go to any URL
- `browser_snapshot()` — get live DOM as YAML
- `browser_take_screenshot(filename)` — PNG of viewport
- `browser_console_messages(level)` — live JS console
- `browser_network_requests()` — every request
- `browser_click(ref)`, `browser_type(ref, text)`, `browser_evaluate(fn)` — interact
- `browser_find(text)`, `browser_press_key(key)`, `browser_resize(w,h)` — explore

**Live test result (2026-07-23):** navigated to `http://127.0.0.1:8765/agent`, sent "What is 2+2?", got "4" in 2091ms with full cost badge ($0.0001).

**Workflow:** no more text descriptions of "what I see" needed — agent runs `browser_snapshot()` and reports. Saves 10–60 sec per bug report cycle.

---

## 10. What's NOT in agent.html (planned per checkpoint)

- ❌ Multi-chat sidebar (only on dashboard.html, not wired to /agent)
- ❌ Conversation history persistence (only localStorage of goals, not responses)
- ❌ File references in message (CSS has `.files` class but never populated)
- ❌ Page routing (settings / dashboard / sessions pages)
- ❌ File editor (Monaco)
- ❌ Workspace management (multiple repos)

---

## Related
- [[project_structure]] — canonical project facts & metrics
- [[checkpoint_2026-07-22_session_end]] — last session state
- [[pivot_web_app_product]] — current product direction
- [[ui_ux_priority]] — UI backlog (11 items, 6 done)
- [[agent_console]] — task file for this surface
