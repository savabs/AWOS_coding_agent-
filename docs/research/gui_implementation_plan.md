# AWOS GUI Implementation Plan - Based on Industry Research

**Date**: June 12, 2026  
**Based on**: Research from Hermes WebUI, Kanna, OpenGUI, AICodeStudio, AG-UI Protocol

---

## Overview

After analyzing leading open-source agent GUIs, we've validated that **our current architecture is sound**. This document outlines concrete improvements we can make, prioritized by impact and effort.

---

## Current State Assessment

### What We Have ✅

```
gui/
  server.py              # Python stdlib HTTP server
  static/
    index.html           # Single HTML with tabs
    style.css            # Styling for chat + runs
    shared.js            # Common utilities (API, markdown)
    chat.js              # Chat tab logic
    app.js               # Runs tab logic

scaffold/agent/
  gui_chat.py            # Chat backend (Q/A, Plan, Agent)
  gui_events.py          # GUI event bus
  orchestrator.py        # Agent execution (emits events)

.awos/
  gui/
    <session_id>/
      events.jsonl       # Event stream per run
      manifest.json      # Run summary + file changes
  gui_chat/
    <chat_id>.json       # Chat session history
```

### What Works Well ✅

1. **Simple architecture**: No build step, stdlib HTTP server
2. **Event-driven**: SSE for real-time streaming
3. **Dual output**: Terminal + GUI events
4. **Mode separation**: Q/A, Plan, Agent modes
5. **Clean separation**: Backend logic vs frontend presentation

### What's Missing ⚠️

1. No AG-UI protocol compliance (custom events)
2. Limited mobile responsiveness
3. No prompt queue (can't queue while agent running)
4. No slash commands
5. No theme switching
6. No workspace file browser
7. No PWA manifest (can't install as desktop app)
8. No authentication

---

## Implementation Roadmap

### 🚀 Phase 1: Quick Wins (1-2 days)

#### 1.1 PWA Manifest (30 min)

Add `gui/static/manifest.json`:

```json
{
  "name": "AWOS Agent",
  "short_name": "AWOS",
  "description": "Adaptive Weight Optimization System - AI Coding Agent",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0a0a0a",
  "theme_color": "#3b82f6",
  "icons": [
    {
      "src": "/static/icon-192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "/static/icon-512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
```

Update `index.html`:

```html
<head>
  <link rel="manifest" href="/static/manifest.json">
  <meta name="theme-color" content="#3b82f6">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
</head>
```

**Result**: Users can install AWOS as a desktop app (like Electron, but simpler).

---

#### 1.2 Slash Commands (1-2 hours)

Add to `gui/static/chat.js`:

```javascript
// Slash command autocomplete
const SLASH_COMMANDS = {
  '/help': 'Show available commands',
  '/clear': 'Clear chat history',
  '/retry': 'Retry last message',
  '/stop': 'Stop current agent run',
  '/mode': 'Switch mode (qa/plan/agent)',
  '/history': 'Show chat history',
};

function handleSlashCommand(input) {
  const cmd = input.trim().toLowerCase();
  
  if (cmd === '/help') {
    return showHelp();
  } else if (cmd === '/clear') {
    return clearChat();
  } else if (cmd === '/retry') {
    return retryLastMessage();
  } else if (cmd === '/stop') {
    return stopCurrentRun();
  } else if (cmd.startsWith('/mode ')) {
    const mode = cmd.split(' ')[1];
    return switchMode(mode);
  } else if (cmd === '/history') {
    return showHistory();
  }
  
  return null; // Not a command, treat as regular message
}

// Add autocomplete dropdown when typing /
function showSlashAutocomplete(partialCmd) {
  const matches = Object.keys(SLASH_COMMANDS)
    .filter(cmd => cmd.startsWith(partialCmd))
    .map(cmd => ({ cmd, desc: SLASH_COMMANDS[cmd] }));
  
  renderAutocomplete(matches);
}
```

**Result**: Faster workflows, better UX.

---

#### 1.3 Theme Switching (1 hour)

Add to `gui/static/style.css`:

```css
/* Light theme */
[data-theme="light"] {
  --bg-primary: #ffffff;
  --bg-secondary: #f3f4f6;
  --text-primary: #111827;
  --text-secondary: #6b7280;
  --border: #e5e7eb;
  --accent: #3b82f6;
}

/* Dark theme (default) */
[data-theme="dark"] {
  --bg-primary: #0a0a0a;
  --bg-secondary: #1a1a1a;
  --text-primary: #f9fafb;
  --text-secondary: #9ca3af;
  --border: #374151;
  --accent: #60a5fa;
}

body {
  background: var(--bg-primary);
  color: var(--text-primary);
}
```

Add theme toggle in `gui/static/shared.js`:

```javascript
function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const newTheme = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);
}

// Add button to nav
document.querySelector('nav').insertAdjacentHTML('beforeend', `
  <button onclick="toggleTheme()" class="theme-toggle">
    🌓
  </button>
`);
```

**Result**: Better accessibility, user preference support.

---

### 🎯 Phase 2: AG-UI Protocol Alignment (2-3 days)

#### 2.1 Map Current Events to AG-UI

**Current events** (in `gui_events.py`):

```python
# What we emit now
self.record_event("plan_complete", {...})
self.record_event("worker_start", {...})
self.record_event("file_change", {...})
self.record_event("verification_pass", {...})
```

**AG-UI equivalent**:

```python
# What AG-UI spec defines
emit_event("RUN_STARTED", {...})        # = plan_complete
emit_event("TOOL_CALL_START", {...})    # = worker_start
emit_event("TOOL_CALL_END", {...})      # = file_change
emit_event("RUN_FINISHED", {...})       # = verification_pass
emit_event("TEXT_MESSAGE_CHUNK", {...}) # = streaming text
```

#### 2.2 Implementation

Update `scaffold/agent/gui_events.py`:

```python
class GuiEventBus:
    """AG-UI compliant event bus for GUI communication."""
    
    # AG-UI event types
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CHUNK = "TEXT_MESSAGE_CHUNK"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_CHUNK = "TOOL_CALL_CHUNK"
    TOOL_CALL_END = "TOOL_CALL_END"
    STATE_UPDATE = "STATE_UPDATE"
    
    def emit_run_started(self, run_id: str, goal: str):
        self.record_event(self.RUN_STARTED, {
            "runId": run_id,
            "goal": goal,
            "timestamp": datetime.now().isoformat()
        })
    
    def emit_tool_call(self, tool_name: str, args: dict, result: Any):
        call_id = str(uuid.uuid4())
        
        # Start
        self.record_event(self.TOOL_CALL_START, {
            "callId": call_id,
            "toolName": tool_name,
            "arguments": args,
            "timestamp": datetime.now().isoformat()
        })
        
        # End
        self.record_event(self.TOOL_CALL_END, {
            "callId": call_id,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })
    
    def emit_text_chunk(self, run_id: str, content: str):
        self.record_event(self.TEXT_MESSAGE_CHUNK, {
            "runId": run_id,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
```

Update `scaffold/agent/orchestrator.py`:

```python
# Before
_gui.record_event("plan_complete", {"plan": plan.plan_text})

# After
_gui.emit_event(GuiEventBus.TOOL_CALL_END, {
    "callId": f"{session.session_id}_plan",
    "toolName": "create_plan",
    "result": plan.plan_text
})
```

**Result**: Frontend can be swapped with any AG-UI compatible client.

---

### 🔨 Phase 3: Enhanced Features (3-5 days)

#### 3.1 Workspace File Browser (1-2 days)

Like Hermes WebUI, show files in workspace with:
- Changed files highlighted
- Git status (modified, added, deleted)
- File preview
- Diff viewer

Add to `gui/server.py`:

```python
def do_GET(self):
    if path == '/api/workspace/files':
        files = list_workspace_files()
        return self.json_response(files)
    
    elif path.startswith('/api/workspace/file/'):
        file_path = path.replace('/api/workspace/file/', '')
        content = read_file(file_path)
        return self.json_response({"content": content})
    
    elif path == '/api/workspace/git-status':
        status = get_git_status()
        return self.json_response(status)
```

Add to `gui/static/index.html`:

```html
<div id="workspace-panel" class="panel">
  <div class="panel-header">
    <h3>Workspace</h3>
    <button onclick="refreshWorkspace()">🔄</button>
  </div>
  <div id="file-tree"></div>
  <div id="file-preview"></div>
</div>
```

**Result**: Users can see what files the agent is working with.

---

#### 3.2 Prompt Queue (1 day)

Allow queuing messages while agent is running.

Add to `gui/static/chat.js`:

```javascript
const messageQueue = [];
let isAgentRunning = false;

async function sendMessage(message, mode) {
  if (isAgentRunning) {
    // Queue it
    messageQueue.push({ message, mode });
    showQueuedMessage(message);
    return;
  }
  
  isAgentRunning = true;
  try {
    await actualSendMessage(message, mode);
  } finally {
    isAgentRunning = false;
    processQueue();
  }
}

function processQueue() {
  if (messageQueue.length > 0 && !isAgentRunning) {
    const next = messageQueue.shift();
    sendMessage(next.message, next.mode);
  }
}
```

Add visual indicator:

```html
<div id="queue-indicator" style="display:none">
  <span>📬 <span id="queue-count">0</span> messages queued</span>
</div>
```

**Result**: Users don't have to wait for agent to finish before typing next request.

---

#### 3.3 Session Management UI (1 day)

Better session picker and history.

Add to `gui/server.py`:

```python
def do_GET(self):
    if path == '/api/runs':
        runs = list_all_runs()  # From .awos/gui/
        return self.json_response(runs)
    
    elif path == '/api/runs/search':
        query = params.get('q', '')
        results = search_runs(query)
        return self.json_response(results)
```

Add to `gui/static/app.js`:

```javascript
// Enhanced runs list with search
function renderRunsList(runs) {
  const html = `
    <div class="runs-list">
      <input type="search" 
             placeholder="Search runs..." 
             onkeyup="searchRuns(this.value)">
      <div class="runs-grid">
        ${runs.map(run => `
          <div class="run-card" onclick="loadRun('${run.session_id}')">
            <div class="run-time">${formatTime(run.timestamp)}</div>
            <div class="run-goal">${run.goal}</div>
            <div class="run-status ${run.status}">${run.status}</div>
            <div class="run-stats">
              ${run.files_changed} files • ${run.elapsed_sec}s
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
  document.getElementById('runs-content').innerHTML = html;
}
```

**Result**: Users can easily find and review past runs.

---

#### 3.4 Optional Authentication (1 day)

Copy Hermes approach: optional password auth with signed cookies.

Add to `gui/server.py`:

```python
import hmac
import hashlib
import os

AUTH_ENABLED = os.getenv('AWOS_GUI_AUTH', 'false').lower() == 'true'
AUTH_PASSWORD = os.getenv('AWOS_GUI_PASSWORD', '')
SECRET_KEY = os.getenv('AWOS_GUI_SECRET', os.urandom(32).hex())

def check_auth(handler):
    if not AUTH_ENABLED:
        return True
    
    # Check for auth cookie
    cookies = handler.headers.get('Cookie', '')
    if 'awos_auth=' in cookies:
        token = cookies.split('awos_auth=')[1].split(';')[0]
        if verify_token(token):
            return True
    
    # Require login
    if handler.path == '/api/auth/login':
        return True
    
    handler.send_response(401)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json.dumps({"error": "Authentication required"}).encode())
    return False

def verify_token(token):
    try:
        expected = hmac.new(
            SECRET_KEY.encode(),
            AUTH_PASSWORD.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(token, expected)
    except:
        return False
```

Add login page to `gui/static/index.html`:

```html
<div id="login-screen" style="display:none">
  <h2>AWOS Agent - Login</h2>
  <input type="password" id="password" placeholder="Password">
  <button onclick="login()">Login</button>
</div>
```

**Result**: Can safely expose GUI over network with password protection.

---

### 🚀 Phase 4: Advanced Features (Later)

#### 4.1 WebSocket Upgrade

Replace SSE with WebSocket for bidirectional real-time communication.

**Why**: 
- Bidirectional (can cancel runs from GUI)
- Lower latency
- More efficient

**How**:
- Use `websockets` library in Python
- Update `gui/static/shared.js` to use WebSocket API
- Implement reconnection logic

#### 4.2 Monaco Editor Integration

Embed VSCode's Monaco editor for inline code editing.

**Why**:
- Edit code directly in GUI
- Syntax highlighting
- Autocomplete

**How**:
- Load Monaco from CDN: `https://cdn.jsdelivr.net/npm/monaco-editor/`
- Add editor pane to workspace panel
- Implement save → agent review flow

#### 4.3 Voice Input Support

Add speech-to-text for accessibility.

**Why**:
- Accessibility
- Hands-free operation
- Mobile-friendly

**How**:
- Use Web Speech API (`webkitSpeechRecognition`)
- Add microphone button to chat input
- Visual feedback during recording

---

## File Changes Checklist

### New Files to Create

```
gui/static/manifest.json          # PWA manifest
gui/static/icon-192.png            # App icon (192x192)
gui/static/icon-512.png            # App icon (512x512)
docs/specs/ag_ui_spec.md           # AG-UI protocol documentation
```

### Files to Modify

```
gui/server.py                      # Add workspace API, auth
gui/static/index.html              # PWA meta tags, workspace panel
gui/static/style.css               # Theme variables, workspace styles
gui/static/shared.js               # Theme toggle, slash commands
gui/static/chat.js                 # Prompt queue, slash command handling
gui/static/app.js                  # Enhanced runs list, search
scaffold/agent/gui_events.py       # AG-UI event types
scaffold/agent/orchestrator.py     # Emit AG-UI events
scaffold/agent/gui_chat.py         # Use AG-UI events
```

---

## Success Metrics

### Phase 1 (Quick Wins)
- [ ] PWA installable on desktop (Chrome "Install" button appears)
- [ ] Slash commands work (`/help`, `/clear`, `/retry`)
- [ ] Theme switching works (dark ↔ light)

### Phase 2 (AG-UI)
- [ ] All events comply with AG-UI spec
- [ ] Frontend can consume events from AG-UI-compliant backend
- [ ] Documentation updated with AG-UI event types

### Phase 3 (Features)
- [ ] Workspace file browser shows changed files
- [ ] Prompt queue allows typing while agent runs
- [ ] Session search finds past runs by keyword
- [ ] Optional auth protects GUI with password

---

## Testing Plan

### Manual Testing

```bash
# Start GUI
cd gui
python3 server.py

# Open in browser
open http://localhost:8080

# Test PWA
- Chrome → three dots → "Install AWOS"
- Verify app opens in standalone window

# Test slash commands
- Type /help → shows help
- Type /clear → clears chat
- Type /retry → retries last message

# Test theme
- Click theme toggle
- Verify colors change
- Verify choice persists on reload

# Test workspace browser
- Run an agent task
- Check workspace panel shows changed files
- Click file → preview shows

# Test prompt queue
- Send message in Agent mode
- While running, type another message
- Verify queue indicator appears
- Verify second message sends after first completes
```

### Automated Testing

```python
# tests/test_gui_events.py
def test_ag_ui_event_format():
    bus = GuiEventBus(session_id="test")
    bus.emit_run_started("run_1", "Test goal")
    
    events = bus.get_events()
    assert events[0]["type"] == "RUN_STARTED"
    assert "runId" in events[0]
    assert "timestamp" in events[0]

def test_slash_command_parsing():
    assert parse_slash_command("/help") == ("help", [])
    assert parse_slash_command("/mode agent") == ("mode", ["agent"])
    assert parse_slash_command("regular message") is None
```

---

## Timeline Estimate

| Phase | Features | Time | Priority |
|-------|----------|------|----------|
| Phase 1 | PWA + Slash + Theme | 1-2 days | 🔥 High |
| Phase 2 | AG-UI Protocol | 2-3 days | 🎯 High |
| Phase 3 | Workspace + Queue + Sessions + Auth | 3-5 days | ⚠️ Medium |
| Phase 4 | WebSocket + Monaco + Voice | 5+ days | 💡 Low |

**Total for v1**: ~6-10 days of focused work

---

## Conclusion

Our current AWOS GUI architecture is validated by industry leaders (Hermes, Kanna). The improvements outlined here are:

1. **Incremental**: Build on what we have, not rewrite
2. **Standards-based**: Adopt AG-UI for future compatibility
3. **User-focused**: PWA, slash commands, themes improve UX
4. **Optional**: Each phase can be done independently

**Recommendation**: Start with Phase 1 (quick wins) immediately, then Phase 2 (AG-UI) for long-term compatibility.

---

**End of Implementation Plan**
