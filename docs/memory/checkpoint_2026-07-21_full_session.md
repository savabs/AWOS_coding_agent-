---
title: "Checkpoint: Full Session — July 21, 2026"
tags:
  - doc/checkpoint
  - phase/4
  - status/done
---

# Full Session Checkpoint — July 21, 2026

> **Massive build session spanning 8+ hours across the entire stack.**
> Agent Console, native app, model routing, OpenCode Go integration, codebase-aware chat, conversation UI, app packaging, and more.

---

## 1. Agent Console — Complete Redesign

**Files:** `gui/static/agent.html`

### What changed
| Before | After |
|--------|-------|
| Single one-shot Q&A | **Multi-turn conversation** — messages accumulate |
| "▶ Run" button | **"Send" button** — changes to "■ Stop" (red) during streaming |
| Big heading "What do you want to build?" | **Empty state**: "Ask a question or describe a task" |
| Chat answer card only | **Message bubbles** — user on right, AWOS on left |
| No metadata | **Live reasoning feed** — model name, timing below each response |
| 600+ lines CSS | **~280 lines CSS** — cleaner, focused |
| Error detection scanned model output | **Proper SSE error handling** — `event: error` caught, `showError()` called correctly |
| Couldn't stop mid-response | **Stop button** — AbortController aborts fetch, keeps partial response |
| "history" section in sidebar | **localStorage history** for recent commands |

### Key features
- `send()` function with AbortController for stop
- `addMessage(role, content, streaming)` — creates user/assistant bubbles
- `updateMessageBubble()` — renders streaming markdown via `marked.parse()`
- `updateMessageMeta()` — shows model + timing below response
- Question pattern detection routes to chat vs orchestrator
- Run `python3 scripts/checkpoint.py` to verify conversation count

---

## 2. Native App (Electron)

**Files:** `awos-app/main.js`, `awos-app/preload.js`, `awos-app/package.json`, `awos-app/inspect.js`, `awos-app/test.js`

### System Tray
- Tray icon (generated programmatically + PNG fallback at `icons/tray-icon.png`)
- Status color changes: gray (idle) → blue (running) → green (done)
- Tooltip updates with status
- Right-click menu: Show AWOS, Status, Quit
- Click tray → show/hide window
- Window close (Cmd+W) → minimizes to tray
- Cmd+Q → truly quits

### Global Command Palette (Cmd+K)
- Cmd+K anywhere on macOS → toggles overlay in AWOS window
- Quick Actions: New task, Ask question
- Recent commands from localStorage
- Keyboard: ↑↓ navigate, Enter execute, Escape close

### Background Mode
- Python server stays alive when window is hidden
- Tray dot shows status
- Click tray → window reappears

### Native Notifications
- On session complete: "4/4 tasks done · $0.0056"
- Click notification → brings window to front
- Uses Electron's Notification API

### IPC Bridge (`preload.js`)
- `awos.dockBounce()` — macOS dock bounce
- `awos.setProgress(progress)` — taskbar progress bar
- `awos.setAgentStatus(status)` — tray status (idle/running/done)
- `awos.notify(title, body)` — native notification
- `awos.onCommandPalette(callback)` — listen for Cmd+K
- `awos.onQuickChat(callback)` — listen for Shift+Cmd+K
- `awos.onNewTask(callback)` — listen for Cmd+N
- `awos.showWindow()` — bring app to front
- `awos.platform` / `awos.isMac`

### App Menu
- Agent menu: Command Palette, New Chat, New Agent Task
- Edit, View, Window, Help menus
- Keyboard shortcuts for all

### Port Conflict Handling
- If port 8765 busy, tries ports 8766, 8767, etc.
- Graceful startup — waits for server before showing window

### Packaging
- `npx electron-builder --dir --mac` builds `dist/mac/AWOS.app`
- `npx electron-builder --mac --publish=never` builds `.dmg`
- `npx electron-builder --linux` builds `.AppImage`
- `npx electron-builder --win` builds `.exe`
- Icons at `icons/icon.png`, `icons/tray-icon.png`
- Extra resources included: `gui/`, `scaffold/` directories
- `app.isPackaged` detection for resource path
- Final output: `dist/AWOS.app` (~608MB)

### Visual Tester (`awos-app/test.js`)
Captures: visible elements, interactive state, content, JS variables
```bash
npx electron test.js                    # idle state
npx electron test.js --cmd-palette      # open palette
npx electron test.js --history          # click history
npx electron test.js --type "query"     # type text
```

---

## 3. OpenCode Go Integration

**Files:** `scaffold/agent/gui_chat.py`, `scaffold/agent/provider_health.py`, `scaffold/agent/escalation_engine.py`, `scaffold/agent/worker.py`, `scaffold/agent/react_worker.py`

### Architecture
OpenCode Go ($10/mo subscription) provides access to 15+ Chinese coding models via a unified API at `https://opencode.ai/zen/go/v1`.

### Dual-Protocol Routing

| Protocol | Endpoint | Auth | Models |
|----------|----------|------|--------|
| **OpenAI-compatible** | `/v1/chat/completions` | `Authorization: Bearer {key}` | Grok, DeepSeek, GLM, Kimi, MiMo |
| **Anthropic-compatible** | `/v1/messages` | `x-api-key` + `anthropic-version: 2023-06-01` | MiniMax, Qwen |

### Current Model Tier (9 models, ordered by speed + cost)

| Tier | Model | Speed | Cost/MTok | Protocol | Status |
|------|-------|-------|-----------|----------|--------|
| T0 | MiniMax M3 | 2.0s | $0.30/$1.20 | anthropic | ✅ PRIMARY |
| T1 | DeepSeek V4 Pro | 2.2s | $0.435/$0.87 | openai | ✅ reasoning_content fix |
| T2 | DeepSeek V4 Flash | 2.2s | $0.14/$0.28 | openai | ✅ cheapest |
| T3 | Grok 4.5 | 2.9s | $2.00/$6.00 | openai | ✅ expensive |
| T4 | MiMo V2.5 Pro | 2.8s | $0.435/$0.87 | openai | ✅ NEW |
| T5 | GLM-5.2 | 3.5s | $1.40/$4.40 | openai | ✅ NEW |
| T6 | MiniMax M2.7 | 3.0s | $0.30/$1.20 | anthropic | ✅ NEW |
| T7 | Qwen3.7 Plus | 8.5s | $0.40/$1.60 | anthropic | ⚠️ slow |
| T8 | Kimi K2.7 Code | DOWN | $0.95/$4.00 | openai | ❌ provider down |

### `_stream_opencode_go()` — Intelligent Model Escalation
- Tries models in order: first one that returns content wins
- 6s per-model timeout
- 20s total timeout across all models
- Empty response detection — if model returns 0 chars, escalates
- Error handling — each model failure caught, next model tried
- Last error message propagated if all fail

### Content Sources Detected

| Content field | Where it comes from | Models |
|---------------|-------------------|--------|
| `delta.content` | Standard OpenAI output | Grok, MiniMax, MiMo, Qwen |
| `delta.model_extra.reasoning_content` | DeepSeek thinking mode | DeepSeek Pro, Flash, GLM-5.2, GLM-5.1 |

The reasoning_content fix: DeepSeek and GLM models put their output in `reasoning_content` (inside `model_extra`), NOT in `content`. The streaming code checks both fields.

### Model Tiers in `gui_chat.py`

```python
_OC_MODEL_TIERS = [
    ("minimax-m3",        "MiniMax M3",        0.30, 1.20, "anthropic"),
    ("deepseek-v4-pro",   "DeepSeek V4 Pro",   0.435, 0.87, "openai"),
    ("deepseek-v4-flash", "DeepSeek V4 Flash", 0.14, 0.28, "openai"),
    ("grok-4.5",          "Grok 4.5",          2.00, 6.00, "openai"),
    ("mimo-v2.5-pro",     "MiMo V2.5 Pro",     0.435, 0.87, "openai"),
    ("glm-5.2",           "GLM-5.2",           1.40, 4.40, "openai"),
    ("minimax-m2.7",      "MiniMax M2.7",      0.30, 1.20, "anthropic"),
    ("qwen3.7-plus",      "Qwen3.7 Plus",      0.40, 1.60, "anthropic"),
    ("kimi-k2.7-code",    "Kimi K2.7 Code",    0.95, 4.00, "openai"),
]
```

### Guardrails
- `max_tokens = 2048` — let model complete naturally (raised from 300)
- 6s timeout per model, 20s total timeout
- Empty response → escalate to next model
- Exception caught per model → next model tried
- Key health check (`OPENCODE_GO_API_KEY` presence) checked per call

---

## 4. Codebase-Aware Chat

**Files:** `scaffold/agent/gui_chat.py` (`_read_project_context`, `_qa_context_blob`)

### Before
- Hardcoded docs (VISION.md, README.md, etc.) + keyword match on filenames
- 12,000 char cap
- No relevance — dumped everything

### After
- **SymbolIndex integration** — loads cached symbol index from `.awos/symbol_index.json`
- Keyword + symbol scoring — matches query words against filename + class/function names
- Relevant files injected with scores — "📄 scaffold/agent/budget_ledger.py (relevance: 3)"
- Context capped at 6,000 chars but now ALL relevant
- Fallback: keyword match on filenames if SymbolIndex unavailable

### Example queries and what they load

| Query | Files loaded |
|-------|-------------|
| "how does auth work" | `worktree.py`, `workflow_orchestrator.py`, `worker.py` |
| "what is the orchestrator" | `workflow_orchestrator.py`, `orchestrator.py`, `chat_orchestrator.py` |
| "how does the budget ledger work" | `budget_ledger.py` |

---

## 5. Provider Health Checks

**Files:** `scaffold/agent/provider_health.py`

### Checks per provider
| Provider | Method | Timeout |
|----------|--------|--------|
| **OpenCode Go** | `client.models.list()` | 5s |
| **DeepSeek** | 1-token chat completion | 2s |
| **Anthropic** | 1-token message | 2s |
| **OpenAI** | 1-token chat completion | 2s |
| **Gemini** | 1-token generate | 2s |

### Caching
- 5-minute TTL per provider
- Background health check thread runs every 5 minutes (non-blocking)
- `GET /api/health` returns cached status

### Server stability
- Health check thread hardened with per-call try/except
- Skips providers that crash (Gemini deprecated)
- Background thread in daemon mode — won't block shutdown

---

## 6. Model Validation Script

**File:** `scripts/validate_models.py`

### Tests per model
1. Unframed API call with `max_tokens=30`, temperature=0
2. Checks: status code, content length, content source field (`content` vs `reasoning_content`)
3. Streaming test with TTFB measurement
4. Protocol detection (OpenAI vs Anthropic)

### Usage
```bash
python3 scripts/validate_models.py           # human-readable
python3 scripts/validate_models.py --json    # machine-readable
```

### Results (last run)
```
8 working · 2 empty · 5 failed of 15 total
Fastest: MiniMax M3 (2028ms)
Working: Grok 4.5, DeepSeek V4 Pro, DeepSeek V4 Flash, GLM-5.2, GLM-5.1,
         MiMo V2.5 Pro, MiniMax M3, MiniMax M2.7
```

---

## 7. Worker OpenCode Go Integration

**Files:** `scaffold/agent/worker.py`, `scaffold/agent/react_worker.py`

### Changes
- `self.opencode_client` — OpenAI client pointing to OpenCode Go endpoint
- Provider dispatch: `model_spec.provider == "opencode"` → calls `self.opencode_client.chat.completions.create()`
- Dead provider pre-population: missing keys → added to `_dead_providers`
- `_try_cheap_fallback` updated to include opencode
- EscalationEngine OPENCODE level enabled (min_complexity=0, min_failures=0)

### Verified
```python
Routing for 'fix the auth tests':
  Model: OpenCode Go (T6)  ✅
  Provider: opencode       ✅
  Cost: ~$0.001           ✅
  
Worker:
  opencode_client: True    ✅
  dead_providers: set()   ✅
```

---

## 8. Diagnostics & Sensors

**Files:** `scaffold/agent/diagnostics.py`, `gui/server.py`

### TraceSpan
- `name`: operation name
- `phase`: http / routing / llm_call / sse / ui
- `started_at`, `finished_at`, `duration_ms`
- `success`, `error`, `metadata`

### Timing Report
- By phase: http, llm_call
- By name: chat_stream, llm_qa_call
- Count, total_ms, avg_ms, errors, slowest

### Diagnostic API
```
GET /api/diagnostics  → {"phases": {...}, "details": {...}, "trace_count": N}
GET /api/budget       → {"spent": 2.09, "budget": 20.0, ...}
GET /api/health       → provider statuses
GET /api/models/status → current model tiers
```

### Current budget
```
Spent:   $2.09 / $20.00 (10.4%)
Requests: 2,153
Tokens:  14.6M
Avg cost: $0.00097/request
```

---

## 9. Current App Assessment

### What works
| Feature | Status |
|---------|--------|
| Chat conversation (multi-turn) | ✅ Messages accumulate, follow-ups work |
| Model routing (9 tiers) | ✅ Dual-protocol, intelligent escalation |
| Codebase context | ✅ SymbolIndex finds relevant files |
| Native app | ✅ Electron, tray, Cmd+K, notifications |
| Stop button | ✅ AbortController, keeps partial response |
| Budget tracking | ✅ Wired to ledger |
| Model validation | ✅ 15 models tested, 8 working |
| Provider health | ✅ 5 providers, 5min cache |
| Diagnostics | ✅ Timing sensors + API |
| App packaging | ✅ .dmg, .AppImage, .exe builds |
| OpenCode Go in worker | ✅ Provider dispatch wired |

### Known issues
| Issue | Severity |
|-------|----------|
| Server crashes after 2-3 queries | ❌ Critical |
| Chat response 7-15s (slow) | ⚠️ High |
| No file editor (Monaco) | ⚠️ High |
| No AI + editor integration | ⚠️ High |
| No workspace management | 🟡 Medium |
| Orchestrator E2E untested | 🟡 Medium |

### Speed profile (direct Python test)
```
_build_qa_prompt:     3ms    ✅ fine
_qa_context_blob:      1ms    ✅ fine
_llm_messages:         0ms    ✅ fine
_stream_opencode_go:  3,190ms (MiniMax M3) — OK
                      34,000ms (fallback chain) ❌ slow
```

---

## 10. Files Created/Modified This Session

### New Files
```
awos-app/icons/icon.png               — App icon (512x512)
awos-app/icons/tray-icon.png          — Tray icon (16x16)
awos-app/inspect.js                   — DOM state inspector (Electron)
awos-app/test.js                      — Visual tester with interactions
awos-app/screenshot.js                — Screenshot capture script
scripts/validate_models.py            — Model validation script
scaffold/agent/diagnostics.py         — Timing sensors
scaffold/agent/provider_health.py     — Provider health checks + OpenCode Go catalog
```

### Modified Files
```
awos-app/main.js                       — System tray, Cmd+K, port fallback, packaged paths
awos-app/preload.js                    — IPC bridge: tray, notifications, palette
awos-app/package.json                  — Build config, icons, extra resources
gui/static/agent.html                  — Complete rewrite: conversation UI, stop button, cleaner
gui/server.py                          — Diagnostics, health, budget, models/status endpoints
scaffold/agent/gui_chat.py             — OpenCode Go tiers, dual-protocol, SymbolIndex context, budget recording
scaffold/agent/worker.py               — OpenCode Go client + dispatch + dead providers
scaffold/agent/react_worker.py         — OpenCode Go in dead provider pre-population
scaffold/agent/escalation_engine.py    — OPENCODE level enabled
```

---

## 11. Key Metrics

| Metric | Value |
|--------|-------|
| Model tiers | 9 (8 working, 1 down) |
| Working models | 8/15 validated |
| Chat latency | 2-8s (MiniMax M3) |
| Budget spent | $2.09 of $20/month |
| Total requests | 2,153 |
| Avg cost/req | $0.00097 |
| Visual tester checks | 10+ |
| Diagnostics traces | HTTP + LLM call |
| Server ports | Fallback from 8765 |
| App size (macOS) | ~608MB (Electron bundle) |

---

## 12. Next Priority List

```
1. Server stability    — app crashes after 2-3 queries (blocks everything)
2. Speed               — target 3-5s per query (users won't wait 10s+)
3. File editor         — Monaco in app, file IPC, save to disk
4. AI + editor         — agent reads open file, shows inline diffs
5. Workspace mgmt      — multiple repos, per-repo .awos/
6. Orchestrator E2E    — awos run executes coding tasks
7. Advanced test suite — after above are stable, test everything
```

---

## 13. How to Resume

```bash
# 1. Check this checkpoint
cat docs/memory/checkpoint_2026-07-21_full_session.md

# 2. Read project facts
cat memories/repo/project_structure.md

# 3. Test current state
python3 scripts/validate_models.py
cd awos-app && npx electron test.js --wait 2

# 4. Start server
cd /path/to/project && python3 gui/server.py --port 8765

# 5. Open the app
# Web: http://localhost:8765/agent
# Native: cd awos-app && npm start
```

---

## Related

- [[agent_console]] — task file
- [[awos_app_spec]] — spec for Electron app
- [[checkpoint_2026-07-12_agent_console]] — previous UI redesign
- [[checkpoint_2026-07-12_awos_app]] — previous Electron work
- [[checkpoint_2026-07-12_smart_routing]] — routing fixes
- [[checkpoint_2026-07-12_in_app_agent]] — in-app agent execution
- [[checkpoint_2026-07-12_native_agent]] — native features
