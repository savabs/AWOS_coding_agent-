#!/bin/bash
# install_replay.sh — one-command Mac setup for AWOS Replay's local model (no sudo, idempotent).
#
#     curl -fsSL https://raw.githubusercontent.com/savabs/AWOS_coding_agent-/main/scripts/install_replay.sh | bash
#     bash scripts/install_replay.sh               # install / repair (safe to run again)
#     bash scripts/install_replay.sh --status      # what is installed, is the server up, how much memory
#     bash scripts/install_replay.sh --uninstall   # remove exactly what install added (--keep-model keeps the GGUF)
#
# What install does (and nothing else):
#   1. checks macOS on Apple Silicon, >= 2 GB free disk
#   2. llama.cpp via Homebrew if llama-server is missing (asks first if Homebrew itself is missing; never
#      installs Homebrew silently, never uses sudo itself)
#   3. downloads the repair model (same file + sha256 as scripts/awos_model.sh) into ~/.awos/models,
#      via <file>.part + retries, renamed only when the sha256 matches
#   4. a launchd *user* agent ~/Library/LaunchAgents/ai.awos.replay-model.plist that keeps llama-server on
#      127.0.0.1:8080 ONLY, with the EngineBench flags (--cache-ram 0 -cms 256), ProcessType Background +
#      nice 10 + low-priority IO, and --sleep-idle-seconds 300 (model unloaded after 5 idle minutes:
#      ~58 MB resident asleep vs ~820 MB loaded; first request after sleep takes ~5 s to reload)
#   5. a record of what it added in ~/.awos/replay/installed, which --uninstall reads
# It never touches browser profiles, other apps, or anything outside $HOME except `brew install llama.cpp`.
#
# Env: AWOS_DRY_RUN=1 (print every action, change nothing), AWOS_MODEL_PORT (8080), AWOS_REPLAY_MODEL
# (g7|g6|base2b, default g7), AWOS_SLEEP_IDLE (seconds, 300; -1 = never unload), AWOS_NONINTERACTIVE=1
# (answer "no" to every question), AWOS_MODEL_URL + AWOS_MODEL_SHA256 (mirror / tests).
set -euo pipefail

REPO=savabs/AWOS_coding_agent-
LABEL=ai.awos.replay-model
MODELS="$HOME/.awos/models"
STATE="$HOME/.awos/replay"
RECORD="$STATE/installed"
LOG="$STATE/model-server.log"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/$LABEL.plist"
PORT="${AWOS_MODEL_PORT:-8080}"
SLEEP_IDLE="${AWOS_SLEEP_IDLE:-300}"
DRY="${AWOS_DRY_RUN:-0}"
case "$DRY" in 0|"") DRY=0 ;; *) DRY=1 ;; esac

say()  { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
# every state-changing command goes through run(): AWOS_DRY_RUN=1 prints it instead
run()  { if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else "$@"; fi; }

pick_model() {   # same files + sha256 as scripts/awos_model.sh (tests/test_install_replay.py keeps them in sync)
  case "${AWOS_REPLAY_MODEL:-g7}" in
    g7) FILE=qwen3.5-2b-g7-rv-Q4_K_M.gguf; SHA=a63e356f91aa5e1f741fc5132301f88140445e50f4b3a45658f921813c957f4f
        URL="https://github.com/$REPO/releases/download/rl-qwen3.5-2b-g7-rv/$FILE" ;;
    g6) FILE=qwen3.5-2b-g6-rv-Q4_K_M.gguf; SHA=af133cd576eecab134d955de70457aa40f91843d6bd0845d53bf1b2fa6534c62
        URL="https://github.com/$REPO/releases/download/rl-qwen3.5-2b-g6-rv/$FILE" ;;
    base2b) FILE=Qwen3.5-2B-Q4_K_M.gguf; SHA=aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223
        URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/$FILE" ;;
    *) die "AWOS_REPLAY_MODEL must be g7, g6 or base2b" ;;
  esac
  URL="${AWOS_MODEL_URL:-$URL}"
  SHA="${AWOS_MODEL_SHA256:-$SHA}"
  MODEL="$MODELS/$FILE"
}

sha256_of() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }
ok_file() { [ -f "$1" ] && [ "$(sha256_of "$1")" = "$SHA" ]; }
healthy() { curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q ok; }
agent_loaded() { launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; }

# ---- install record: one "<kind> <path-or-name>" per line, appended once ----
record() {
  if [ "$DRY" = 1 ]; then say "[dry-run] record: $*"; return; fi
  mkdir -p "$STATE"
  grep -qxF "$*" "$RECORD" 2>/dev/null || printf '%s\n' "$*" >> "$RECORD"
}
mkdir_rec() {   # create a directory (and missing parents), recording each one we created
  local d="$1" missing="" p
  while [ ! -d "$d" ]; do missing="$d $missing"; d=$(dirname "$d"); done
  [ -z "$missing" ] && return 0
  run mkdir -p "$1"
  for p in $missing; do record "dir $p"; done
}

ask_yes() {   # ask_yes "question" -> 0 only on an explicit y/yes typed on the terminal
  local ans=""
  [ "${AWOS_NONINTERACTIVE:-0}" = 1 ] && return 1
  { [ -r /dev/tty ] && printf '%s [y/N] ' "$1" > /dev/tty && read -r ans < /dev/tty; } 2>/dev/null || return 1
  case "$ans" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

check_mac() {
  [ "$(uname -s)" = Darwin ] || die "AWOS Replay's local model installer is for macOS (found $(uname -s))."
  if [ "$(uname -m)" != arm64 ]; then
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then
      die "this shell runs under Rosetta (x86_64): open a native Terminal and run again"
    fi
    die "needs an Apple Silicon Mac (M1 or newer); found $(uname -m). Use Claude with your own key instead (extension Options)."
  fi
  local mem_gb ver free_kb
  mem_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
  ver=$(sw_vers -productVersion 2>/dev/null || echo unknown)
  say "macOS $ver, Apple Silicon, ${mem_gb} GB RAM"
  [ "$mem_gb" -ge 8 ] || warn "less than 8 GB RAM: the model needs ~0.9 GB while loaded"
  free_kb=$(df -Pk "$HOME" | awk 'NR==2 {print $4}')
  [ "${free_kb:-0}" -ge 2097152 ] || die "need ~2 GB free disk in $HOME for the model (1.3 GB)"
}

find_brew() {
  command -v brew 2>/dev/null && return
  local b
  for b in ${AWOS_BREW_CANDIDATES-/opt/homebrew/bin/brew /usr/local/bin/brew}; do [ -x "$b" ] && { echo "$b"; return; }; done
  return 1
}

ensure_llama() {
  LLAMA=$(command -v llama-server 2>/dev/null || true)
  if [ -n "$LLAMA" ]; then say "llama.cpp: $LLAMA (already installed)"; return; fi
  local brew
  if ! brew=$(find_brew); then
    say "llama.cpp is not installed, and it comes from Homebrew, which is not installed either."
    say "Homebrew's own installer (https://brew.sh) may ask for your Mac password; this script never does."
    if [ "$DRY" = 1 ]; then
      say "[dry-run] would ask: Install Homebrew now?"
      brew=/opt/homebrew/bin/brew
    elif ask_yes "Install Homebrew now?"; then
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty
      brew=$(find_brew) || die "Homebrew install did not finish; run this script again afterwards"
      record "homebrew $brew"
    else
      die "install Homebrew from https://brew.sh (or run: brew install llama.cpp), then run this again"
    fi
  fi
  say "installing llama.cpp with Homebrew (one-time, ~30 MB)..."
  run "$brew" install llama.cpp < /dev/null   # never let a child read the rest of a piped script
  record "brew llama.cpp"
  LLAMA="$(dirname "$brew")/llama-server"
  [ "$DRY" = 1 ] || [ -x "$LLAMA" ] || die "brew installed llama.cpp but $LLAMA is missing"
}

download_model() {
  mkdir_rec "$MODELS"
  if ok_file "$MODEL"; then say "model: $MODEL (already downloaded, sha256 ok)"; return; fi
  if [ "$DRY" = 1 ]; then
    say "[dry-run] download $URL -> $MODEL.part, check sha256 $SHA, rename to $MODEL"
    record "file $MODEL"; return
  fi
  local attempt
  for attempt in 1 2 3; do
    say "downloading $FILE (1.3 GB, attempt $attempt)..."
    # -C - resumes a cut .part; a wrong .part is deleted below and fetched fresh
    curl -fL --retry 5 --retry-delay 2 -C - -o "$MODEL.part" "$URL" || true
    if ok_file "$MODEL.part"; then mv "$MODEL.part" "$MODEL"; break; fi
    [ "$attempt" = 1 ] && [ -f "$MODEL.part" ] && continue   # first retry: maybe only truncated, resume
    rm -f "$MODEL.part"
  done
  ok_file "$MODEL" || { rm -f "$MODEL.part"; die "download failed or sha256 mismatch for $FILE: run the script again"; }
  record "file $MODEL"
}

xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

server_args() {   # one per line; the EngineBench flags from scripts/awos_model.sh + the idle sleep
  printf '%s\n' "$LLAMA" -m "$MODEL" --alias local --host 127.0.0.1 --port "$PORT" \
    -c 4096 -np 1 --jinja --reasoning off --cache-prompt --cache-ram 0 -cms 256 --temp 0
  if [ "$SLEEP_IDLE" != "-1" ]; then
    # llama.cpp >= b6900ish: unload the model after N idle seconds, reload on the next request
    if [ "$DRY" = 1 ] && [ ! -x "$LLAMA" ] || "$LLAMA" --help 2>&1 | grep -q -- --sleep-idle-seconds; then
      printf '%s\n' --sleep-idle-seconds "$SLEEP_IDLE"
    else
      warn "this llama.cpp has no --sleep-idle-seconds (brew upgrade llama.cpp); the model stays loaded (~0.8 GB)"
    fi
  fi
}

plist_text() {
  local a
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
EOF
  server_args | while IFS= read -r a; do printf '    <string>%s</string>\n' "$(xml_escape "$a")"; done
  cat <<EOF
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>LowPriorityBackgroundIO</key><true/>
  <key>StandardOutPath</key><string>$(xml_escape "$LOG")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$LOG")</string>
</dict>
</plist>
EOF
}

install_agent() {
  mkdir_rec "$AGENTS_DIR"
  local text; text=$(plist_text)
  if healthy && ! agent_loaded; then
    die "something else already serves 127.0.0.1:$PORT (scripts/awos_model.sh in a terminal?). Stop it and run again, or use AWOS_MODEL_PORT=8081 and set that endpoint in the extension Options."
  fi
  if [ "$DRY" = 1 ]; then
    say "[dry-run] write $PLIST:"; printf '%s\n' "$text" | sed 's/^/    /'
  else
    printf '%s\n' "$text" > "$PLIST.tmp"
    if command -v plutil >/dev/null && ! plutil -lint -s "$PLIST.tmp"; then rm -f "$PLIST.tmp"; die "generated plist is invalid"; fi
    mv "$PLIST.tmp" "$PLIST"
    # keep the log small: start fresh if it grew past 20 MB
    [ ! -f "$LOG" ] || [ "$(wc -c < "$LOG")" -lt 20971520 ] || : > "$LOG"
  fi
  record "file $PLIST"
  record "file $LOG"
  run launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true   # reload if it was already there
  run launchctl bootstrap "gui/$(id -u)" "$PLIST"
  [ "$DRY" = 1 ] && return
  say "starting the model server..."
  local _; for _ in $(seq 1 60); do healthy && break; sleep 1; done
  if healthy; then say "model server: UP on http://127.0.0.1:$PORT"
  else warn "model server not answering yet; check: bash install_replay.sh --status (log: $LOG)"; fi
}

do_install() {
  [ "$DRY" = 1 ] && say "AWOS_DRY_RUN=1: nothing will be changed; printing what install would do."
  check_mac
  mkdir_rec "$STATE"   # first, so the record itself lives in a directory we recorded
  pick_model
  ensure_llama
  download_model
  install_agent
  [ "$DRY" = 1 ] && say "" && say "(dry run: nothing was changed; the text below is what a real install prints)"
  cat <<EOF

AWOS Replay local model is installed. It starts at login, runs at low priority on 127.0.0.1:$PORT only,
and unloads itself after $SLEEP_IDLE idle seconds (about 58 MB while asleep).

Next steps:
  1. Install the AWOS Replay extension (Chrome Web Store), or for a dev build: chrome://extensions ->
     Developer mode -> Load unpacked -> the extension/ folder of the repo.
  2. Extension Options -> model endpoint http://127.0.0.1:$PORT/v1 (the default), Save.
  3. Record a chore once (popup -> Record), then Replay it.

Status:     curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install_replay.sh | bash -s -- --status
Uninstall:  curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install_replay.sh | bash -s -- --uninstall
EOF
}

do_status() {
  pick_model
  say "launchd agent: $([ -f "$PLIST" ] && echo "$PLIST" || echo "not installed")"
  if agent_loaded; then
    local pid; pid=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | awk '/^\tpid = / {print $3}')
    say "loaded: yes${pid:+ (pid $pid, resident $(( $(ps -o rss= -p "$pid" 2>/dev/null || echo 0) / 1024 )) MB)}"
  else
    say "loaded: no"
  fi
  if healthy; then say "server: UP on http://127.0.0.1:$PORT"; else say "server: DOWN on 127.0.0.1:$PORT"; fi
  if [ -f "$MODEL" ]; then say "model: $MODEL ($(( $(wc -c < "$MODEL") / 1048576 )) MB)"; else say "model: not downloaded ($FILE)"; fi
  say "llama-server: $(command -v llama-server || echo 'not on PATH')"
  [ -f "$LOG" ] && say "log: $LOG ($(( $(wc -c < "$LOG") / 1024 )) KB)"
  if [ -f "$RECORD" ]; then say "added by install_replay.sh:"; sed 's/^/  /' "$RECORD"; fi
  return 0
}

do_uninstall() {
  local keep_model="$1" line kind path dirs="" brew_pkg=0 homebrew=""
  run launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  if [ ! -f "$RECORD" ]; then
    # no record (older install, or a hand-made agent): only remove our own plist, nothing else
    [ -f "$PLIST" ] && run rm -f "$PLIST"
    say "nothing recorded in $RECORD; removed the launchd agent if present"; return 0
  fi
  while IFS= read -r line; do
    kind=${line%% *}; path=${line#* }
    case "$kind" in
      file)
        if [ "$keep_model" = 1 ] && [ "$path" = "${path%.gguf}.gguf" ]; then say "keeping $path"; continue; fi
        [ ! -e "$path" ] || run rm -f "$path"; [ ! -e "$path.part" ] || run rm -f "$path.part" ;;
      dir) dirs="$path $dirs" ;;              # reversed: deepest first
      brew) brew_pkg=1 ;;
      homebrew) homebrew="$path" ;;
    esac
  done < "$RECORD"
  if [ "$brew_pkg" = 1 ]; then
    local brew; brew=$(find_brew || true)
    if [ -n "$brew" ]; then run "$brew" uninstall llama.cpp || warn "brew uninstall llama.cpp failed"; fi
  fi
  run rm -f "$RECORD"
  # directories we created: removed only if empty (anything else you put there stays)
  for path in $dirs; do
    if [ "$DRY" = 1 ]; then say "[dry-run] rmdir $path (if empty)"; else rmdir "$path" 2>/dev/null || true; fi
  done
  [ -z "$homebrew" ] || say "Homebrew was installed by this script; other apps may use it now, so it stays. To remove it: https://github.com/Homebrew/install#uninstall-homebrew"
  say "AWOS Replay local model uninstalled. Remove the extension itself from chrome://extensions."
}

main() {
  case "${1:-}" in
    ""|--install) do_install ;;
    --status) do_status ;;
    --uninstall) if [ "${2:-}" = "--keep-model" ]; then do_uninstall 1; else do_uninstall 0; fi ;;
    -h|--help) sed -n '2,25p' "$0" 2>/dev/null || say "usage: install_replay.sh [--status|--uninstall [--keep-model]]" ;;
    *) die "usage: install_replay.sh [--status | --uninstall [--keep-model] | --help]" ;;
  esac
}

main "$@"   # last line: a cut-off `curl | bash` download never runs half a script
