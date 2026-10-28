#!/usr/bin/env bash
#
# run_benchmark.sh — Set up and run the executor benchmark on your own machine.
#
# Checks the things that otherwise fail halfway through, then records the
# comparison. Nothing here is destructive and nothing is installed without
# asking; a failed check explains what to do and stops.
#
#   ./scripts/run_benchmark.sh
#   ./scripts/run_benchmark.sh --agent-only     # skip the single-shot arm
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ARMS="both"
MAX_COST="0.15"
CASSETTE_DIR=".awos/cassettes"

while [ $# -gt 0 ]; do
  case "$1" in
    --agent-only) ARMS="agent"; shift ;;
    --max-cost)   MAX_COST="$2"; shift 2 ;;
    --cassette-dir) CASSETTE_DIR="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

step()  { printf '\n\033[1m── %s\033[0m\n' "$1"; }
ok()    { printf '   ok    %s\n' "$1"; }
fail()  { printf '\n   FAILED  %s\n\n' "$1"; exit 1; }

# ── 1. Python ─────────────────────────────────────────────────────────────────
step "Python"
command -v python3 >/dev/null || fail "python3 is not on PATH."
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')
[ "$PY_OK" = "1" ] || fail "Python 3.10+ is required; found $(python3 -V)."
ok "$(python3 -V)"

# ── 2. Dependencies ───────────────────────────────────────────────────────────
step "Dependencies"
MISSING=$(python3 - <<'PY'
needed = {"openai": "openai", "anthropic": "anthropic", "pytest": "pytest",
          "dotenv": "python-dotenv", "numpy": "numpy"}
missing = []
for module, package in needed.items():
    try:
        __import__(module)
    except ImportError:
        missing.append(package)
print(" ".join(missing))
PY
)
if [ -n "$MISSING" ]; then
  printf '   missing: %s\n\n' "$MISSING"
  printf '   Install them with:\n     pip install -r requirements.txt\n'
  fail "dependencies are not installed."
fi
ok "all present"

# ── 3. Credentials ────────────────────────────────────────────────────────────
step "Backend"
if [ -f .env ]; then
  ok ".env found (it is gitignored — your key will not be committed)"
else
  printf '   no .env file. Create one with your key, for example:\n\n'
  printf '     printf "OPENROUTER_API_KEY=sk-or-...\\nAWOS_AGENT_MODEL=anthropic/claude-haiku-4.5\\n" >> .env\n'
  fail "no .env file."
fi

# One tiny call: reachable, authenticated, and actually calls tools.
python3 scripts/check_backend.py || fail "the backend is not usable yet — see above."

# ── 4. Cases ──────────────────────────────────────────────────────────────────
step "Benchmark cases"
python3 scripts/validate_bug_cases.py >/dev/null 2>&1 \
  || fail "some cases are invalid. Run: python3 scripts/validate_bug_cases.py"
ok "all cases valid (fail on buggy.py, pass on fix.py)"

# ── 5. Run ────────────────────────────────────────────────────────────────────
step "Recording the comparison"
printf '   arms=%s  max-cost=$%s per case  cassettes=%s\n' "$ARMS" "$MAX_COST" "$CASSETTE_DIR"
printf '   A cost preflight follows; nothing is spent until you confirm it.\n\n'

python3 scripts/bench_executors.py \
  --arms "$ARMS" \
  --record \
  --cassette-dir "$CASSETTE_DIR" \
  --max-cost "$MAX_COST"
STATUS=$?

if [ $STATUS -ne 0 ]; then
  printf '\n   The run did not complete. Partial cassettes (if any) are in %s\n\n' "$CASSETTE_DIR"
  exit $STATUS
fi

# ── 6. What to do with the result ─────────────────────────────────────────────
step "Done"
cat <<EOF
   Results:   .awos/bench_executors_*.json
   Cassettes: $CASSETTE_DIR

   The cassettes hold the model's replies and token counts — no credentials
   (there is a test asserting that). Commit and push them, and the whole
   comparison can be replayed for free, by anyone, with no API key:

     git add $CASSETTE_DIR .awos/bench_executors_*.json
     git commit -m "bench: record executor comparison"
     git push

   Replay it any time, offline:

     python3 scripts/bench_executors.py --cassette-dir $CASSETTE_DIR

EOF
