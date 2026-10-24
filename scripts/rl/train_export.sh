#!/usr/bin/env bash
# LoRA adapter -> merged HF bf16 -> GGUF (llama.cpp b11146) -> Q4_K_M -> llama-server check.
# Runs on a CPU box: GitHub Actions (.github/workflows/rl-export.yml) or, last resort, the laptop.
#
#   BASE=<HF checkpoint dir, real files> ADAPTER=<dir with adapter_model.safetensors> OUT=<dir> \
#   NAME=qwen3.5-4b-rft-v1 bash scripts/rl/train_export.sh
#
# Env:
#   PY           python with llama.cpp's convert requirements (torch, safetensors, transformers 4.57.6, gguf)
#   LLAMA_SRC    llama.cpp source tree at tag b11146 (convert_hf_to_gguf.py + gguf-py)
#   QUANTIZE     llama-quantize (b11146)          SERVER  llama-server (b11146)
#   GGUF_SPLIT   llama-gguf-split (b11146); SPLIT_MB (default 1900): GitHub release assets must be < 2 GiB,
#                so a bigger GGUF (4B Q4_K_M = 2.7 GB) is also written as <NAME>-Q4_K_M-0000k-of-0000n.gguf
#                shards; llama-server -m <first shard> loads them all
#   IMATRIX      unsloth's imatrix for this size (optional; matches the evaluated unsloth GGUFs)
#   TRAJ         trajectories.jsonl for the serve check (verified steps; optional)
#   MERGE_IN_PLACE=1  merge into BASE itself (saves one copy of the weights on small disks)
#   KEEP=1       keep the merged HF dir and the bf16 GGUF
# Outputs in OUT: <NAME>-Q4_K_M.gguf (+ shards), SHA256SUMS, export.json, serve_check.json
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
: "${BASE:?}" "${OUT:?}" "${NAME:?}"
PY="${PY:-python3}"; QUANTIZE="${QUANTIZE:-llama-quantize}"; SERVER="${SERVER:-llama-server}"
GGUF_SPLIT="${GGUF_SPLIT:-$(dirname "$(command -v "$QUANTIZE" || echo .)")/llama-gguf-split}"; SPLIT_MB="${SPLIT_MB:-1900}"
: "${LLAMA_SRC:?set LLAMA_SRC=<llama.cpp b11146 source tree>}"
mkdir -p "$OUT"
ts() { date +%s; }
T0=$(ts)

if [ -n "${ADAPTER:-}" ]; then
  if [ "${MERGE_IN_PLACE:-0}" = 1 ]; then HF="$BASE"; else HF="$OUT/hf"; fi
  echo "=== merge $ADAPTER into $BASE -> $HF"
  "$PY" -m scaffold.rl.train.merge_lora --base "$BASE" --adapter "$ADAPTER" --out "$HF" | tee "$OUT/merge.json"
else
  echo "=== no ADAPTER: exporting the base unchanged (same-pipeline baseline)"
  HF="$BASE"
fi
T1=$(ts)

echo "=== convert_hf_to_gguf (llama.cpp $(basename "$LLAMA_SRC")) bf16, no MTP head (as unsloth's GGUFs)"
"$PY" "$LLAMA_SRC/convert_hf_to_gguf.py" "$HF" --outtype bf16 --no-mtp --outfile "$OUT/$NAME-bf16.gguf"
if [ "${KEEP:-0}" != 1 ] && [ "$HF" = "$OUT/hf" ]; then rm -rf "$HF"; fi
T2=$(ts)

echo "=== llama-quantize Q4_K_M"
QARGS=()
if [ -n "${IMATRIX:-}" ]; then QARGS=(--imatrix "$IMATRIX"); fi
"$QUANTIZE" "${QARGS[@]}" "$OUT/$NAME-bf16.gguf" "$OUT/$NAME-Q4_K_M.gguf" Q4_K_M
[ "${KEEP:-0}" = 1 ] || rm -f "$OUT/$NAME-bf16.gguf"
BYTES=$(wc -c < "$OUT/$NAME-Q4_K_M.gguf" | tr -d ' ')
SHARDS=1
if [ "$BYTES" -gt $((SPLIT_MB * 1000000)) ]; then
  echo "=== split into <${SPLIT_MB}M shards (release-asset limit)"
  "$GGUF_SPLIT" --split --split-max-size "${SPLIT_MB}M" "$OUT/$NAME-Q4_K_M.gguf" "$OUT/$NAME-Q4_K_M"
  SHARDS=$(ls "$OUT/$NAME-Q4_K_M"-0*-of-0*.gguf | wc -l | tr -d ' ')
fi
(cd "$OUT" && { sha256sum "$NAME"-Q4_K_M*.gguf 2>/dev/null || shasum -a 256 "$NAME"-Q4_K_M*.gguf; }) > "$OUT/SHA256SUMS"
cat "$OUT/SHA256SUMS"
T3=$(ts)

CHECK=skipped
if [ -n "${TRAJ:-}" ]; then
  echo "=== serve_check (llama-server loads it; template + tokens == training text; grammar reply parses)"
  "$PY" -m scaffold.rl.train.serve_check --gguf "$OUT/$NAME-Q4_K_M.gguf" --trajectories "$TRAJ" \
    --hf-tokenizer "$BASE" --server "$SERVER" --n "${CHECK_N:-3}" --ctx 8192 --timeout 600 \
    > "$OUT/serve_check.json" && CHECK=ok || CHECK=FAILED
  cat "$OUT/serve_check.json"
fi
T4=$(ts)
cat > "$OUT/export.json" <<JSON
{"name": "$NAME", "file": "$NAME-Q4_K_M.gguf", "bytes": $BYTES, "shards": $SHARDS,
 "sha256": "$(grep " $NAME-Q4_K_M.gguf\$" "$OUT/SHA256SUMS" | cut -d' ' -f1)",
 "imatrix": "${IMATRIX:-}", "llama_src": "$(basename "$LLAMA_SRC")", "serve_check": "$CHECK",
 "seconds": {"merge": $((T1 - T0)), "convert": $((T2 - T1)), "quantize": $((T3 - T2)), "serve_check": $((T4 - T3))}}
JSON
cat "$OUT/export.json"
[ "$CHECK" != FAILED ]
