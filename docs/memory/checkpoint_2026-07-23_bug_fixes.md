---
title: "Checkpoint: All 4 chat bugs fixed + stress tested — July 23, 2026 (afternoon)"
tags:
  - doc/checkpoint
  - phase/4
  - status/done
---

# Session Checkpoint — July 23, 2026 (afternoon)

## What changed (4 bug fixes in 1 session)

### ✅ B1: Model name in badge (5 min)
- **File:** `scaffold/agent/gui_chat.py:1069`
- **Before:** `self._last_model = f"ocg:{model_id}"` (raw ID)
- **After:** `self._last_model = name` (human-readable from `_OC_MODEL_TIERS` tuple)
- **Live proof:** badge now shows `DeepSeek V4 Flash` instead of `ocg:deepseek-v4-flash`

### ✅ B2: Phantom model names during streaming (10 min)
- **File:** `gui/static/agent.html:420, 436`
- **Before:** hardcoded `'DeepSeek V4 Pro'` placeholder, then `'MiniMax M3'` fallback
- **After:** both start with empty `''` — meta stays empty until real usage event arrives
- **Live proof:** console log shows clean progression: empty → status msg → real model

### ✅ B3: M3 cost badge reachable from UI (15 min)
- **File:** `gui/static/agent.html:160` (added to "Balanced" optgroup)
- **Added:** `<option value="minimax-m3">M3 — $0.30/$1.20 per MTok</option>`
- **Live proof:** M3 query `7+8?` returned `15` with full meta: `MiniMax M3 · 2685ms · TTFB 1566ms · 0.8k in 0.1k out · $0.0003`
- **Math verified:** 0.787 × $0.30 + 0.083 × $1.20 = $0.0003357 ≈ $0.0003 ✓
- **Discovery:** the M3 cost badge was always working — it just wasn't reachable. The "checkpoint bug" was actually a UX bug (M3 not in dropdown).

### ✅ B4: Server stability (20 min)
- **No code change** — pure stress test via Playwright MCP
- **Result:** 20/20 queries successful
  - First 3 (cold): 2177ms / 1808ms / 1732ms
  - Last 3 (cached): **18ms / 19ms / 37ms** ← response cache paying off
  - Total wall time: 10 seconds for 20 queries
- **Server:** PID 15691 still alive after test, 0.03s CPU, HTTP 200 confirmed
- **Conclusion:** Server stability is solid. The original "crash after 2-3 queries" claim from 2026-07-21 is no longer reproducible.

## Files touched today

| File | Change |
|------|--------|
| `opencode.json` | Added `mcp.playwright` entry (morning session) |
| `.env` line 70 | New OPENCODE_GO_API_KEY (user did) |
| `scaffold/agent/gui_chat.py:1069` | B1 fix — use `name` instead of `f"ocg:{model_id}"` |
| `gui/static/agent.html:420, 436` | B2 fix — empty placeholders |
| `gui/static/agent.html:160` | B3 fix — added M3 to dropdown |
| `memories/repo/ui_state.md` | Refreshed §7 bugs, updated dropdown description |
| `memories/repo/project_structure.md` | Marked 4 bugs DONE in Active Work table |
| `docs/memory/checkpoint_2026-07-23_playwright_mcp.md` | Previous checkpoint (morning) |

## Files created today
- `agent_initial.png` — empty state
- `agent_chat_in_progress.png` — first test (showed 429)
- `agent_chat_works.png` — successful test with new key
- `agent_m3_works.png` — M3 cost badge works

## Active bugs remaining

| # | Bug | Where | Severity |
|---|-----|-------|----------|
| B5 | Error message truncated mid-string when long errors fire | `agent.html` has no `text-overflow`/expand on `.msg-bubble` | 🟢 minor |

## Net progress on chat

- Before this session: chat was completely broken (429 on all models)
- After this session: chat works, badge shows real model names, M3 is selectable, server handles 20+ queries
- All 4 bugs from the morning's live test are fixed
- 1 minor bug (B5) remains

## What to do next session

1. **B5 fix** (15 min) — add overflow handling + "Show full error" toggle on `.msg-bubble` for long error messages
2. **Pick from UI priority list** (multi-chat sidebar, conversation history, file references, page routing)
3. **Pick from kernel work** (CI Rescue wedge next steps, more model validation)

## How to resume

```bash
# 1. Cold start
cat memories/repo/project_structure.md
cat memories/repo/ui_state.md
cat docs/memory/checkpoint_2026-07-23_bug_fixes.md   # this file

# 2. Verify server is up
ps aux | grep "gui/server" | grep -v grep
# If not: nohup python3 gui/server.py --port 8765 > /tmp/awos_server.log 2>&1 &

# 3. Quick test
# In opencode: "navigate to the agent, send a question, report the meta badge"
```

## Related
- [[checkpoint_2026-07-23_playwright_mcp]] — morning session (MCP install + key rotation)
- [[checkpoint_2026-07-22_session_end]] — previous day
- [[ui_state]] — current app state with bug table
- [[project_structure]] — canonical project facts
