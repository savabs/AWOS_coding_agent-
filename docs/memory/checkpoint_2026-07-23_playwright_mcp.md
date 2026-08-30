---
title: "Checkpoint: Playwright MCP + API key rotation — July 23, 2026"
tags:
  - doc/checkpoint
  - phase/4
  - status/done
---

# Session Checkpoint — July 23, 2026

## What changed

### 1. Installed Playwright MCP in opencode.json
- New `mcp.playwright` entry: `npx -y @playwright/mcp --headless --executable-path /usr/bin/google-chrome --isolated --allowed-hosts * --console-level info`
- Agent now has eyes on the running app via `@playwright/mcp` tools (navigate, snapshot, screenshot, click, type, console, network)
- User verified by restart + asking to navigate to example.com (got "Example Domain" snapshot back)

### 2. Rotated OpenCode Go API key
- Old key (starting `sk-0FLkD...`) was returning 429 `GoUsageLimitError` for all 9 models
- User updated `.env` line 70 with new key (starting `sk-srsu...`)
- Server restarted on PID 91339 (was 51712)
- Live test now works: Flash returns clean responses

### 3. Live-tested the chat flow
Query: `What is 2+2? Just the number.`
- **Result:** `4` in 2091ms
- Model used: `ocg:deepseek-v4-flash` (Auto → T0 won on first try)
- TTFB: 1688ms · Tokens: 0.7k in / 0.0k out · Cost: $0.0001
- 0 JS errors, HTTP 200, full meta badge rendered
- Screenshot: `agent_chat_works.png`

## Bugs found in this session

| # | Status | Bug | Where |
|---|--------|-----|-------|
| 1 | ✅ RESOLVED | OpenCode Go 401 → was actually 429 quota | was `gui/server.py` env loading |
| 2 | ✅ RESOLVED | 429 quota exhausted | `.env` API key rotation |
| 3 | 🆕 OPEN | Model name shows raw ID `ocg:deepseek-v4-flash` instead of `DeepSeek V4 Flash` | `gui_chat.py:1069` |
| 4 | 🆕 OPEN | Phantom model names during streaming (`DeepSeek V4 Pro` then `MiniMax M3` placeholder before real model) | `gui_chat.py:420` |
| 5 | ⚪ UNTESTED | M3 cost badge missing | can't reproduce — M3 not in dropdown |
| 6 | ⚪ UNTESTED | Server stability (alleged crash after e2e) | needs stress test |
| 7 | 🆕 MINOR | Error message truncated mid-string when 429 fires | `agent.html` bubble has no overflow handling |

## Files touched today
- `opencode.json` — added `mcp.playwright` entry
- `memories/repo/ui_state.md` — refreshed "Known Bugs" section, added §11 Playwright MCP, updated "How to Run"
- `.env` (line 70) — new OPENCODE_GO_API_KEY (user did)
- `scaffold/agent/gui_chat.py` — NOT touched (bugs identified, fixes not yet applied)

## Files created today
- `agent_initial.png` — empty state screenshot
- `agent_chat_in_progress.png` — first test (showed 429 error)
- `agent_chat_works.png` — successful test with new key

## Next session — what to do

1. **Fix B1: model name bug** (5 min, 1 file)
   - Change `gui_chat.py:1069` from `self._last_model = f"ocg:{model_id}"` to also store the human-readable name
   - Verify with Playwright: badge should show `DeepSeek V4 Flash` not `ocg:deepseek-v4-flash`

2. **Fix B2: phantom model names** (10 min, 1 file)
   - Change `gui_chat.py:420` from hardcoded `'DeepSeek V4 Pro'` to empty string (no placeholder) or use the real auto-routing decision
   - Or: defer status events until model is known

3. **Test B3: M3 cost badge** (15 min)
   - Add M3 to `#model-select` dropdown, or temporarily force T0-T2 to fail to reach T3
   - Live-verify with Playwright whether cost appears

4. **Stress test B6: server stability** (20 min)
   - Loop 20 queries via Playwright MCP, check server doesn't die
   - Reuse the stress pattern from `scripts/stress_test_chat.py`

5. **Pick from UI priority list** (from project_structure.md)
   - Items 7-11: multi-chat sidebar, history, file refs, stop polish, page routing

## How to resume

```bash
# 1. Read these (cold start)
cat memories/repo/project_structure.md
cat memories/repo/ui_state.md
cat docs/memory/checkpoint_2026-07-23_playwright_mcp.md   # this file

# 2. Check server is up
ps aux | grep gui/server | grep -v grep
# If not:
nohup python3 gui/server.py --port 8765 > /tmp/awos_server.log 2>&1 &

# 3. Quick chat test
# Just say to the agent: "navigate to the agent, send a question, report"
```

## Related
- [[checkpoint_2026-07-22_session_end]] — previous session
- [[ui_state]] — current app state with bug table
- [[project_structure]] — canonical project facts
