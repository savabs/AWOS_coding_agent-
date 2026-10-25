#!/bin/bash
# awos_model.sh — run the AWOS repair model on this Mac for the Chrome extension (AWOS Replay).
#
#     bash scripts/awos_model.sh            # G7: Qwen3.5 2B trained for repair + verify, round 2 (default)
#     bash scripts/awos_model.sh g6         # G6: round 1
#     bash scripts/awos_model.sh base2b     # untrained Qwen3.5 2B (for an A/B)
#     bash scripts/awos_model.sh --check    # is it up? (one tiny repair prompt)
#
# Downloads the GGUF once into ~/.awos/models (sha256-checked), then serves it with llama.cpp on
# 127.0.0.1:8080 ONLY (the extension refuses any non-loopback endpoint; nothing leaves the machine).
# Same server flags as CI (rl-repair-verify.yml): --jinja --reasoning off --temp 0, one slot.
# Needs: brew install llama.cpp (llama-server), and gh (logged in) for the G6 / G7 releases.
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

case "${1:-g7}" in
  --check) check; exit $? ;;
  g7) FILE=qwen3.5-2b-g7-rv-Q4_K_M.gguf; SHA=a63e356f91aa5e1f741fc5132301f88140445e50f4b3a45658f921813c957f4f
      TAG=rl-qwen3.5-2b-g7-rv ;;
  g6) FILE=qwen3.5-2b-g6-rv-Q4_K_M.gguf; SHA=af133cd576eecab134d955de70457aa40f91843d6bd0845d53bf1b2fa6534c62
      TAG=rl-qwen3.5-2b-g6-rv ;;
  base2b) FILE=Qwen3.5-2B-Q4_K_M.gguf; SHA=aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223
      TAG="" ;;
  *) echo "usage: $0 [g7|g6|base2b|--check]"; exit 2 ;;
esac
ok_file() { [ -f "$DIR/$FILE" ] && echo "$SHA  $DIR/$FILE" | shasum -a 256 -c - >/dev/null 2>&1; }
# download to <file>.part and rename only when complete + sha256-correct (a cut download never looks finished)
for attempt in 1 2 3; do
  ok_file && break
  rm -f "$DIR/$FILE" "$DIR/$FILE.part"
  echo "downloading $FILE (attempt $attempt)..."
  if [ -n "$TAG" ]; then
    rm -rf "$DIR/.dl" && mkdir -p "$DIR/.dl" && gh release download "$TAG" --repo "$REPO" --pattern "$FILE" --dir "$DIR/.dl" \
      && mv "$DIR/.dl/$FILE" "$DIR/$FILE.part"
  else
    curl -fL --retry 5 -o "$DIR/$FILE.part" \
      "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/$FILE"
  fi
  [ -f "$DIR/$FILE.part" ] && mv "$DIR/$FILE.part" "$DIR/$FILE"
done
rm -rf "$DIR/.dl"
ok_file || { rm -f "$DIR/$FILE"; echo "download failed or sha256 mismatch for $FILE: run the script again"; exit 1; }
command -v llama-server >/dev/null || { echo "llama-server not found: brew install llama.cpp"; exit 1; }
if curl -s -m 2 "http://127.0.0.1:$PORT/health" | grep -q ok; then
  echo "something is already serving on 127.0.0.1:$PORT (stop it first, or set AWOS_MODEL_PORT)"; exit 1
fi
echo "serving $FILE on http://127.0.0.1:$PORT (Ctrl-C to stop); extension Options -> endpoint http://127.0.0.1:$PORT/v1"
exec llama-server -m "$DIR/$FILE" --alias local --host 127.0.0.1 --port "$PORT" \
  -c 4096 -np 1 --jinja --reasoning off --cache-prompt --temp 0
