#!/usr/bin/env bash
# Live proof: stagnation breaker in action

set -e

echo "════════════════════════════════════════════════════════════════"
echo "STAGNATION BREAKER — Live Proof"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "This demo:"
echo "  1. Creates a file with intentional syntax error (missing colon)"
echo "  2. Runs AWOS mission with stagnation threshold = 2 (faster demo)"
echo "  3. Worker will fail repeatedly on same syntax error"
echo "  4. After 2nd identical failure → breaker trips → session paused"
echo ""
echo "Watch for these markers:"
echo "  [TASK 1] VERIFICATION FAILED  ← first fail"
echo "  [TASK 1] VERIFICATION FAILED  ← second fail (same error)"
echo "  [BREAKER] Stagnation detected ← TRIP!"
echo "  Status: paused (STAGNATION)   ← reason displayed"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo ""

# Set low threshold for faster demo
export AWOS_STAGNATION_THRESHOLD=2
export AWOS_STAGNATION_WINDOW=5
export AWOS_RUNTIME_SESSION=true

# Clean up any previous proof files
rm -f tests/fixtures/stagnation_target.py

# Create the broken file
cat > tests/fixtures/stagnation_target.py <<'EOF'
"""Intentionally broken file to trigger stagnation breaker."""


def broken_function()
    """This function is missing a colon - worker will struggle to fix it."""
    return "hello"


def working_function():
    """This one is fine."""
    return 42
EOF

echo "Created broken file: tests/fixtures/stagnation_target.py"
echo ""

# Load mission config
MISSION_FILE="docs/missions/stagnation_proof.json"

if [ ! -f "$MISSION_FILE" ]; then
    echo "Error: $MISSION_FILE not found"
    exit 1
fi

echo "Starting mission (threshold=2, watch for breaker output)..."
echo "════════════════════════════════════════════════════════════════"
echo ""

# Run mission - will pause when stagnation detected
python3 awos.py run \
    --goal "$(jq -r .goal "$MISSION_FILE")" \
    --root . \
    || true  # Don't fail script if mission pauses

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "PROOF COMPLETE"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Find the latest session
LATEST_SESSION=$(ls -t .awos/sessions/rs_*.json 2>/dev/null | head -n1)

if [ -n "$LATEST_SESSION" ]; then
    SESSION_ID=$(basename "$LATEST_SESSION" .json)
    echo "Session paused: $SESSION_ID"
    echo ""
    echo "Check session details:"
    echo "  python3 awos.py sessions show $SESSION_ID"
    echo ""
    echo "If you see 'Status: paused (STAGNATION)' → breaker worked! ✓"
else
    echo "No session found (check if mission completed or failed before tripping)"
fi

echo ""
