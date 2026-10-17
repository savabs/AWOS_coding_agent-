#!/usr/bin/env bash
# Live proof for M1 host queue (docs/specs/host_queue.md):
# 3 jobs, `awos serve --worker` in the background, SIGKILL mid-job, restart,
# all 3 reach done. Model-free (sleep runner); uses a throwaway AWOS_HOST_DIR.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
export AWOS_HOST_DIR="$(mktemp -d)/host"
export AWOS_HOST_FAKE_STEPS=3 AWOS_HOST_FAKE_STEP_SEC=1
WS="$(mktemp -d)"
awos() { "$PY" "$REPO/awos.py" "$@" 2>&1 | grep -v '^\[DEBUGGER\]'; }

echo "== AWOS_HOST_DIR=$AWOS_HOST_DIR"
for g in "job A: add a docstring" "job B: fix the off-by-one" "job C: rename the helper"; do
  awos submit "$g" --repo "$WS" --budget 0.10 --privacy local_only
done
echo "== status BEFORE"; awos status

"$PY" "$REPO/awos.py" serve --worker --runner sleep --poll 0.2 > "$AWOS_HOST_DIR/serve1.log" 2>&1 &
PID=$!
sleep 5.5   # job A done (~3s), job B mid-step
echo "== status MID-JOB (worker pid $PID)"; awos status
kill -9 "$PID"; wait "$PID" 2>/dev/null || true
echo "== SIGKILLed worker $PID; status AFTER KILL"; awos status

"$PY" "$REPO/awos.py" serve --worker --once --runner sleep --poll 0.2 > "$AWOS_HOST_DIR/serve2.log" 2>&1
echo "== restart log"; grep -v '^\[DEBUGGER\]' "$AWOS_HOST_DIR/serve2.log"
echo "== status AFTER RESTART"; awos status
n=$(sqlite3 "$AWOS_HOST_DIR/queue.sqlite" "select count(*) from jobs where state='done'")
echo "== done jobs: $n/3"
if [ "$n" = 3 ]; then echo "LIVE PROOF: PASS"; else echo "LIVE PROOF: FAIL"; exit 1; fi
