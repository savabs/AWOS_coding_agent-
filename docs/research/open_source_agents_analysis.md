# Open-Source Coding Agents Research

**Date:** 2026-07-04  
**Purpose:** Analyze UI/UX patterns from successful coding agents to improve AWOS visual interface

---

## Executive Summary

Analyzed 4 major open-source coding agents with a combined **165k+ GitHub stars**:

| Agent | Stars | UI Type | Key Strength |
|-------|-------|---------|--------------|
| **OpenHands** | 79.4k | Web UI | Full agent canvas with multi-backend support |
| **Aider** | 47k | Terminal | Simple, focused, git integration |
| **SWE-agent** | 19.7k | Terminal + Trajectories | Research-focused, benchmark-driven |
| **Devika** | 19.5k | Web UI | Chat-based with project management |

---

## 1. Aider (47k stars)

**Repository:** https://github.com/Aider-AI/aider  
**Language:** Python (80%), CSS, Shell, JavaScript  
**UI Type:** Terminal-only

### Key Features

- **Terminal-based pair programming** - No web UI, pure CLI
- **Git integration** - Automatic commits with sensible messages
- **Code mapping** - Maps entire codebase for context
- **100+ languages** - Python, JavaScript, Rust, Ruby, Go, etc.
- **IDE integration** - Watch mode for editor integration
- **Voice-to-code** - Speak commands to the agent

### UI/UX Patterns

**Terminal Output:**
```
▶ aider --model sonnet
Added src/main.py to the chat
▶ add user authentication
...
```

**Progress Indicators:**
- Simple text-based progress
- File change notifications
- Git commit messages

**File Diffs:**
- Uses standard git diff format
- Color-coded in terminal (green/red)
- Shows before/after clearly

### Strengths

1. **Simplicity** - No complex UI, just works
2. **Git-first** - Every change is a commit
3. **Fast** - Terminal is instant
4. **Familiar** - Developers already use terminals

### Weaknesses

1. **No visual feedback** - Can't see agent "thinking"
2. **No real-time updates** - Wait for completion
3. **No file browser** - Can't explore changes visually
4. **No progress bar** - Don't know how long it will take

### Lessons for AWOS

✅ **Keep it simple** - Don't over-engineer the UI  
✅ **Git integration is essential** - Every change should be committable  
✅ **Terminal output matters** - Color coding helps readability  
❌ **Avoid terminal-only** - Users want visual feedback  
❌ **Don't skip progress indicators** - Users need to know what's happening  

---

## 2. OpenHands (79.4k stars)

**Repository:** https://github.com/OpenHands/OpenHands  
**Language:** Python (64.5%), TypeScript (34.1%)  
**UI Type:** Full web UI with agent canvas

### Key Features

- **Agent Canvas** - Visual control center for multiple agents
- **Multi-backend support** - Local, Docker, VM, Cloud
- **Automation server** - Schedule agents, webhook triggers
- **ACP protocol** - Works with Claude Code, Codex, Gemini
- **Real-time monitoring** - Live agent status updates

### UI/UX Patterns

**Web Interface:**
- Modern React/TypeScript frontend
- Sidebar with agent list
- Main area with agent canvas
- Real-time status updates
- File browser integration

**Agent Monitoring:**
- Live status indicators (running/idle/completed)
- Progress bars for long tasks
- Token usage tracking
- Cost tracking per agent

**File Diffs:**
- Monaco editor integration
- Side-by-side diff view
- Syntax highlighting
- Line numbers
- Accept/reject changes

### Architecture

```
┌─────────────────────────────────────┐
│      Agent Canvas (Frontend)        │
│  - React/TypeScript                 │
│  - Real-time updates                │
│  - Multi-agent management           │
└──────────────┬──────────────────────┘
               │ REST API / WebSocket
┌──────────────▼──────────────────────┐
│      Agent Server (Backend)         │
│  - Python FastAPI                   │
│  - Agent orchestration              │
│  - Tool execution                   │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│      Automation Server              │
│  - Scheduled tasks                  │
│  - Webhook handlers                 │
│  - Event triggers                   │
└─────────────────────────────────────┘
```

### Strengths

1. **Professional UI** - Looks like a real product
2. **Multi-agent** - Manage multiple agents at once
3. **Real-time** - Live updates via WebSocket
4. **Flexible** - Multiple backend options
5. **Extensible** - Plugin architecture

### Weaknesses

1. **Complex** - Heavy infrastructure requirements
2. **Overkill** - Too much for simple tasks
3. **Learning curve** - Need to understand the system
4. **Resource intensive** - Requires significant compute

### Lessons for AWOS

✅ **Real-time updates are essential** - Use WebSocket or SSE  
✅ **Professional UI matters** - Users judge by appearance  
✅ **Multi-agent support** - Users want to manage multiple tasks  
✅ **Progress tracking** - Show token usage, cost, time  
❌ **Don't over-engineer** - Start simple, add complexity later  
❌ **Avoid heavy dependencies** - Keep it lightweight  

---

## 3. SWE-agent (19.7k stars)

**Repository:** https://github.com/SWE-agent/SWE-agent  
**Language:** Python (94.8%)  
**UI Type:** Terminal + Trajectory visualization

### Key Features

- **GitHub issue fixing** - Takes issue, creates PR
- **Trajectory tracking** - Visualize agent's reasoning
- **Benchmark-focused** - Optimized for SWE-bench
- **Configurable** - Single YAML config file
- **Research-oriented** - Built by Princeton/Stanford

### UI/UX Patterns

**Terminal Output:**
```
🤖 SWE-agent is working on issue #123
📝 Reading issue description...
🔍 Analyzing codebase...
💡 Found relevant files: src/auth.py, src/user.py
🛠️  Making changes...
✅ Created PR #456
```

**Trajectory Visualization:**
- JSON-based trajectory logs
- Step-by-step reasoning
- Tool calls and responses
- Can replay agent's thought process

**File Diffs:**
- Standard git diff
- Shows in terminal
- Can be reviewed before commit

### Strengths

1. **Research-backed** - Academic rigor
2. **Benchmark-driven** - Optimized for performance
3. **Transparent** - Full trajectory logging
4. **Configurable** - Easy to customize
5. **Reproducible** - Same input = same output

### Weaknesses

1. **No real-time UI** - Terminal only
2. **Complex setup** - Requires Docker, API keys
3. **Slow iteration** - Research pace
4. **Limited interactivity** - Can't guide agent mid-task

### Lessons for AWOS

✅ **Trajectory logging is valuable** - Users want to see reasoning  
✅ **Benchmark focus** - Prove your agent works  
✅ **Transparency** - Show what the agent is doing  
✅ **Configuration** - Make it easy to customize  
❌ **Don't skip real-time UI** - Users want live feedback  
❌ **Avoid complex setup** - Keep it simple to start  

---

## 4. Devika (19.5k stars)

**Repository:** https://github.com/stitionai/devika  
**Language:** Python (57.5%), Svelte (22.1%)  
**UI Type:** Web UI with chat interface

### Key Features

- **Chat-based interface** - Natural language interaction
- **Project management** - Organize tasks by project
- **Web browsing** - Agent can search the web
- **Multi-model support** - Claude, GPT-4, Gemini, local LLMs
- **State tracking** - Visualize agent's current state

### UI/UX Patterns

**Web Interface:**
- Svelte-based frontend
- Chat interface (like ChatGPT)
- Project sidebar
- Settings panel
- Real-time agent status

**Chat Flow:**
```
User: Create a REST API for user management
Agent: I'll break this down into steps:
  1. Set up project structure
  2. Create database models
  3. Implement CRUD endpoints
  4. Add authentication
  5. Write tests

Starting with step 1...
[Progress bar: 20%]
```

**State Visualization:**
- Current step indicator
- Progress bar
- Token usage
- Cost tracking
- Time elapsed

### Architecture

```
┌─────────────────────────────────────┐
│      Web UI (Svelte)                │
│  - Chat interface                   │
│  - Project management               │
│  - Real-time updates                │
└──────────────┬──────────────────────┘
               │ REST API
┌──────────────▼──────────────────────┐
│      Backend (Python)               │
│  - Agent orchestration              │
│  - Tool execution                   │
│  - Web browsing                     │
└─────────────────────────────────────┘
```

### Strengths

1. **User-friendly** - Chat interface is familiar
2. **Visual feedback** - Progress bars, status indicators
3. **Project-based** - Organize work logically
4. **Multi-model** - Flexibility in model choice
5. **Web browsing** - Agent can research

### Weaknesses

1. **Early stage** - Many features incomplete
2. **Slow development** - Small team
3. **Limited customization** - Hard to extend
4. **No trajectory view** - Can't see reasoning

### Lessons for AWOS

✅ **Chat interface works** - Users are comfortable with it  
✅ **Progress bars are essential** - Show completion percentage  
✅ **Project organization** - Group related tasks  
✅ **Multi-model support** - Give users choices  
❌ **Don't stay in early stage** - Ship features quickly  
❌ **Avoid limited customization** - Make it extensible  

---

## Comparative Analysis

### UI/UX Comparison

| Feature | Aider | OpenHands | SWE-agent | Devika | AWOS (Current) |
|---------|-------|-----------|-----------|--------|----------------|
| **Web UI** | ❌ | ✅ | ❌ | ✅ | ✅ |
| **Terminal UI** | ✅ | ❌ | ✅ | ❌ | ✅ |
| **Real-time updates** | ❌ | ✅ | ❌ | ✅ | ⚠️ (polling) |
| **Progress bars** | ❌ | ✅ | ❌ | ✅ | ✅ |
| **File diffs** | ✅ | ✅ | ✅ | ❌ | ✅ |
| **Chat interface** | ❌ | ✅ | ❌ | ✅ | ❌ |
| **Multi-agent** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Trajectory view** | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Cost tracking** | ❌ | ✅ | ❌ | ✅ | ✅ |
| **Git integration** | ✅ | ❌ | ✅ | ❌ | ✅ |

### Technology Stack Comparison

| Agent | Frontend | Backend | Real-time | Database |
|-------|----------|---------|-----------|----------|
| **Aider** | Terminal | Python | N/A | Git |
| **OpenHands** | React/TS | Python/FastAPI | WebSocket | SQLite/Postgres |
| **SWE-agent** | Terminal | Python | N/A | JSON logs |
| **Devika** | Svelte | Python | REST polling | SQLite |
| **AWOS** | Vanilla JS | Python/http.server | REST polling | JSON files |

---

## Key Insights

### 1. Real-Time Updates Are Non-Negotiable

**Finding:** All successful agents with web UIs (OpenHands, Devika) have real-time updates.

**Implementation Options:**
- **WebSocket** - Best for bidirectional communication (OpenHands)
- **Server-Sent Events (SSE)** - Good for one-way streaming (simpler)
- **REST polling** - Works but inefficient (Devika, AWOS current)

**Recommendation for AWOS:**
```python
# Use SSE for simplicity
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

@app.get("/api/agent/stream")
async def stream_agent_updates():
    async def event_stream():
        while agent.is_running():
            yield f"data: {json.dumps(agent.get_status())}\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### 2. Progress Indicators Are Essential

**Finding:** Users need to know:
- What's happening now
- How much is done
- How long it will take
- How much it costs

**Implementation:**
```javascript
// Frontend progress component
<div class="progress-container">
  <div class="progress-bar">
    <div class="progress-fill" style="width: ${progress}%"></div>
  </div>
  <div class="progress-text">
    Task ${current}/${total} · ${cost} · ${time_elapsed}
  </div>
</div>
```

### 3. File Diffs Need Syntax Highlighting

**Finding:** All agents show file diffs, but only OpenHands has proper syntax highlighting.

**Implementation Options:**
- **Monaco Editor** - Full-featured, used by VS Code (heavy)
- **CodeMirror** - Lightweight, customizable (good balance)
- **Prism.js** - Simple syntax highlighting (lightweight)
- **highlight.js** - Another lightweight option

**Recommendation for AWOS:**
```html
<!-- Use CodeMirror for balance -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/python/python.min.js"></script>

<div id="diff-viewer"></div>
<script>
  const editor = CodeMirror(document.getElementById('diff-viewer'), {
    mode: 'python',
    theme: 'dracula',
    lineNumbers: true,
    readOnly: true
  });
</script>
```

### 4. Chat Interface Is Familiar

**Finding:** Devika's chat interface is intuitive because users are used to ChatGPT.

**Implementation:**
```html
<div class="chat-container">
  <div class="chat-messages" id="messages"></div>
  <div class="chat-input">
    <input type="text" id="user-input" placeholder="Describe your goal...">
    <button onclick="sendMessage()">Send</button>
  </div>
</div>
```

### 5. Multi-Agent Support Is Coming

**Finding:** OpenHands is the only one with multi-agent support, but it's a key differentiator.

**Future Consideration:**
```python
# Multi-agent orchestration
class AgentManager:
    def __init__(self):
        self.agents = {}
    
    def create_agent(self, name, task):
        agent = Agent(name, task)
        self.agents[name] = agent
        return agent
    
    def get_status(self):
        return {
            name: agent.get_status()
            for name, agent in self.agents.items()
        }
```

---

## Recommendations for AWOS

### Phase 1: Immediate Improvements (Week 1)

1. **Add Server-Sent Events (SSE)**
   - Replace polling with SSE for real-time updates
   - Update frontend to use EventSource API
   - Show live agent status

2. **Improve Progress Indicators**
   - Add progress bars to task list
   - Show percentage completion
   - Display estimated time remaining

3. **Add Syntax Highlighting**
   - Integrate CodeMirror or Prism.js
   - Highlight Python, JavaScript, etc.
   - Improve diff readability

### Phase 2: Enhanced Features (Week 2-3)

4. **Add Chat Interface**
   - Create chat view alongside dashboard
   - Allow natural language interaction
   - Show agent responses in real-time

5. **Add Trajectory View**
   - Log agent reasoning steps
   - Visualize decision tree
   - Allow replay of agent's thought process

6. **Improve File Browser**
   - Add file tree view
   - Show modified files
   - Allow quick navigation

### Phase 3: Advanced Features (Week 4+)

7. **Multi-Agent Support**
   - Allow multiple concurrent agents
   - Show agent status grid
   - Manage agent lifecycle

8. **Cost Analytics**
   - Track cost per task
   - Show cost trends
   - Compare model costs

9. **Performance Metrics**
   - Track success rate
   - Show time per task
   - Benchmark against baselines

---

## Implementation Priority

### High Priority (Must Have)

1. ✅ **Real-time updates (SSE)** - Users expect live feedback
2. ✅ **Progress indicators** - Users need to know status
3. ✅ **Syntax highlighting** - Diffs are unreadable without it
4. ✅ **Better error messages** - Users need to understand failures

### Medium Priority (Should Have)

5. **Chat interface** - Familiar interaction pattern
6. **Trajectory view** - Transparency in reasoning
7. **File browser** - Easy navigation
8. **Cost tracking** - Budget awareness

### Low Priority (Nice to Have)

9. **Multi-agent support** - Advanced feature
10. **Performance analytics** - Optimization insights
11. **Custom themes** - Personalization
12. **Plugin system** - Extensibility

---

## Technology Recommendations

### Frontend Stack

**Current:** Vanilla HTML/CSS/JS  
**Recommended:** Keep vanilla for now, add libraries as needed

**Libraries to Add:**
- **CodeMirror** - Syntax highlighting for diffs
- **Chart.js** - Cost and performance charts
- **Marked.js** - Markdown rendering for agent responses

### Backend Stack

**Current:** Python http.server  
**Recommended:** Migrate to FastAPI for better performance

**Benefits of FastAPI:**
- Async support for SSE
- Better performance
- Automatic API docs
- Type validation

**Migration Plan:**
```python
# Current (http.server)
class GuiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        ...

# New (FastAPI)
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/api/agent/status")
async def get_status():
    return agent.get_status()

@app.get("/api/agent/stream")
async def stream_updates():
    async def event_stream():
        while agent.is_running():
            yield f"data: {json.dumps(agent.get_status())}\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(event_stream())
```

### Database

**Current:** JSON files  
**Recommended:** SQLite for better querying

**Benefits of SQLite:**
- Better performance
- SQL queries
- ACID compliance
- Still file-based (no server needed)

---

## Conclusion

The research shows that successful coding agents share these patterns:

1. **Real-time updates** - Users expect live feedback
2. **Progress indicators** - Users need to know status
3. **Syntax highlighting** - Code must be readable
4. **Chat interface** - Familiar interaction pattern
5. **Git integration** - Changes should be committable

AWOS already has a good foundation. The key improvements are:

1. **Add SSE for real-time updates** (replace polling)
2. **Add syntax highlighting** (CodeMirror)
3. **Add chat interface** (natural language interaction)
4. **Add trajectory view** (show reasoning)

These changes will bring AWOS to parity with OpenHands and Devika, while maintaining the simplicity that makes Aider popular.

---

## Next Steps

1. **Implement SSE** - Replace polling with Server-Sent Events
2. **Add CodeMirror** - Syntax highlighting for diffs
3. **Create chat view** - Natural language interaction
4. **Add trajectory logging** - Show agent reasoning
5. **Test with users** - Get feedback on new features

**Timeline:** 2-3 weeks for Phase 1-2, then iterate based on feedback.
