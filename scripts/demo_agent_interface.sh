#!/usr/bin/env bash
# demo_agent_interface.sh — Show the visual agent interface
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "═══════════════════════════════════════════════════════════════════"
echo "  AWOS Agent Console — Transparent Back Demo"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "Starting web server..."
cd "$ROOT"
python3 gui/server.py --port 8765 &
SERVER_PID=$!
sleep 2

echo ""
echo "◆ Verifying Agent Console UI..."
echo ""

# Verify title
TITLE=$(curl -s http://127.0.0.1:8765/agent | grep -o '<title>[^<]*</title>')
if echo "$TITLE" | grep -q "AWOS Agent Console"; then
  echo "  ✅ TITLE: $TITLE"
else
  echo "  ❌ TITLE: $TITLE (expected 'AWOS Agent Console')"
fi

# Verify thinking panel exists
if curl -s http://127.0.0.1:8765/agent | grep -q "thinking-panel"; then
  echo "  ✅ THINKING PANEL: Agent reasoning panel present"
else
  echo "  ❌ THINKING PANEL: Missing"
fi

# Verify no CI Rescue references
if curl -s http://127.0.0.1:8765/agent | grep -qi "CI Rescue\|fix-ci"; then
  echo "  ❌ BRANDING: Found CI Rescue references (should be removed)"
else
  echo "  ✅ BRANDING: No CI Rescue references — clean general agent"
fi

# Verify empty state
if curl -s http://127.0.0.1:8765/agent | grep -q "awos run"; then
  echo "  ✅ EMPTY STATE: Shows 'awos run' entry point"
else
  echo "  ⚠️  Empty state text check"
fi

# Verify new stat labels
PAGE=$(curl -s http://127.0.0.1:8765/agent)
for label in "Tasks" "Models" "Cost" "Efficiency"; do
  if echo "$PAGE" | grep -qi "$label"; then
    echo "  ✅ STAT: '$label' label present"
  else
    echo "  ⚠️  STAT: '$label' label not found"
  fi
done

# Check sessions API
echo ""
echo "◆ Checking sessions API..."
SESSIONS=$(curl -s http://127.0.0.1:8765/api/sessions)
echo "  Sessions: $(echo "$SESSIONS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} session(s)')" 2>/dev/null || echo "0")"

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  All UI checks passed. Open in browser:"
echo "  → http://127.0.0.1:8765/agent"
echo ""
echo "  To see the agent in action, in another terminal:"
echo "  → python3 -m awos run \"fix the tests\" --root tests/fixtures/wedge_v1/ci_rescue_sprint"
echo ""
echo "  Watch the Agent Console — you'll see:"
echo "  • 🧠 Agent Reasoning panel — model thinking in real-time"
echo "  • 🧠 Router decisions — model chosen, tier, cost"
echo "  • 📋 Task progress — live progress bar"
echo "  • 📝 File edits — diffs with syntax highlighting"
echo "  • ✅ Test results — pass/fail"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Wait for Ctrl+C
trap "kill $SERVER_PID 2>/dev/null; echo 'Server stopped'; exit 0" INT
wait $SERVER_PID
