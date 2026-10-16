#!/usr/bin/env bash
# local_model.sh — the local model endpoint AWOS_PROVIDER=local talks to.
#
#   scripts/local_model.sh download   fetch the GGUF into ~/.cache/awos-models/ if missing
#   scripts/local_model.sh start      download if needed, then start llama-server (background)
#   scripts/local_model.sh status     is it up? pid, RSS, /health, model id
#   scripts/local_model.sh stop       stop the server this script started
#   scripts/local_model.sh cmd        print the exact llama-server command and exit
#   scripts/local_model.sh plan       show the effective runtime plan (policy, spec, memory)
#   scripts/local_model.sh watch      stop the server once it has slept AWOS_LOCAL_EXIT_AFTER_SLEEP_S
#
# Defaults (docs/specs/local_provider_spec.md): Qwen3.5-9B Q4_K_M (unsloth),
# llama-server on 127.0.0.1:8080 only, all layers on Metal, 24k context, one
# slot, f16 KV (no KV quantization), thinking off, prompt cache on.
#
# Overrides (env):
#   AWOS_LOCAL_GGUF_REPO   HF repo            (default unsloth/Qwen3.5-9B-GGUF)
#   AWOS_LOCAL_GGUF_FILE   file in the repo   (default Qwen3.5-9B-Q4_K_M.gguf)
#   AWOS_LOCAL_MODEL       served model alias (default qwen3.5-9b)
#   AWOS_LOCAL_CONTEXT     context tokens     (default 24576; trick book: 16-24k)
#   AWOS_LOCAL_PORT        port               (default 8080)
#   AWOS_LOCAL_SLOTS       parallel slots     (default 1)
#   AWOS_MODELS_DIR        download dir       (default ~/.cache/awos-models)
#   AWOS_LOCAL_EXTRA_ARGS  extra llama-server flags appended verbatim (e.g. --kv-unified)
#   LLAMA_SERVER           binary             (default: llama-server on PATH)
#
# Runtime policy (scaffold/agent/local_runtime.py; REA roadmap B3-B5):
#   AWOS_LOCAL_SPEC        off|ngram|draft    speculative decoding (default off)
#                          ngram: prompt-lookup drafting, no extra memory (works on Qwen3.5)
#                          draft: needs AWOS_LOCAL_DRAFT_GGUF with the SAME tokenizer;
#                          refused on mismatch, swapped for ngram on battery
#   AWOS_LOCAL_SPEC_DRAFT_MAX  max drafted tokens per step, 1..64 (default 16)
#   AWOS_LOCAL_IDLE_S      unload the model after N s without requests (default 300,
#                          120 on battery; 0 = never); the next request reloads it
#   AWOS_LOCAL_EXIT_AFTER_SLEEP_S  `watch`: exit the process after N s asleep (default 300)
#   AWOS_LOCAL_SLOT_SAVE   1|0 slot save/restore dir for prefix KV (default 1)
#   AWOS_LOCAL_SLOT_DIR    slot files          (default $AWOS_MODELS_DIR/slots)
#   AWOS_LOCAL_CACHE_RAM_MB host prompt cache  (default 2048; llama-server's own is 8192)
#   AWOS_LOCAL_BATTERY_POLICY restrictive|ignore (default restrictive: no draft model,
#                          shorter idle; context capped at 16384 below 20 % battery)
#   AWOS_LOCAL_BATTERY_CTX_MAX  also cap the context on battery (default: no cap above 20 %)
#   AWOS_LOCAL_OS_RESERVE_MB    RAM kept for the OS in memory admission (default max(4096, RAM/4))
#   AWOS_LOCAL_PLAN=0      skip policy + admission (raw env only)
set -euo pipefail

REPO_ID="${AWOS_LOCAL_GGUF_REPO:-unsloth/Qwen3.5-9B-GGUF}"
GGUF_FILE="${AWOS_LOCAL_GGUF_FILE:-Qwen3.5-9B-Q4_K_M.gguf}"
ALIAS="${AWOS_LOCAL_MODEL:-qwen3.5-9b}"
CTX="${AWOS_LOCAL_CONTEXT:-24576}"
PORT="${AWOS_LOCAL_PORT:-8080}"
SLOTS="${AWOS_LOCAL_SLOTS:-1}"
MODELS_DIR="${AWOS_MODELS_DIR:-$HOME/.cache/awos-models}"
EXTRA_ARGS="${AWOS_LOCAL_EXTRA_ARGS:-}"
SERVER_BIN="${LLAMA_SERVER:-$(command -v llama-server || echo /opt/homebrew/bin/llama-server)}"
HOST="127.0.0.1"   # never bind outside the machine
MODEL_PATH="$MODELS_DIR/$GGUF_FILE"
PID_FILE="$MODELS_DIR/llama-server.$PORT.pid"
LOG_FILE="$MODELS_DIR/llama-server.$PORT.log"
CMD_FILE="$MODELS_DIR/llama-server.$PORT.cmd"
SLOT_DIR="${AWOS_LOCAL_SLOT_DIR:-$MODELS_DIR/slots}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_PY="$ROOT/scaffold/agent/local_runtime.py"

runtime_py() {
  AWOS_MODELS_DIR="$MODELS_DIR" AWOS_LOCAL_GGUF_FILE="$GGUF_FILE" AWOS_LOCAL_CONTEXT="$CTX" \
    AWOS_LOCAL_SLOTS="$SLOTS" AWOS_LOCAL_PORT="$PORT" AWOS_LOCAL_SLOT_DIR="$SLOT_DIR" \
    python3 "$RUNTIME_PY" "$@"
}

# Policy + memory admission -> AWOS_PLAN_* (ctx, spec flags, idle seconds, host cache).
load_plan() {
  if [[ "${AWOS_LOCAL_PLAN:-1}" == "0" ]]; then
    AWOS_PLAN_CTX="$CTX"; AWOS_PLAN_SPEC="off"; AWOS_PLAN_SPEC_ARGS=""
    AWOS_PLAN_IDLE_S="${AWOS_LOCAL_IDLE_S:-300}"
    AWOS_PLAN_CACHE_RAM_MB="${AWOS_LOCAL_CACHE_RAM_MB:-2048}"
    AWOS_PLAN_POLICY="none"; AWOS_PLAN_NOTES=""
    return 0
  fi
  local out
  if ! out="$(runtime_py plan --shell)"; then
    exit 3   # the reason is already on stderr
  fi
  eval "$out"
  if [[ "$AWOS_PLAN_SPEC" != "off" ]] && ! "$SERVER_BIN" --help 2>&1 | grep -q -- "--spec-type"; then
    echo "AWOS_LOCAL_SPEC=$AWOS_PLAN_SPEC: this llama-server build has no --spec-type (upgrade llama.cpp)" >&2
    exit 3
  fi
}

server_cmd() {
  # -ngl 99          every layer on the GPU (Metal)
  # -c / -np         one slot owns the whole context
  # -fa on           flash attention
  # --jinja          the model's own chat template (tool calls)
  # --reasoning off  Qwen3.5 thinks by default; off is the AWOS default (A/B it)
  # --cache-reuse    reuse the cached prefix across turns (cache-prompt is on by default)
  # sampling         Qwen3.5 card, non-thinking: temp 0.7 top-p 0.8 top-k 20 min-p 0
  #                  (a request that sends temperature overrides it)
  # KV cache stays f16: 4-bit KV cost -58.9% at 7B (trick book §2.2).
  # (llama-server ignores --cache-reuse for hybrid models such as Qwen3.5.)
  # --sleep-idle-seconds  unload after idle; the next request reloads (B5)
  # --slot-save-path      static-prefix KV save/restore across tasks (B4)
  # --cache-ram           bound the host-memory prompt cache (counted in admission)
  local runtime="--cache-ram $AWOS_PLAN_CACHE_RAM_MB"
  if [[ "${AWOS_PLAN_IDLE_S:-0}" -gt 0 ]]; then
    runtime="$runtime --sleep-idle-seconds $AWOS_PLAN_IDLE_S"
  fi
  if [[ "${AWOS_LOCAL_SLOT_SAVE:-1}" != "0" ]]; then
    runtime="$runtime --slot-save-path $SLOT_DIR"
  fi
  echo "$SERVER_BIN -m $MODEL_PATH --alias $ALIAS --host $HOST --port $PORT" \
       "-ngl 99 -c $AWOS_PLAN_CTX -np $SLOTS -fa on --jinja --reasoning off" \
       "--cache-prompt --cache-reuse 256 $runtime" \
       "--temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --metrics" $AWOS_PLAN_SPEC_ARGS $EXTRA_ARGS
}

download() {
  mkdir -p "$MODELS_DIR"
  if [[ -s "$MODEL_PATH" ]]; then
    echo "model present: $MODEL_PATH ($(du -h "$MODEL_PATH" | cut -f1))"
    return 0
  fi
  local hf_cli=""
  for c in hf huggingface-cli; do
    if command -v "$c" >/dev/null 2>&1; then hf_cli="$c"; break; fi
  done
  if [[ -n "$hf_cli" ]]; then
    echo "+ $hf_cli download $REPO_ID $GGUF_FILE --local-dir $MODELS_DIR"
    "$hf_cli" download "$REPO_ID" "$GGUF_FILE" --local-dir "$MODELS_DIR"
  else
    local url="https://huggingface.co/$REPO_ID/resolve/main/$GGUF_FILE"
    echo "+ curl -L -C - -o $MODEL_PATH.part $url"
    curl -fL --retry 3 -C - -o "$MODEL_PATH.part" "$url"
    mv "$MODEL_PATH.part" "$MODEL_PATH"
  fi
  echo "downloaded: $MODEL_PATH ($(du -h "$MODEL_PATH" | cut -f1))"
}

running_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then echo "$pid"; return 0; fi
  return 1
}

healthy() {
  curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1
}

start() {
  if pid="$(running_pid)"; then
    echo "already running (pid $pid) on http://$HOST:$PORT/v1"
    return 0
  fi
  [[ -x "$SERVER_BIN" ]] || { echo "llama-server not found (brew install llama.cpp)" >&2; exit 1; }
  download
  load_plan
  mkdir -p "$SLOT_DIR"
  local cmd
  cmd="$(server_cmd)"
  echo "plan: policy=$AWOS_PLAN_POLICY ctx=$AWOS_PLAN_CTX spec=$AWOS_PLAN_SPEC" \
       "idle=${AWOS_PLAN_IDLE_S}s mem=${AWOS_PLAN_NEED_MB:-?}/${AWOS_PLAN_BUDGET_MB:-?} MB" \
       "${AWOS_PLAN_NOTES:+($AWOS_PLAN_NOTES)}"
  echo "+ $cmd"
  echo "$cmd" >"$CMD_FILE"
  # shellcheck disable=SC2086
  nohup $cmd >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "pid $(cat "$PID_FILE"), log $LOG_FILE; waiting for /health ..."
  for _ in $(seq 1 180); do
    if healthy; then
      echo "ready: http://$HOST:$PORT/v1  model=$ALIAS"
      echo "use:   AWOS_PROVIDER=local AWOS_LOCAL_MODEL=$ALIAS"
      return 0
    fi
    if ! running_pid >/dev/null; then
      echo "llama-server exited; last log lines:" >&2
      tail -20 "$LOG_FILE" >&2
      rm -f "$PID_FILE"
      exit 1
    fi
    sleep 1
  done
  echo "not healthy after 180 s; see $LOG_FILE" >&2
  exit 1
}

status() {
  if pid="$(running_pid)"; then
    local rss_kb
    rss_kb="$(ps -o rss= -p "$pid" | tr -d ' ')"
    echo "running: pid $pid, RSS $((rss_kb / 1024)) MB, http://$HOST:$PORT/v1"
    if healthy; then
      echo "health:  ok"
      curl -fsS "http://$HOST:$PORT/v1/models" 2>/dev/null \
        | python3 -c 'import sys,json; print("models: ", [m["id"] for m in json.load(sys.stdin)["data"]])' \
        || true
    else
      echo "health:  not ready"
    fi
    runtime_py status 2>/dev/null || true
  else
    echo "stopped"
    return 1
  fi
}

stop() {
  if pid="$(running_pid)"; then
    kill "$pid"
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    echo "stopped pid $pid"
  else
    echo "not running"
  fi
  rm -f "$PID_FILE" "$CMD_FILE"
}

case "${1:-start}" in
  download) download ;;
  start) start ;;
  status) status ;;
  stop) stop ;;
  cmd) load_plan; server_cmd ;;
  plan) runtime_py plan ;;
  watch) runtime_py watch ;;
  *) echo "usage: $0 {download|start|status|stop|cmd|plan|watch}" >&2; exit 2 ;;
esac
