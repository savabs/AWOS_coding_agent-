# AWOS Agent Interface

A real-time interface for watching the coding agent plan, execute, verify, recover, and improve.

## 🚀 Quick Start

```bash
# Start the web server
python3 gui/server.py --port 8765

# Open the normal agent interface
open http://127.0.0.1:8765/agent

# Open the full developer renderer
open http://127.0.0.1:8765/renderer
```

## ✨ Features

- **Real-time agent monitoring** — Watch the agent plan, execute, verify, and recover
- **Developer renderer** — Inspect the full execution timeline, exploration, parallel tasks, files, evidence, and verification
- **Live progress tracking** — See which tasks are running, completed, or failed
- **Visual diffs** — View code changes with syntax highlighting
- **Session history** — Browse past coding-agent sessions
- **Statistics dashboard** — Track runs, tasks completed, cost, and efficiency trends

## 🎯 How It Works

1. **Start the server**: `python3 gui/server.py`
2. **Open the agent interface**: `http://127.0.0.1:8765/agent`
3. **Run a coding job**: `awos worker start "fix the auth bug" --root /path/to/repo`
4. **Optional coding-agent workload**: `awos fix-ci --root /path/to/repo` runs the CI repair adapter
5. **Watch it work**: The interface shows real-time progress for any coding task

## 📊 What You'll See

### Sidebar
- **Statistics**: Total runs, tasks completed, cost, and efficiency vs heavier baselines
- **Session history**: List of past coding-agent sessions with status

### Main View
- **Progress bar**: Visual indicator of task completion
- **Terminal output**: Real-time log of agent actions
- **File diffs**: Code changes with syntax highlighting

## 🎨 Design

The interface uses a familiar dark developer-tool aesthetic:
- Dark theme with GitHub-style colors
- Monospace font for terminal output
- Color-coded log entries (info, success, error, warning)
- Smooth animations and transitions

## 🔧 API Endpoints

The agent interface uses these endpoints:

- `GET /api/sessions/stats` — Aggregate coding-agent run statistics
- `GET /api/sessions/history` — Coding-agent run history
- `GET /api/ci-rescue/stats` — Backward-compatible legacy alias for session stats
- `GET /api/ci-rescue/history` — Backward-compatible legacy alias for session history
- `GET /api/sessions/<id>` — Session details
- `GET /api/sessions/<id>/files` — File diffs for a session

## 💡 Tips

- Click on a session in the sidebar to view its details
- The interface auto-refreshes every 5 seconds
- Use the terminal output to see what the agent is doing in real-time
- Check the file diffs to review code changes before merging

## 🚧 Coming Soon

- Interactive task approval/rejection
- Real-time test execution output
- Failure-learning metrics and charts
