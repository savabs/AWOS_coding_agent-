# Agent GUI Architecture Comparison

Visual comparison of different agent GUI architectures analyzed in our research.

---

## 1. Hermes WebUI Architecture (14K⭐) - The Simple Approach

```
┌─────────────────────────────────────────────────────────────┐
│                         Browser                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐           │
│  │  Sessions  │  │    Chat    │  │ Workspace  │           │
│  │  Sidebar   │  │   Panel    │  │   Files    │           │
│  └────────────┘  └────────────┘  └────────────┘           │
│         │              │                │                   │
│         └──────────────┴────────────────┘                   │
│                        │                                     │
│                   Vanilla JS                                │
│                        │                                     │
│                     SSE/HTTP                                │
└────────────────────────┼───────────────────────────────────┘
                         │
┌────────────────────────┼───────────────────────────────────┐
│                Python HTTP Server                           │
│    ┌──────────────────────────────────────────┐            │
│    │  http.server.ThreadingHTTPServer         │            │
│    └──────────────────────────────────────────┘            │
│         │                                                    │
│    ┌────┴─────────────────────────────────┐                │
│    │           api/ modules               │                │
│    │  ┌──────┐ ┌──────┐ ┌────────┐       │                │
│    │  │routes│ │stream│ │sessions│       │                │
│    │  └──────┘ └──────┘ └────────┘       │                │
│    └──────────────────────────────────────┘                │
│                      │                                      │
└──────────────────────┼──────────────────────────────────────┘
                       │
┌──────────────────────┼──────────────────────────────────────┐
│              Hermes Agent Core                              │
│    ~/.hermes/state.db    ~/.hermes/sessions/               │
└─────────────────────────────────────────────────────────────┘

Key Features:
✅ No build step (no webpack/vite)
✅ No framework (no React/Vue)
✅ Python stdlib only
✅ SSE for real-time streaming
✅ Mobile responsive
✅ ~14K stars, proven at scale
```

---

## 2. Kanna Architecture (556⭐) - The Event-Sourcing Approach

```
┌─────────────────────────────────────────────────────────────┐
│                      Browser (React)                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Zustand State Store                    │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐         │   │
│  │  │ Projects │  │   Chat   │  │  Tools   │         │   │
│  │  └──────────┘  └──────────┘  └──────────┘         │   │
│  └─────────────────────────────────────────────────────┘   │
│                        │                                     │
│                   WebSocket                                 │
└────────────────────────┼───────────────────────────────────┘
                         │
┌────────────────────────┼───────────────────────────────────┐
│               Bun Server (TypeScript)                       │
│                        │                                     │
│    ┌───────────────────┴──────────────────────┐            │
│    │           WSRouter                       │            │
│    │  (subscription & command routing)        │            │
│    └───────────────────┬──────────────────────┘            │
│                        │                                     │
│    ┌──────────┬────────┴────────┬──────────┐              │
│    │   Event  │  Read Models    │  Agent   │              │
│    │   Store  │  (derived views)│  Coord   │              │
│    └──────────┴─────────────────┴──────────┘              │
│         │                                                    │
│    ~/.kanna/data/events.jsonl                              │
│                                                              │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────┼───────────────────────────────────────┐
│         Multi-Provider Agent (stdio)                        │
│    Claude Code / Codex / OpenAI / Anthropic                │
└─────────────────────────────────────────────────────────────┘

Key Features:
✅ Event sourcing (full audit trail)
✅ CQRS (separate read/write paths)
✅ WebSocket bidirectional
✅ Multi-provider support
✅ Reactive state broadcasting
❌ More complex setup
❌ Requires build step (Vite)
```

---

## 3. AWOS Current Architecture - Our Approach

```
┌─────────────────────────────────────────────────────────────┐
│                      Browser                                │
│  ┌─────────────────┐    ┌─────────────────┐               │
│  │  Chat Tab       │    │  Runs Tab       │               │
│  │  ┌───────────┐  │    │  ┌───────────┐  │               │
│  │  │Mode Select│  │    │  │Run History│  │               │
│  │  ├───────────┤  │    │  ├───────────┤  │               │
│  │  │  Q/A      │  │    │  │Diffs      │  │               │
│  │  │  Plan     │  │    │  │Manifests  │  │               │
│  │  │  Agent    │  │    │  │           │  │               │
│  │  └───────────┘  │    │  └───────────┘  │               │
│  └─────────────────┘    └─────────────────┘               │
│         │                        │                          │
│    Vanilla JS               Vanilla JS                     │
│         │                        │                          │
│         └────────────┬───────────┘                          │
│                      │                                       │
│                  HTTP/SSE                                   │
└──────────────────────┼─────────────────────────────────────┘
                       │
┌──────────────────────┼─────────────────────────────────────┐
│            Python HTTP Server (gui/server.py)              │
│    ┌──────────────────────────────────────────┐            │
│    │  http.server.ThreadingHTTPServer         │            │
│    └──────────────────────────────────────────┘            │
│         │                                                    │
│    ┌────┴─────────────────────────────────┐                │
│    │    API Endpoints                     │                │
│    │  /api/chat       (chat interface)    │                │
│    │  /api/modes      (list modes)        │                │
│    │  /api/runs       (list runs)         │                │
│    │  /api/runs/:id   (run details)       │                │
│    └──────────────────────────────────────┘                │
│                      │                                       │
└──────────────────────┼───────────────────────────────────────┘
                       │
┌──────────────────────┼───────────────────────────────────────┐
│              AWOS Backend (scaffold/agent/)                 │
│                      │                                       │
│    ┌─────────────────┴──────────────────────┐              │
│    │  GuiChatService                        │              │
│    │  ┌──────────┐  ┌──────────┐           │              │
│    │  │ Q/A Mode │  │Plan Mode │           │              │
│    │  └──────────┘  └──────────┘           │              │
│    │                                         │              │
│    │  ┌──────────────────────────┐          │              │
│    │  │  Agent Mode              │          │              │
│    │  │  (Orchestrator)          │          │              │
│    │  │  ┌────────┐ ┌────────┐  │          │              │
│    │  │  │Planner │→│Worker  │  │          │              │
│    │  │  └────────┘ └────────┘  │          │              │
│    │  │  ┌────────┐              │          │              │
│    │  │  │Verifier│              │          │              │
│    │  │  └────────┘              │          │              │
│    │  └──────────────────────────┘          │              │
│    └─────────────────────────────────────────┘             │
│                      │                                       │
│    ┌─────────────────┴──────────────────────┐              │
│    │  GuiEventBus (dual output)            │              │
│    │  ┌──────────────┐ ┌─────────────────┐ │              │
│    │  │ events.jsonl │ │ telemetry.jsonl │ │              │
│    │  │ (for GUI)    │ │ (for agent)     │ │              │
│    │  └──────────────┘ └─────────────────┘ │              │
│    └─────────────────────────────────────────┘             │
│                                                              │
│    .awos/gui/<session_id>/                                 │
│    .awos/gui_chat/<chat_id>.json                           │
└──────────────────────────────────────────────────────────────┘

Key Features:
✅ Simple (Python stdlib + vanilla JS)
✅ No build step
✅ Event-driven (SSE)
✅ Dual output (terminal + GUI)
✅ Mode separation (Q/A, Plan, Agent)
✅ Clean separation of concerns
⚠️ Custom events (not AG-UI yet)
⚠️ Limited mobile responsive
⚠️ No workspace browser yet
```

---

## 4. Architecture Decision Matrix

| Aspect | Hermes | Kanna | AWOS | Recommendation |
|--------|--------|-------|------|----------------|
| **Backend** | Python stdlib | Bun | Python stdlib | ✅ Keep Python |
| **Frontend** | Vanilla JS | React+Zustand | Vanilla JS | ✅ Keep Vanilla |
| **Build** | None | Vite | None | ✅ Keep None |
| **Real-time** | SSE | WebSocket | SSE | ⚠️ Consider WS later |
| **State** | Agent DB | Event sourcing | JSON files | ✅ Current OK |
| **Events** | Custom | Custom | Custom | 🚀 Adopt AG-UI |
| **Mobile** | ✅ Great | ⚠️ Desktop | ⚠️ Basic | 🚀 Improve |
| **Auth** | ✅ Optional | ❌ None | ❌ None | ⚠️ Add optional |
| **Workspace** | ✅ Files | ❌ None | ❌ None | 🚀 Add |

---

## 5. AG-UI Protocol Flow (Recommended Future State)

```
┌─────────────────────────────────────────────────────────────┐
│              AG-UI Compatible Frontend                      │
│                 (Any client library)                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ CopilotKit | Custom React | Vanilla JS | Mobile      │  │
│  └──────────────────────────────────────────────────────┘  │
│                        │                                     │
│                AG-UI Protocol                               │
│         (RUN_STARTED, TEXT_CHUNK, etc.)                    │
└────────────────────────┼───────────────────────────────────┘
                         │
                    SSE/WebSocket
                         │
┌────────────────────────┼───────────────────────────────────┐
│              AG-UI Adapter Layer                            │
│    ┌──────────────────────────────────────────┐            │
│    │  Maps framework events → AG-UI events    │            │
│    └──────────────────────────────────────────┘            │
│                        │                                     │
└────────────────────────┼───────────────────────────────────┘
                         │
┌────────────────────────┼───────────────────────────────────┐
│         Any Agent Backend                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                 │
│  │LangGraph │  │  AWOS    │  │ CrewAI   │                 │
│  └──────────┘  └──────────┘  └──────────┘                 │
└─────────────────────────────────────────────────────────────┘

Benefits:
✅ Frontend can swap backends without changes
✅ Backend can swap frontends without changes
✅ Industry standard (Google, AWS, Microsoft)
✅ Future-proof architecture
```

---

## 6. Recommended Evolution Path for AWOS

### Current State (v0)
```
Python stdlib HTTP → Custom events → Vanilla JS GUI
```

### Phase 1: Quick Wins (v0.1)
```
Python stdlib HTTP → Custom events → Vanilla JS GUI
                                      + PWA manifest
                                      + Slash commands
                                      + Theme switching
```

### Phase 2: AG-UI Alignment (v0.2)
```
Python stdlib HTTP → AG-UI events → Vanilla JS GUI
                     + Adapter        (AG-UI compatible)
```

### Phase 3: Enhanced Features (v0.3)
```
Python stdlib HTTP → AG-UI events → Vanilla JS GUI
+ Workspace API      + Adapter       + Workspace browser
+ Session search                     + Prompt queue
+ Optional auth                      + Better mobile
```

### Phase 4: Advanced (v1.0)
```
Python + WebSocket → AG-UI events → Enhanced GUI
+ WS server          + Adapter       + Monaco editor
+ Real-time bi-dir                   + Voice input
                                     + PWA offline
```

---

## 7. Complexity vs Features Trade-off

```
High │                         
     │                    ┌─── Kanna
     │                    │    (Event sourcing,
C  │                    │     CQRS, WS)
o  │              
m  │                    
p  │         ┌─── AWOS (Phase 4)
l  │         │    (WS, Monaco, Voice)
e  │         │
x  │    ┌────┴─── AWOS (Phase 3)
i  │    │         (Workspace, Auth)
t  │    │
y  │ ┌──┴──── AWOS (Phase 2)
   │ │        (AG-UI aligned)
   │ │
Low│ ┴──── Hermes / AWOS (Current)
   │       (Simple, proven)
   └───────────────────────────────────
      Low    Medium    High    Very High
              Feature Richness

Sweet Spot: Hermes / AWOS Current + Phase 1-2
- Simple architecture
- Standard protocol (AG-UI)
- Essential features
- Easy to maintain
```

---

## 8. Key Architectural Principles (Learned from Research)

### ✅ DO

1. **Keep it simple**: Python stdlib beats frameworks
2. **No build step**: Vanilla JS beats React for tooling GUIs
3. **Event-driven**: SSE/WS for real-time is essential
4. **Standard protocols**: AG-UI for future compatibility
5. **Progressive enhancement**: Start simple, add features incrementally
6. **Mobile-first**: Responsive design from day one
7. **Dual output**: Terminal for devs, GUI for users

### ❌ DON'T

1. **Over-engineer**: Event sourcing is overkill for v1
2. **Framework lock-in**: React/Vue add complexity without benefit
3. **Build complexity**: Webpack/Vite are unnecessary overhead
4. **Desktop-only**: Mobile is a first-class use case
5. **Custom protocols**: AG-UI exists, don't reinvent
6. **Electron**: PWA gives desktop experience without bloat

---

## Conclusion

**AWOS is architecturally sound.** We're following the same patterns as Hermes (14K stars), just need to:

1. Add quick wins (PWA, slash, theme)
2. Align with AG-UI protocol
3. Enhance features incrementally

**No major rewrite needed** - we're on the right path.

---

**Sources**: Hermes WebUI, Kanna, OpenGUI, AICodeStudio, AG-UI Protocol
**Date**: June 12, 2026
