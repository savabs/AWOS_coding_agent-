# Agent GUI Patterns - Research from Open Source Projects (2026)

**Date**: June 12, 2026  
**Purpose**: Learn from leading open-source agent GUIs to inform AWOS GUI development

---

## Executive Summary

After researching leading open-source agent projects (Hermes WebUI, Kanna, OpenGUI, AICodeStudio), a clear architectural pattern emerges:

1. **Keep it simple**: Python stdlib HTTP server + vanilla JS (Hermes approach) works extremely well
2. **Event-driven architecture**: WebSocket/SSE for real-time agent communication
3. **Standardized protocols**: AG-UI protocol is becoming the standard (adopted by Google, AWS, Microsoft, LangChain)
4. **Separation of concerns**: Backend handles agent logic, frontend handles presentation
5. **No build complexity**: Avoid framework overhead unless necessary

**Our current AWOS GUI aligns well with these patterns.** We're on the right track.

---

## 1. Hermes WebUI - The Minimal Approach

**Repository**: [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) (14K+ stars)  
**Philosophy**: "No build step, no framework, no bundler"

### Architecture

```
server.py         # Entry point - Python stdlib HTTP server
api/
  auth.py         # Authentication (password, signed cookies, passkeys)
  config.py       # Configuration discovery and management
  helpers.py      # HTTP helpers, security headers
  models.py       # Session model + CRUD + agent state bridge
  routes.py       # GET/POST handlers (if/elif dispatch, no decorators)
  streaming.py    # SSE engine for agent runs
  workspace.py    # File operations, workspace browser
static/
  index.html      # Single HTML file
  style.css       # All CSS (includes mobile responsive, themes)
  ui.js           # DOM helpers, markdown rendering, tool cards
  sessions.js     # Session CRUD, grouping, search
  messages.js     # Message sending, SSE handlers, streaming
  workspace.js    # File preview, git integration
  panels.js       # Skills, memory, profiles, settings
  commands.js     # Slash command autocomplete
  boot.js         # Mobile nav, theme switching
```

### Key Design Decisions

1. **HTTP Server**: Uses Python's `http.server.ThreadingHTTPServer` (stdlib)
2. **No Framework**: Vanilla JavaScript, no React/Vue/build step
3. **SSE for Streaming**: Server-sent events for real-time agent output
4. **Three-Panel Layout**: Sessions sidebar | Chat | Workspace file browser
5. **Mobile-First**: Fully responsive with hamburger menu
6. **Authentication**: Optional password auth with signed cookies
7. **State Persistence**: Reads from agent's `~/.hermes/` directory

### What We Can Learn

✅ **We're doing this right**: Our `gui/server.py` uses stdlib HTTP server  
✅ **We're doing this right**: Vanilla JS in `gui/static/`  
✅ **We're doing this right**: SSE/events for streaming  
⚠️ **Could improve**: Add workspace file browser like Hermes  
⚠️ **Could improve**: More sophisticated mobile responsive design  
⚠️ **Could improve**: Theme switching support

---

## 2. Kanna - The Event-Sourcing Approach

**Repository**: [jakemor/kanna](https://github.com/jakemor/kanna/) (556 stars)  
**Philosophy**: "Event sourcing + CQRS + WebSocket reactive broadcasting"

### Architecture

```
src/
  client/              # React + Zustand
    app/               # App router, pages, state hooks
  server/              # Bun backend (HTTP + WebSocket)
    agent.ts           # AgentCoordinator (multi-provider)
    provider-catalog.ts # Provider/model normalization
    ws-router.ts       # WebSocket routing & subscriptions
    event-store.ts     # JSONL persistence + snapshots
    read-models.ts     # Derived views from event log
  shared/              # Shared types & protocols
    types.ts           # Core data types
    protocol.ts        # WebSocket message protocol
    ports.ts           # Port configuration
```

### Key Design Decisions

1. **Event Sourcing**: All state mutations persisted as JSONL logs
2. **CQRS Pattern**: Separate write (event log) and read (snapshots) paths
3. **WebSocket**: Real-time bidirectional communication
4. **Reactive Broadcasting**: Subscribers get pushed state updates
5. **Multi-Provider**: Supports multiple LLM providers with normalization
6. **Bun Runtime**: Uses Bun instead of Node.js for performance

### Data Flow

```
Browser (React + Zustand)
    ↕  WebSocket
Bun Server (HTTP + WS)
    ├── WSRouter ─── subscription & command routing
    ├── AgentCoordinator ─── multi-provider turn management
    ├── EventStore ─── JSONL persistence + snapshots
    └── ReadModels ─── derived views
    ↕  stdio
Agent Process (Claude Code / Codex)
```

### What We Can Learn

⚠️ **Consider**: Event sourcing for full audit trail (we partially do this with `events.jsonl`)  
⚠️ **Consider**: WebSocket for more interactive real-time updates (we use SSE)  
❌ **Skip for now**: Bun/CQRS complexity - premature for v1  
✅ **We're doing this right**: Multi-provider support (we have this)

---

## 3. AG-UI Protocol - The Emerging Standard

**Adopted by**: Google, AWS, Microsoft, LangChain, CopilotKit, Mastra

### What is AG-UI?

AG-UI (Agent-User Interaction) is an open protocol for how agents communicate with frontends.

**Think of it like HTTP or MCP** - a specification, not an implementation.

### Protocol Overview

- **16+ event types**: Streaming chat, tool calls, state sync, generative UI, interrupts
- **Transport agnostic**: Works over SSE, WebSockets, or webhooks
- **Bidirectional**: Agent → UI (events) and UI → Agent (controls)
- **Human-in-the-loop**: Pause, approve, reject, redirect agent execution

### Standard Event Types

```typescript
// Lifecycle
RUN_STARTED
RUN_FINISHED
RUN_ERROR

// Text streaming
TEXT_MESSAGE_START
TEXT_MESSAGE_CHUNK
TEXT_MESSAGE_END

// Tool execution
TOOL_CALL_START
TOOL_CALL_CHUNK
TOOL_CALL_END

// State sync
STATE_UPDATE
APPROVAL_REQUEST
```

### Architecture Pattern

```
Frontend (React/Vanilla JS)
    ↕ AG-UI Events (SSE/WebSocket)
AG-UI Adapter
    ↕ Framework-Native Events
Agent Backend (LangGraph/CrewAI/Custom)
```

### Benefits

1. **Framework agnostic**: Switch between LangGraph, CrewAI, etc. without changing frontend
2. **Standardized**: Well-documented protocol with wide adoption
3. **Composable**: Works with MCP (Model Context Protocol) for tool interfaces
4. **Future-proof**: Industry is converging on this standard

### What We Can Learn

🚀 **We should adopt AG-UI**: Our `events.jsonl` is similar but custom  
✅ **We're close**: Our event structure is already similar to AG-UI  
⚠️ **Migration path**: Map our current events to AG-UI spec  
✅ **Keep separation**: AG-UI for GUI, telemetry.jsonl for internal logging

---

## 4. OpenGUI - The Desktop App Approach

**Repository**: [akemmanuel/OpenGUI](https://github.com/akemmanuel/OpenGUI) (27 stars)  
**Tech**: Electron + React + TypeScript

### Features

- **Desktop app** (not browser-based)
- **Multi-project workspaces**
- **Streaming chat over SSE**
- **Prompt queue** (queue prompts while agent is working)
- **Model switching** from UI
- **Voice input** support
- **Slash commands** from prompt box
- **Syntax highlighting** (Shiki) + math rendering (KaTeX)

### File Structure

```
src/
  index.ts          # Bun web server
  index.html        # HTML entry point
  frontend.tsx      # React entry point
  App.tsx           # Main app layout
  hooks/
    use-opencode.tsx  # Central state management
    useSTT.ts         # Speech-to-text
  components/       # UI components
  lib/              # Utilities
  types/            # TypeScript definitions
```

### What We Can Learn

⚠️ **Consider**: Prompt queue is a great UX feature  
⚠️ **Consider**: Voice input for accessibility  
❌ **Skip**: Electron adds complexity - browser-first is better for now  
✅ **Consider**: Slash commands (like `/help`, `/clear`, `/retry`)

---

## 5. AICodeStudio - The PWA Approach

**Repository**: [smouj/AICodeStudio](https://github.com/smouj/AICodeStudio) (3 stars)  
**Tech**: Next.js 16 + React 19 + Monaco Editor

### Key Concept

**Progressive Web App (PWA)**: Installable as desktop app *without* Electron

### Architecture

- **Next.js 16** with App Router
- **Monaco Editor** (VSCode's editor engine)
- **Zustand** for state management
- **Tailwind CSS + shadcn/ui** for styling
- **isomorphic-git** for browser-based Git operations
- **PWA manifest** for desktop installation

### What We Can Learn

🚀 **This is interesting**: PWA gives desktop experience without Electron  
⚠️ **Consider**: Monaco Editor for inline code editing in GUI  
⚠️ **Consider**: isomorphic-git for Git operations in browser  
✅ **Add PWA manifest**: Easy win for desktop-like experience

---

## Comparison Table

| Aspect | Hermes WebUI | Kanna | OpenGUI | AICodeStudio | AWOS GUI (current) |
|--------|--------------|-------|---------|--------------|-------------------|
| **Backend** | Python stdlib | Bun + WS | Bun + SSE | Next.js API | Python stdlib |
| **Frontend** | Vanilla JS | React + Zustand | React | React 19 | Vanilla JS |
| **Build Step** | None | Vite | Electron | Next.js | None |
| **Communication** | SSE | WebSocket | SSE | API + SSE | SSE |
| **State Persistence** | Agent's DB | Event sourcing | Project files | Database | events.jsonl + sessions |
| **Mobile** | ✅ Full support | ⚠️ Desktop-first | ❌ Desktop only | ⚠️ PWA | ⚠️ Basic |
| **Protocol** | Custom | Custom | Custom | Custom | Custom (AG-UI-like) |
| **Auth** | ✅ Password | ❌ None | ❌ None | ✅ OAuth | ❌ None |
| **Complexity** | Low | Medium | Medium | High | Low |

---

## Key Takeaways for AWOS

### What We're Doing Right ✅

1. **Simple architecture**: Python stdlib + vanilla JS is validated by Hermes (14K stars)
2. **Event-driven**: Our `events.jsonl` + SSE approach is industry standard
3. **Separation of concerns**: Backend (orchestrator) vs Frontend (GUI) is clean
4. **No build complexity**: Avoiding React/Vite/bundlers keeps things maintainable

### Quick Wins 🚀

1. **Adopt AG-UI protocol**: Map our events to AG-UI spec for future compatibility
2. **Add PWA manifest**: Enable desktop installation without Electron
3. **Workspace file browser**: Like Hermes, show files changed/affected
4. **Prompt queue**: Allow queuing messages while agent is running
5. **Slash commands**: Add `/help`, `/clear`, `/retry`, `/stop`
6. **Theme switching**: Dark/light mode toggle

### Medium-Term Improvements ⚠️

1. **Mobile responsive**: Improve mobile layout (hamburger menu, touch-friendly)
2. **WebSocket option**: Upgrade from SSE to WebSocket for bidirectional real-time
3. **Session management**: Better session picker and history
4. **Monaco Editor**: Inline code editing in GUI
5. **Authentication**: Optional password protection (copy Hermes approach)
6. **Model switching**: UI for changing models mid-conversation

### What to Skip ❌

1. **Electron**: Browser-first + PWA is simpler
2. **React/Vue**: Vanilla JS is working well, no need for complexity
3. **Event sourcing (full CQRS)**: Overkill for v1
4. **Bun**: Python stdlib is fine, no need to rewrite

---

## Recommended Next Steps

### Phase 1: Protocol Alignment (1-2 days)
- [ ] Map our current events to AG-UI specification
- [ ] Update `GuiEventBus` to emit AG-UI-compliant events
- [ ] Add AG-UI documentation to `docs/specs/gui_layer_spec.md`

### Phase 2: UX Polish (2-3 days)
- [ ] Add PWA manifest for desktop installation
- [ ] Implement slash commands (`/help`, `/clear`, `/retry`, `/stop`)
- [ ] Add theme switching (dark/light)
- [ ] Improve mobile responsive design

### Phase 3: Enhanced Features (3-5 days)
- [ ] Workspace file browser (show changed files)
- [ ] Prompt queue (queue messages while agent running)
- [ ] Session picker and history UI
- [ ] Optional password authentication

### Phase 4: Advanced (Later)
- [ ] WebSocket upgrade for bidirectional real-time
- [ ] Monaco Editor integration for inline editing
- [ ] Voice input support
- [ ] Git integration in GUI

---

## References

- **Hermes WebUI**: https://github.com/nesquena/hermes-webui
- **Kanna**: https://github.com/jakemor/kanna
- **OpenGUI**: https://github.com/akemmanuel/OpenGUI
- **AICodeStudio**: https://github.com/smouj/AICodeStudio
- **AG-UI Protocol**: https://github.com/CopilotKit/CopilotKit
- **Awesome AI Agents 2026**: https://github.com/ARUNAGIRINATHAN-K/awesome-ai-agents-2026

---

## Appendix: Code Snippets

### Hermes WebUI - Server Entry Point

```python
"""
Hermes Web UI -- Main server entry point.
Thin routing shell: imports Handler, delegates to api/routes.py, runs server.
All business logic lives in api/*.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from api.auth import check_auth
from api.config import HOST, PORT, STATE_DIR
from api.routes import handle_get, handle_post

class Handler(BaseHTTPRequestHandler):
    server_version = 'HermesWebUI/0.2'
    
    def do_GET(self):
        if not check_auth(self):
            return
        handle_get(self)
    
    def do_POST(self):
        if not check_auth(self):
            return
        handle_post(self)

def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Hermes Web UI listening on http://{HOST}:{PORT}')
    httpd.serve_forever()
```

### Kanna - WebSocket Event Flow

```typescript
// src/server/ws-router.ts
export class WSRouter {
  private subscriptions = new Map<string, WebSocket[]>()
  
  subscribe(ws: WebSocket, channel: string) {
    if (!this.subscriptions.has(channel)) {
      this.subscriptions.set(channel, [])
    }
    this.subscriptions.get(channel)!.push(ws)
  }
  
  broadcast(channel: string, event: any) {
    const subs = this.subscriptions.get(channel) || []
    const payload = JSON.stringify(event)
    subs.forEach(ws => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(payload)
      }
    })
  }
}

// src/server/event-store.ts
export class EventStore {
  async append(event: Event) {
    // Persist to JSONL
    await fs.appendFile(
      this.logPath,
      JSON.stringify(event) + '\n'
    )
    
    // Update snapshots
    this.applyToSnapshot(event)
    
    // Broadcast to subscribers
    this.router.broadcast(event.channel, event)
  }
}
```

### AG-UI Event Examples

```typescript
// Run lifecycle
{
  "type": "RUN_STARTED",
  "runId": "run_abc123",
  "timestamp": "2026-06-12T15:30:00Z"
}

// Streaming text
{
  "type": "TEXT_MESSAGE_CHUNK",
  "runId": "run_abc123",
  "content": "Let me analyze the code...",
  "timestamp": "2026-06-12T15:30:01Z"
}

// Tool execution
{
  "type": "TOOL_CALL_START",
  "runId": "run_abc123",
  "toolName": "read_file",
  "arguments": {"path": "src/main.py"},
  "timestamp": "2026-06-12T15:30:02Z"
}

// Run completion
{
  "type": "RUN_FINISHED",
  "runId": "run_abc123",
  "status": "success",
  "timestamp": "2026-06-12T15:30:15Z"
}
```

---

**End of Research Document**
