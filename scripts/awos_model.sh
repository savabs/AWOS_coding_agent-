#!/bin/bash
# awos_model.sh — run the AWOS repair model on this Mac for the Chrome extension (AWOS Replay).
#
#     bash scripts/awos_model.sh            # G6: Qwen3.5 2B trained for repair + verify (default)
#     bash scripts/awos_model.sh base2b     # untrained Qwen3.5 2B (for an A/B)
#     bash scripts/awos_model.sh --check    # is it up? (one tiny repair prompt)
#
# Downloads the GGUF once into ~/.awos/models (sha256-checked), then serves it with llama.cpp on
# 127.0.0.1:8080 ONLY (the extension refuses any non-loopback endpoint; nothing leaves the machine).
# Same server flags as CI (rl-repair-verify.yml): --jinja --reasoning off --temp 0, one slot.
# Needs: brew install llama.cpp (llama-server), and gh (logged in) for the G6 release.
set -euo pipefail
REPO=savabs/AWOS_coding_agent-
DIR="$HOME/.awos/models"
PORT="${AWOS_MODEL_PORT:-8080}"
mkdir -p "$DIR"

check() {
  curl -s -m 5 "http://127.0.0.1:$PORT/health" | grep -q ok || { echo "model server: DOWN on 127.0.0.1:$PORT"; return 1; }
  curl -s -m 60 "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' -d '{
    "model": "local", "temperature": 0, "max_tokens": 64,
    "grammar": "root ::= (\"PICK \" [1-5] | \"NOT_FOUND\") \": \" [^\\n]{0,160}",
    "messages": [{"role": "user", "content": "The step used: button named '"'"'Save'"'"'\n\nCandidates on the page now:\n1. button named '"'"'Cancel'"'"'\n\nWhich candidate is the same control? '"'"'PICK <n>: reason'"'"' or '"'"'NOT_FOUND: reason'"'"'."}]
  }' | python3 -c "import json,sys; print('model server: UP; sample answer:', json.load(sys.stdin)['choices'][0]['message']['content'])"
}

case "${1:-g6}" in
  --check) check; exit $? ;;
  g6)
    FILE=qwen3.5-2b-g6-rv-Q4_K_M.gguf
    SHA=af133cd576eecab134d955de70457aa40f91843d6bd0845d53bf1b2fa6534c62
    [ -f "$DIR/$FILE" ] || gh release download rl-qwen3.5-2b-g6-rv --repo "$REPO" --pattern "$FILE" --dir "$DIR" ;;
  base2b)
    FILE=Qwen3.5-2B-Q4_K_M.gguf
    SHA=aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223
    [ -f "$DIR/$FILE" ] || curl -fL --retry 5 -o "$DIR/$FILE" \
      "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/$FILE" ;;
  *) echo "usage: $0 [g6|base2b|--check]"; exit 2 ;;
esac
echo "$SHA  $DIR/$FILE" | shasum -a 256 -c - >/dev/null || { echo "sha256 mismatch for $DIR/$FILE: delete it and retry"; exit 1; }
command -v llama-server >/dev/null || { echo "llama-server not found: brew install llama.cpp"; exit 1; }
if curl -s -m 2 "http://127.0.0.1:$PORT/health" | grep -q ok; then
  echo "something is already serving on 127.0.0.1:$PORT (stop it first, or set AWOS_MODEL_PORT)"; exit 1
fi
echo "serving $FILE on http://127.0.0.1:$PORT (Ctrl-C to stop); extension Options -> endpoint http://127.0.0.1:$PORT/v1"
exec llama-server -m "$DIR/$FILE" --alias local --host 127.0.0.1 --port "$PORT" \
  -c 4096 -np 1 --jinja --reasoning off --cache-prompt --temp 0
