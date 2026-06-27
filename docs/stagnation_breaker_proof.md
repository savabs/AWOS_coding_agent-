# Stagnation Breaker — Live Proof Guide

**Purpose:** See the breaker trip in real-time with clear, observable output.

---

## Quick start

```bash
./scripts/demo_stagnation_breaker.sh
```

This will:
1. Create a broken Python file (missing colon in function def)
2. Run AWOS mission with `AWOS_STAGNATION_THRESHOLD=2`
3. Worker fails repeatedly on same syntax error
4. After 2nd identical failure → breaker trips → session paused
5. Show session with `Status: paused (STAGNATION)`

---

## What to watch for

The output will show:

```
[TASK 1] Worker attempt 1/3
[TASK 1] VERIFICATION FAILED: invalid syntax (<string>, line 4)
                              ↑ first fail

[TASK 1] Worker attempt 2/3
[TASK 1] VERIFICATION FAILED: invalid syntax (<string>, line 4)
                              ↑ second fail (same error hash)

[BREAKER] Stagnation detected: same error pattern repeating
[BREAKER] Task 1 stuck — auto-pausing session
[BREAKER] Review: awos sessions show rs_<id>

[SESSION] Pause requested — stopping after last checkpoint

Session paused: rs_<id>
Status: paused (STAGNATION)  ← THIS is the proof!
```

---

## Manual run (if you want more control)

```bash
# Set low threshold for faster trip
export AWOS_STAGNATION_THRESHOLD=2
export AWOS_RUNTIME_SESSION=true

# Create broken file
cat > tests/fixtures/stagnation_target.py <<'EOF'
def broken_function()
    return "missing colon above"
EOF

# Run mission
python3 awos.py run \
    --goal "Fix syntax error in tests/fixtures/stagnation_target.py" \
    --root .

# Check session (it will be paused)
python3 awos.py sessions list
python3 awos.py sessions show rs_<latest_id>
```

Look for `Status: paused (STAGNATION)` in the session output.

---

## Why this proves it works

**Before stagnation breaker:** Worker would retry 3×, decompose task, retry sub-tasks, burn tokens until max_decomposition or budget exhausted.

**With stagnation breaker:** After 2nd identical error (same file, same error text) → immediate pause. No wasted retries, no token burn, clear reason for human review.

---

## Config used

| Var | Demo value | Production default |
|-----|------------|-------------------|
| `AWOS_STAGNATION_THRESHOLD` | `2` | `3` |
| `AWOS_STAGNATION_WINDOW` | `5` | `10` |

Lower threshold = faster trip for demo purposes.

---

## Next steps after proof

1. Resume with manual fix: `awos sessions resume rs_<id>`
2. Or skip stuck task: edit session JSON, remove task 1, resume
3. Or accept the pause as intended behavior (unattended safety)
