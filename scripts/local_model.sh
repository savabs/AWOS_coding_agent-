#!/usr/bin/env bash
# local_model.sh — the local model endpoint AWOS_PROVIDER=local talks to.
#
#   scripts/local_model.sh download   fetch the GGUF into ~/.cache/awos-models/ if missing
#   scripts/local_model.sh start      download if needed, then start llama-server (background)
#   scripts/local_model.sh status     is it up? pid, RSS, /health, model id
#   scripts/local_model.sh stop       stop the server this script started
#   scripts/local_model.sh cmd        print the exact llama-server command and exit
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
#   LLAMA_SERVER           binary             (default: llama-server on PATH)
set -euo pipefail

REPO_ID="${AWOS_LOCAL_GGUF_REPO:-unsloth/Qwen3.5-9B-GGUF}"
GGUF_FILE="${AWOS_LOCAL_GGUF_FILE:-Qwen3.5-9B-Q4_K_M.gguf}"
ALIAS="${AWOS_LOCAL_MODEL:-qwen3.5-9b}"
CTX="${AWOS_LOCAL_CONTEXT:-24576}"
PORT="${AWOS_LOCAL_PORT:-8080}"
SLOTS="${AWOS_LOCAL_SLOTS:-1}"
MODELS_DIR="${AWOS_MODELS_DIR:-$HOME/.cache/awos-models}"
SERVER_BIN="${LLAMA_SERVER:-$(command -v llama-server || echo /opt/homebrew/bin/llama-server)}"
HOST="127.0.0.1"   # never bind outside the machine
MODEL_PATH="$MODELS_DIR/$GGUF_FILE"
PID_FILE="$MODELS_DIR/llama-server.$PORT.pid"
LOG_FILE="$MODELS_DIR/llama-server.$PORT.log"

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
  echo "$SERVER_BIN -m $MODEL_PATH --alias $ALIAS --host $HOST --port $PORT" \
       "-ngl 99 -c $CTX -np $SLOTS -fa on --jinja --reasoning off" \
       "--cache-prompt --cache-reuse 256" \
       "--temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --metrics"
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
  local cmd
  cmd="$(server_cmd)"
  echo "+ $cmd"
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
  rm -f "$PID_FILE"
}

case "${1:-start}" in
  download) download ;;
  start) start ;;
  status) status ;;
  stop) stop ;;
  cmd) server_cmd ;;
  *) echo "usage: $0 {download|start|status|stop|cmd}" >&2; exit 2 ;;
esac
