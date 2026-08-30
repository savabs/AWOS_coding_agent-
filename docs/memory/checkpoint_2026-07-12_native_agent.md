---
title: "Checkpoint: Native Agent — System Tray, Cmd+K, Background Mode"
tags:
  - doc/checkpoint
  - phase/4
  - topic/native-app
  - topic/agent-console
  - status/done
---

# Native Agent Features

> **Date:** July 12, 2026
> **Theme:** AWOS is now a real agent — lives in your dock, works in background, summons globally.

---

## What Was Added

### 1. System Tray
- **Tray icon** changes color based on agent status: gray (idle) → blue (running) → green (done)
- **Tooltip:** "AWOS — Working..." / "AWOS — Idle"
- **Click tray:** show/hide window
- **Right-click tray:** Show AWOS, Status, Quit
- **Window close (Cmd+W):** minimizes to tray instead of quitting
- **Cmd+Q:** truly quits

### 2. Global Command Palette (Cmd+K)
- **Cmd+K anywhere in the OS** → toggles the palette overlay in the AWOS window
- **Blurred backdrop** with centered card (Spotlight-inspired)
- **Quick Actions:** "New Agent Task" (Cmd+K), "Ask a Question" (Cmd+Shift+K), "Clear Session"
- **Recent Commands:** last 8 stored in localStorage, filterable as you type
- **Keyboard:** ↑↓ to navigate, Enter to execute, Escape to close
- **Browser fallback:** Cmd+K works in browser too

### 3. Background Mode
- When window is hidden (minimized to tray), the Python server keeps running
- Agent continues working on tasks
- Tray icon shows status — user knows it's working even without the window open
- Click tray → window reappears instantly

### 4. Native Notifications
- When a session completes: pops up OS notification
- "4/4 tasks done · $0.0056"
- Click notification → brings window to front
- Tray icon turns green → fades to gray after 5 seconds

---

## Files Changed

| File | Change |
|------|--------|
| `awos-app/main.js` | Complete rewrite: system tray, global shortcuts, minimize-to-tray, IPC for notifications, tray status |
| `awos-app/preload.js` | New IPC channels: setAgentStatus, notify, onCommandPalette, onQuickChat, onNewTask, showWindow |
| `gui/static/agent.html` | Command palette overlay (CSS + HTML + JS), recent commands in localStorage, tray status + notification on session_done |

---

## Live Proof

| Check | Status |
|-------|--------|
| Palette overlay in DOM | ✅ |
| togglePalette() function | ✅ |
| setAgentStatus IPC | ✅ |
| Notify IPC | ✅ |
| onCommandPalette listener | ✅ |
| Browser Cmd+K fallback | ✅ |
| Recent commands (localStorage) | ✅ |
| Notification on session_done | ✅ |
| main.js syntax (node --check) | ✅ |
| Electron launch (server ready) | ✅ |

---

## The User Experience Now

```
1. Open AWOS → frameless dark window, tray icon appears
2. Cmd+K → Spotlight-like palette: "What should the agent do?"
3. Type "fix tests in backend-api" → Enter
4. Agent starts working → tray turns blue → Reasoning panel fills
5. Close window (Cmd+W) → minimizes to tray → keeps working
6. 30 seconds later → notification pops: "4/4 tasks done · $0.0042"
7. Tray turns green → click to see what it did
8. Cmd+K again → recent commands appear: "fix tests in backend-api" 🕐
9. Arrow down + Enter → runs again
```

Not a web page. Not a dashboard. A **native agent.**
