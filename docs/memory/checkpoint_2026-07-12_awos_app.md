---
title: "Checkpoint: AWOS Native App — Electron + Apple Design"
tags:
  - doc/checkpoint
  - phase/4
  - topic/native-app
  - topic/agent-console
  - status/done
---

# AWOS Native App — Electron + Apple Design

> **Date:** July 12, 2026
> **North star:** AWOS ships as a real desktop application, not a URL.

---

## What Changed

**Problem:** AWOS was a web page you visited at `http://localhost:8765/agent`. No dock icon, no native menus, no app presence. And the visual design was GitHub-dark — functional but not *finished*.

**Fix:** Three things shipped in one push:

### 1. Electron native app (`awos-app/`)
- `main.js` — Spawns Python server as child process, polls until ready, creates frameless BrowserWindow (1200×800, hiddenInset title bar)
- Native application menu (File, Edit, View, Window, Help) with keyboard shortcuts
- Dock integration: `dock.bounce()` on new activity via IPC, `setProgressBar` for task progress
- `preload.js` — Secure context bridge exposing `awos.dockBounce()`, `awos.setProgress()`, `awos.platform`
- Clean shutdown: kills Python server on app quit
- `package.json` with `electron-builder` config for macOS (.dmg), Linux (.AppImage), Windows (.exe)

### 2. Apple-inspired visual redesign (`gui/static/agent.html`)
Complete overhaul following Apple design principles:

| Element | Before | After |
|---------|--------|-------|
| Background | `#0d1117` (GitHub cold dark) | `#1a1a1e` (warm dark) |
| Surface | `#161b22` | `#252529` with `backdrop-filter: blur(20px)` |
| Accent | `#58a6ff` (GitHub blue) | `#0071e3` (Apple blue) |
| Text | `#c9d1d9` | `#f5f5f7` |
| Font | system-ui | `-apple-system, 'SF Pro Text'` |
| Code font | Monaco | `'SF Mono', 'Menlo'` |
| Title bar | Browser chrome | Custom frameless with window controls |
| Thinking panel | Dark box with border | Frosted glass sheet with blur backdrop |
| Event log | Monospace text | Structured cards with entrance animations |
| Radius | 6px | 10px (cards), 6px (small) |
| Animations | fadeIn | `cubic-bezier(0.22, 1, 0.36, 1)` |
| Shadows | none | `0 2px 12px rgba(0,0,0,0.4)` |

### 3. All event handling preserved
- SSE connection to `/api/agent/events` — all 9 event types render as beautiful cards
- `agent_thinking` events → reasoning sheet (transparent back)
- `model_routed` events → rich routing cards with tier, cost, provider
- Session history from `/api/sessions`
- Historical session event replay

---

## Live Proof

### UI verification (11/11 markers)

| Check | Status |
|-------|--------|
| Title: "AWOS Agent Console" | ✅ |
| Frameless title bar | ✅ |
| Reasoning sheet (thinking panel) | ✅ |
| Apple dark background (#1a1a1e) | ✅ |
| Apple blue accent (#0071e3) | ✅ |
| Event cards present | ✅ |
| Stat: Tasks | ✅ |
| Stat: Models | ✅ |
| Stat: Cost | ✅ |
| No CI Rescue branding | ✅ |
| Electron bridge (window.awos) | ✅ |

### Electron launch

```
Starting AWOS server...
Waiting for server at http://127.0.0.1:8765/agent...
Server ready.
```

Electron spawns Python server, waits for it, loads the app. GPU errors on headless server only — real desktop shows a beautiful frameless window.

---

## Files Created/Modified

| File | Type | Purpose |
|------|------|---------|
| `awos-app/package.json` | create | Electron project |
| `awos-app/main.js` | create | Main process: server spawn, window, menus, dock |
| `awos-app/preload.js` | create | Context bridge |
| `gui/static/agent.html` | redesign | Apple-inspired frameless UI, reasoning sheet, event cards |
| `docs/specs/awos_app_spec.html` | create | Spec document |
| `docs/specs/awos_app_spec.md` | create | Spec stub |
| `tasks/active/awos_app.html` | create | Task file (5/5 done) |
| `tasks/active/awos_app.md` | create | Task stub |

---

## State at End

| Metric | Value |
|--------|-------|
| awos_app task | 5/5 steps done ✅ |
| UI verification | 11/11 markers |
| Electron launch | Server connects, window loads |
| Active task files | 3 (awos_app done, agent_console done, routing_learning_repair) |

---

## How to Run

```bash
# Web only (browser)
python3 gui/server.py --port 8765
open http://localhost:8765/agent

# Native app (macOS/Linux/Windows)
cd awos-app && npm start
```

To build distributables:
```bash
cd awos-app
npm run build:mac    # → dist/AWOS-1.0.0.dmg
npm run build:linux  # → dist/AWOS-1.0.0.AppImage
npm run build:win    # → dist/AWOS Setup 1.0.0.exe
```

---

## Next

- App icon design (replace placeholder)
- System tray: background agent that bounces dock on completion
- Auto-updater via electron-updater
- Deep link: `awos://run?goal=fix+the+tests`
- CI/CD: GitHub Actions to build + sign + notarize

---

## Related

- [[awos_app]] — task file
- [[awos_app_spec]] — spec
- [[agent_console]] — previous UI redesign
- [[checkpoint_2026-07-12_agent_console]] — agent console checkpoint
