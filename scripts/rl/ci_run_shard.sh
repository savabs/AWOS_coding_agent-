#!/usr/bin/env bash
# Run one rl-eval shard: scaffold.rl.harness over (model, source, split, shard) against the
# llama-server already listening on 127.0.0.1:8080. Called by .github/workflows/rl-eval.yml;
# also usable on the laptop with the same env vars.
#
# Required env: MODEL SOURCE SPLIT SHARD SHARDS        Optional: N NP AWOS_LOCAL_TIMEOUT_S
#               GGUF_REPO GGUF_FILE GGUF_REV (recorded in run_meta.json only)
#
# THE ONLY PLACE that knows the harness CLI. Assumed interface (reconcile here if the
# harness differs):
#   python -m scaffold.rl.harness run --source S --model local --split SPLIT
#          [--n N] [--shard K/N] [--parallel P] --out runs/rl/<name>/
#   python -m scaffold.rl.harness summarize runs/rl/<name>/
# Model endpoint/label via env: AWOS_LOCAL_BASE_URL, AWOS_LOCAL_MODEL (LocalGBNFModel).
# --shard / --parallel are passed only if `run --help` mentions them; without --shard,
# shard 1 runs the whole split and the other shards exit 0 (no duplicate work).
set -uo pipefail

: "${MODEL:?}" "${SOURCE:?}" "${SPLIT:?}" "${SHARD:?}" "${SHARDS:?}"
N="${N:-}"
NP="${NP:-1}"
NAME="${MODEL}__${SOURCE}__${SPLIT}__shard${SHARD}of${SHARDS}"
OUT="runs/rl/${NAME}"
mkdir -p "$OUT"

export AWOS_LOCAL_BASE_URL="${AWOS_LOCAL_BASE_URL:-http://127.0.0.1:8080/v1}"
export AWOS_LOCAL_MODEL="$MODEL"
export AWOS_LOCAL_TIMEOUT_S="${AWOS_LOCAL_TIMEOUT_S:-600}"
export PYTHONPATH="${PYTHONPATH:-$PWD}"

HELP="$(python -m scaffold.rl.harness run --help 2>&1 || true)"
ARGS=(run --source "$SOURCE" --model local --split "$SPLIT" --out "$OUT/")
[ -n "$N" ] && ARGS+=(--n "$N")
if grep -q -- "--shard" <<<"$HELP"; then
  ARGS+=(--shard "${SHARD}/${SHARDS}")
elif [ "$SHARD" != "1" ]; then
  echo "harness has no --shard: shard 1 runs the whole split; shard $SHARD skips"
  echo '{"skipped": "harness has no --shard"}' > "$OUT/run_meta.json"
  exit 0
fi
ARGS+=(--workers "$NP")

python3 - "$OUT/run_meta.json" <<PY
import json, os, sys, time
meta = {k.lower(): os.environ.get(k, "") for k in
        ("MODEL", "SOURCE", "SPLIT", "SHARD", "SHARDS", "N", "NP", "GGUF_REPO", "GGUF_FILE", "GGUF_REV",
         "LLAMA_TAG", "GITHUB_RUN_ID", "GITHUB_SHA")}
meta["started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
meta["harness_args"] = "${ARGS[*]}"
json.dump(meta, open(sys.argv[1], "w"), indent=1)
PY

echo "+ python -m scaffold.rl.harness ${ARGS[*]}"
python -m scaffold.rl.harness "${ARGS[@]}" 2>&1 | tee "$OUT/harness.log"
RC=${PIPESTATUS[0]}
python -m scaffold.rl.harness summarize "$OUT/" 2>&1 | tee "$OUT/summary.txt" || true
echo "harness exit $RC"
exit "$RC"
