#!/bin/bash
# install_replay.sh — one-command Mac setup for AWOS Replay's local models (no sudo, idempotent).
#
#     curl -fsSL https://raw.githubusercontent.com/savabs/AWOS_coding_agent-/main/scripts/install_replay.sh | bash
#     bash scripts/install_replay.sh               # install / repair (safe to run again)
#     bash scripts/install_replay.sh --agent 9b    # Tell-it agent model: 4b | 9b | none (default 4b on 8 GB+)
#     bash scripts/install_replay.sh --status      # what is installed, are both servers up, how much memory
#     bash scripts/install_replay.sh --uninstall   # remove exactly what install added (--keep-model keeps GGUFs)
#
# What install does (and nothing else):
#   1. checks macOS on Apple Silicon, and enough free disk for the models it still has to download
#   2. llama.cpp via Homebrew if llama-server is missing (asks first if Homebrew itself is missing; never
#      installs Homebrew silently, never uses sudo itself)
#   3. downloads the repair/verify model (same file + sha256 as scripts/awos_model.sh) and the Tell-it agent
#      model (Qwen3.5-4B or 9B Q4_K_M, pinned repo/revision/sha256) into ~/.awos/models,
#      via <file>.part + retries, renamed only when the sha256 matches
#   4. two launchd *user* agents, both 127.0.0.1 ONLY, ProcessType Background + nice 10 + low-priority IO,
#      --cache-ram 0 -cms 256, and --sleep-idle-seconds 300 (model unloaded after 5 idle minutes):
#        ai.awos.replay-model  2B repair/verify on :8080 (~58 MB asleep vs ~820 MB loaded)
#        ai.awos.replay-agent  Tell-it agent on :8081 (4B ~2.7 GB mapped while loaded, ~60 MB asleep)
#   5. a record of what it added in ~/.awos/replay/installed, which --uninstall reads
# It never touches browser profiles, other apps, or anything outside $HOME except `brew install llama.cpp`.
#
# Env: AWOS_DRY_RUN=1 (print every action, change nothing), AWOS_MODEL_PORT (8080), AWOS_AGENT_PORT (8081),
# AWOS_REPLAY_MODEL (g7|g6|base2b, default g7), AWOS_REPLAY_AGENT (4b|9b|none, same as --agent),
# AWOS_SLEEP_IDLE (seconds, 300; -1 = never unload), AWOS_NONINTERACTIVE=1 (answer "no" to every question),
# AWOS_MODEL_URL + AWOS_MODEL_SHA256 / AWOS_AGENT_URL + AWOS_AGENT_SHA256 (mirror / tests).
set -euo pipefail

REPO=savabs/AWOS_coding_agent-
LABEL=ai.awos.replay-model
ALABEL=ai.awos.replay-agent
MODELS="$HOME/.awos/models"
STATE="$HOME/.awos/replay"
RECORD="$STATE/installed"
LOG="$STATE/model-server.log"
ALOG="$STATE/agent-server.log"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/$LABEL.plist"
APLIST="$AGENTS_DIR/$ALABEL.plist"
PORT="${AWOS_MODEL_PORT:-8080}"
APORT="${AWOS_AGENT_PORT:-8081}"
SLEEP_IDLE="${AWOS_SLEEP_IDLE:-300}"
DRY="${AWOS_DRY_RUN:-0}"
case "$DRY" in 0|"") DRY=0 ;; *) DRY=1 ;; esac
AGENT_ARG="${AWOS_REPLAY_AGENT:-}"
GB=1073741824

say()  { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
# every state-changing command goes through run(): AWOS_DRY_RUN=1 prints it instead
run()  { if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else "$@"; fi; }
gb()   { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1073741824 }'; }

pick_model() {   # same files + sha256 as scripts/awos_model.sh (tests/test_install_replay.py keeps them in sync)
  case "${AWOS_REPLAY_MODEL:-g7}" in
    g7) FILE=qwen3.5-2b-g7-rv-Q4_K_M.gguf; SHA=a63e356f91aa5e1f741fc5132301f88140445e50f4b3a45658f921813c957f4f; SIZE=1274396352
        URL="https://github.com/$REPO/releases/download/rl-qwen3.5-2b-g7-rv/$FILE" ;;
    g6) FILE=qwen3.5-2b-g6-rv-Q4_K_M.gguf; SHA=af133cd576eecab134d955de70457aa40f91843d6bd0845d53bf1b2fa6534c62; SIZE=1274396352
        URL="https://github.com/$REPO/releases/download/rl-qwen3.5-2b-g6-rv/$FILE" ;;
    base2b) FILE=Qwen3.5-2B-Q4_K_M.gguf; SHA=aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223; SIZE=1280835840
        URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/$FILE" ;;
    *) die "AWOS_REPLAY_MODEL must be g7, g6 or base2b" ;;
  esac
  URL="${AWOS_MODEL_URL:-$URL}"
  SHA="${AWOS_MODEL_SHA256:-$SHA}"
  MODEL="$MODELS/$FILE"
}

# Tell-it agent models: pinned to the same Hugging Face files CI evaluates (ci/rl_eval.env MODEL_SPECS; a test
# keeps them equal). sha256 = the LFS oid from the HF API for that exact revision.
agent_spec() {
  case "$1" in
    4b) AREPO=unsloth/Qwen3.5-4B-GGUF AFILE=Qwen3.5-4B-Q4_K_M.gguf AREV=e87f176479d0855a907a41277aca2f8ee7a09523 ASIZE=2740937888 ASHA=00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4 ;;
    9b) AREPO=unsloth/Qwen3.5-9B-GGUF AFILE=Qwen3.5-9B-Q4_K_M.gguf AREV=3885219b6810b007914f3a7950a8d1b469d598a5 ASIZE=5680522464 ASHA=03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8 ;;
    *) die "unknown agent model '$1' (use 4b, 9b or none)" ;;
  esac
  AALIAS="qwen3.5-$1"
  AURL="${AWOS_AGENT_URL:-https://huggingface.co/$AREPO/resolve/$AREV/$AFILE}"
  ASHA="${AWOS_AGENT_SHA256:-$ASHA}"
  AMODEL="$MODELS/$AFILE"
}

mem_gb() { echo $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / GB )); }
recorded_agent() { [ -f "$RECORD" ] && awk '$1 == "agent" {print $2; exit}' "$RECORD" || true; }

choose_agent() {   # sets AGENT (4b|9b|none) and says why
  local mem; mem=$(mem_gb)
  if [ -n "$AGENT_ARG" ]; then
    AGENT="$AGENT_ARG"; say "Tell-it agent model: $AGENT (you chose it)"
    [ "$AGENT" = 9b ] && [ "$mem" -lt 16 ] && warn "the 9B model needs a 16 GB+ Mac (this one has ${mem} GB); --agent 4b is the safe choice"
  elif [ -n "$(recorded_agent)" ]; then
    AGENT=$(recorded_agent); say "Tell-it agent model: $AGENT (already installed; keeping it)"
  elif [ "$mem" -ge 8 ]; then
    AGENT=4b; say "Tell-it agent model: 4b (Qwen3.5-4B, 2.7 GB download; default for ${mem} GB RAM)"
    [ "$mem" -ge 16 ] && say "  Your Mac has ${mem} GB: --agent 9b is stronger (5.7 GB download, ~5.7 GB while loaded)."
  else
    AGENT=none; say "Tell-it agent model: none (only ${mem} GB RAM; the 4B needs ~2.7 GB while loaded)"
  fi
  say "  Change it any time: install_replay.sh --agent 4b | --agent 9b | --agent none"
}

sha256_of() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }
ok_file() { [ -f "$1" ] && [ "$(sha256_of "$1")" = "$2" ]; }   # ok_file <path> <sha256>
healthy() { curl -s -m 2 "http://127.0.0.1:$1/health" 2>/dev/null | grep -q ok; }   # healthy <port>
loaded()  { launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1; }                     # loaded <label>
model_ids() {   # the ids /v1/models lists on <port>, one per line
  curl -s -m 3 "http://127.0.0.1:$1/v1/models" 2>/dev/null | grep -o '"id" *: *"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/' || true
}
is_agent_id() {   # not the 2B repair/verify model (alias "local", or a 2b / g6 / g7 / verify / repair name)
  [ "$1" != local ] && ! printf '%s' "$1" | grep -Eiq '(^|[^0-9.])2b([^a-z0-9]|$)|g6|g7|verif|repair'
}

# ---- install record: one "<kind> <path-or-name>" per line, appended once ----
record() {
  if [ "$DRY" = 1 ]; then say "[dry-run] record: $*"; return; fi
  mkdir -p "$STATE"
  grep -qxF "$*" "$RECORD" 2>/dev/null || printf '%s\n' "$*" >> "$RECORD"
}
unrecord() {
  if [ "$DRY" = 1 ]; then say "[dry-run] unrecord: $*"; return; fi
  [ -f "$RECORD" ] || return 0
  grep -vxF "$*" "$RECORD" > "$RECORD.tmp" || true
  mv "$RECORD.tmp" "$RECORD"
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
  local mem ver
  mem=$(mem_gb)
  ver=$(sw_vers -productVersion 2>/dev/null || echo unknown)
  say "macOS $ver, Apple Silicon, ${mem} GB RAM"
  [ "$mem" -ge 8 ] || warn "less than 8 GB RAM: the repair model needs ~0.9 GB while loaded"
}

missing_bytes() {   # missing_bytes <path> <sha> <size> -> bytes still to download (a .part counts as done)
  if ok_file "$1" "$2"; then echo 0; return; fi
  local have=0; [ -f "$1.part" ] && have=$(wc -c < "$1.part" | tr -d ' ')
  [ "$have" -lt "$3" ] || have=0
  echo $(( $3 - have ))
}

check_disk() {   # refuse before downloading anything if the models will not fit (+1 GB headroom)
  local need free_kb
  need=$(missing_bytes "$MODEL" "$SHA" "$SIZE")
  [ "$AGENT" = none ] || need=$(( need + $(missing_bytes "$AMODEL" "$ASHA" "$ASIZE") ))
  [ "$need" -gt 0 ] || return 0
  free_kb=$(df -Pk "$HOME" | awk 'NR==2 {print $4}')
  if [ $(( ${free_kb:-0} * 1024 )) -lt $(( need + GB )) ]; then
    die "not enough free disk in $HOME: the models need $(gb "$need") GB plus 1 GB headroom, $(gb $(( ${free_kb:-0} * 1024 ))) GB is free. Free some space, or pick a smaller agent: --agent 4b or --agent none"
  fi
  say "disk: $(gb "$need") GB to download, $(gb $(( free_kb * 1024 ))) GB free"
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

download() {   # download <kind: file|agentfile> <path> <url> <sha256> <size-bytes>
  local kind="$1" path="$2" url="$3" sha="$4" size="$5" name attempt
  name=$(basename "$path")
  mkdir_rec "$MODELS"
  if ok_file "$path" "$sha"; then say "model: $path (already downloaded, sha256 ok)"; return; fi
  if [ "$DRY" = 1 ]; then
    say "[dry-run] download $url -> $path.part, check sha256 $sha, rename to $path"
    record "$kind $path"; return
  fi
  for attempt in 1 2 3; do
    say "downloading $name ($(gb "$size") GB, attempt $attempt)..."
    # -C - resumes a cut .part; a wrong .part is deleted below and fetched fresh
    curl -fL --retry 5 --retry-delay 2 -C - -o "$path.part" "$url" || true
    if ok_file "$path.part" "$sha"; then mv "$path.part" "$path"; break; fi
    [ "$attempt" = 1 ] && [ -f "$path.part" ] && continue   # first retry: maybe only truncated, resume
    rm -f "$path.part"
  done
  ok_file "$path" "$sha" || { rm -f "$path.part"; die "download failed or sha256 mismatch for $name: run the script again"; }
  record "$kind $path"
}

xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

sleep_args() {   # llama.cpp >= b6900ish: unload the model after N idle seconds, reload on the next request
  [ "$SLEEP_IDLE" != "-1" ] || return 0
  if [ "$DRY" = 1 ] && [ ! -x "$LLAMA" ] || "$LLAMA" --help 2>&1 | grep -q -- --sleep-idle-seconds; then
    printf '%s\n' --sleep-idle-seconds "$SLEEP_IDLE"
  else
    warn "this llama.cpp has no --sleep-idle-seconds (brew upgrade llama.cpp); the models stay loaded"
  fi
}

server_args() {   # one per line; the EngineBench flags from scripts/awos_model.sh + the idle sleep
  printf '%s\n' "$LLAMA" -m "$MODEL" --alias local --host 127.0.0.1 --port "$PORT" \
    -c 4096 -np 1 --jinja --reasoning off --cache-prompt --cache-ram 0 -cms 256 --temp 0
  sleep_args
}

agent_args() {   # Tell it: 12k context for page text + history, one slot, grammar-constrained (works on llama-server)
  printf '%s\n' "$LLAMA" -m "$AMODEL" --alias "$AALIAS" --host 127.0.0.1 --port "$APORT" \
    --jinja --reasoning off -c 12288 -np 1 --temp 0 --cache-ram 0 -cms 256
  sleep_args
}

plist_text() {   # plist_text <label> <log> <args-function>
  local a
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key>
  <array>
EOF
  "$3" | while IFS= read -r a; do printf '    <string>%s</string>\n' "$(xml_escape "$a")"; done
  cat <<EOF
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>LowPriorityBackgroundIO</key><true/>
  <key>StandardOutPath</key><string>$(xml_escape "$2")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$2")</string>
</dict>
</plist>
EOF
}

install_launchd() {   # install_launchd <kind> <label> <plist> <log> <port> <args-function> <what> <port-env>
  local kind="$1" label="$2" plist="$3" log="$4" port="$5" args="$6" what="$7" portenv="$8" text
  mkdir_rec "$AGENTS_DIR"
  text=$(plist_text "$label" "$log" "$args")
  if healthy "$port" && ! loaded "$label"; then
    die "something else already serves 127.0.0.1:$port (scripts/awos_model.sh or llama-server in a terminal?). Stop it and run again, or set $portenv to a free port and the same endpoint in the extension Options."
  fi
  if [ "$DRY" = 1 ]; then
    say "[dry-run] write $plist:"; printf '%s\n' "$text" | sed 's/^/    /'
  else
    printf '%s\n' "$text" > "$plist.tmp"
    if command -v plutil >/dev/null && ! plutil -lint -s "$plist.tmp"; then rm -f "$plist.tmp"; die "generated plist is invalid"; fi
    mv "$plist.tmp" "$plist"
    # keep the log small: start fresh if it grew past 20 MB
    [ ! -f "$log" ] || [ "$(wc -c < "$log")" -lt 20971520 ] || : > "$log"
  fi
  record "$kind $plist"
  record "$kind $log"
  run launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true   # reload if it was already there
  run launchctl bootstrap "gui/$(id -u)" "$plist"
  [ "$DRY" = 1 ] && return
  say "starting the $what server..."
  local _; for _ in $(seq 1 60); do healthy "$port" && break; sleep 1; done
  if healthy "$port"; then say "$what server: UP on http://127.0.0.1:$port"
  else warn "$what server not answering yet; check: bash install_replay.sh --status (log: $log)"; fi
}

remove_agent() {   # remove_agent [keep-path]: boot out the agent server, delete what we recorded for it
  local keep="${1:-}" line path snapshot
  if [ -z "$keep" ]; then run launchctl bootout "gui/$(id -u)/$ALABEL" 2>/dev/null || true; fi
  [ -f "$RECORD" ] || return 0
  snapshot=$(< "$RECORD")   # unrecord rewrites the file while we loop
  while IFS= read -r line; do
    case "$line" in
      "agent "*) [ -n "$keep" ] || unrecord "$line" ;;
      "agentfile "*)
        path=${line#agentfile }
        [ "$path" != "$keep" ] || continue
        if [ -n "$keep" ] && [ "$path" = "${path%.gguf}" ]; then continue; fi   # switching: only other GGUFs go
        [ ! -e "$path" ] || run rm -f "$path"; [ ! -e "$path.part" ] || run rm -f "$path.part"
        unrecord "$line" ;;
    esac
  done <<< "$snapshot"
}

install_agent_model() {
  if [ "$AGENT" = none ]; then
    if [ -f "$APLIST" ] || grep -q '^agent' "$RECORD" 2>/dev/null; then say "removing the Tell-it agent server and model (--agent none)"; fi
    remove_agent
    return
  fi
  remove_agent "$AMODEL"   # switching 4b <-> 9b: the other agent GGUF we downloaded goes
  local prev; prev=$(recorded_agent)
  [ -z "$prev" ] || [ "$prev" = "$AGENT" ] || unrecord "agent $prev"
  download agentfile "$AMODEL" "$AURL" "$ASHA" "$ASIZE"
  install_launchd agentfile "$ALABEL" "$APLIST" "$ALOG" "$APORT" agent_args "Tell-it agent" AWOS_AGENT_PORT
  record "agent $AGENT"
}

do_install() {
  [ "$DRY" = 1 ] && say "AWOS_DRY_RUN=1: nothing will be changed; printing what install would do."
  check_mac
  pick_model
  choose_agent
  [ "$AGENT" = none ] || agent_spec "$AGENT"
  check_disk
  mkdir_rec "$STATE"   # first, so the record itself lives in a directory we recorded
  ensure_llama
  download file "$MODEL" "$URL" "$SHA" "$SIZE"
  install_launchd file "$LABEL" "$PLIST" "$LOG" "$PORT" server_args "repair model" AWOS_MODEL_PORT
  install_agent_model
  [ "$DRY" = 1 ] && say "" && say "(dry run: nothing was changed; the text below is what a real install prints)"
  local tell
  if [ "$AGENT" = none ]; then
    tell="  4. Tell it (describe a chore instead of recording it) needs an agent model: run this again with --agent 4b"
  else
    tell="  4. Tell it: Options -> agent endpoint http://127.0.0.1:$APORT/v1, model $AALIAS (the defaults), Save."
  fi
  cat <<EOF

AWOS Replay local models are installed. They start at login, run at low priority on 127.0.0.1 only,
and unload themselves after $SLEEP_IDLE idle seconds (about 60 MB each while asleep).
  repair / verify model: http://127.0.0.1:$PORT/v1
  Tell-it agent model:   $([ "$AGENT" = none ] && echo "not installed (--agent none)" || echo "http://127.0.0.1:$APORT/v1 ($AALIAS)")

Next steps:
  1. Install the AWOS Replay extension (Chrome Web Store), or for a dev build: chrome://extensions ->
     Developer mode -> Load unpacked -> the extension/ folder of the repo.
  2. Extension Options -> model endpoint http://127.0.0.1:$PORT/v1 (the default), Save.
  3. Record a chore once (popup -> Record), then Replay it.
$tell

Status:     curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install_replay.sh | bash -s -- --status
Agent:      curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install_replay.sh | bash -s -- --agent 9b
Uninstall:  curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install_replay.sh | bash -s -- --uninstall
EOF
}

status_one() {   # status_one <title> <label> <plist> <port> <model-path-or-empty> <log>
  say "$1:"
  say "  launchd agent: $([ -f "$3" ] && echo "$3" || echo "not installed")"
  if loaded "$2"; then
    local pid; pid=$(launchctl print "gui/$(id -u)/$2" 2>/dev/null | awk '/^\tpid = / {print $3}')
    say "  loaded: yes${pid:+ (pid $pid, resident $(( $(ps -o rss= -p "$pid" 2>/dev/null || echo 0) / 1024 )) MB)}"
  else
    say "  loaded: no"
  fi
  if healthy "$4"; then say "  server: UP on http://127.0.0.1:$4"; else say "  server: DOWN on 127.0.0.1:$4"; fi
  if [ -n "$5" ]; then
    if [ -f "$5" ]; then say "  model: $5 ($(( $(wc -c < "$5") / 1048576 )) MB)"; else say "  model: not downloaded ($(basename "$5"))"; fi
  fi
  [ ! -f "$6" ] || say "  log: $6 ($(( $(wc -c < "$6") / 1024 )) KB)"
}

do_status() {
  pick_model
  status_one "repair / verify model" "$LABEL" "$PLIST" "$PORT" "$MODEL" "$LOG"
  local agent amodel="" ids id ok=""
  agent=$(recorded_agent)
  if [ -n "$agent" ]; then agent_spec "$agent"; amodel="$AMODEL"; fi
  status_one "Tell-it agent model (${agent:-none})" "$ALABEL" "$APLIST" "$APORT" "$amodel" "$ALOG"
  if healthy "$APORT"; then
    ids=$(model_ids "$APORT")
    for id in $ids; do is_agent_id "$id" && ok="$id"; done
    if [ -n "$ok" ]; then say "  serves: $ok (an agent model: Tell it can use it)"
    else say "  serves: ${ids:-nothing listed} -- NOT an agent model (that is the 2B repair model?); run: install_replay.sh --agent 4b"; fi
  elif [ -z "$agent" ]; then
    say "  (Tell it needs an agent model: install_replay.sh --agent 4b, or --agent 9b on a 16 GB+ Mac)"
  fi
  say "llama-server: $(command -v llama-server || echo 'not on PATH')"
  if [ -f "$RECORD" ]; then say "added by install_replay.sh:"; sed 's/^/  /' "$RECORD"; fi
  return 0
}

do_uninstall() {
  local keep_model="$1" line kind path dirs="" brew_pkg=0 homebrew=""
  run launchctl bootout "gui/$(id -u)/$ALABEL" 2>/dev/null || true
  run launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  if [ ! -f "$RECORD" ]; then
    # no record (older install, or a hand-made agent): only remove our own plists, nothing else
    [ ! -f "$PLIST" ] || run rm -f "$PLIST"
    [ ! -f "$APLIST" ] || run rm -f "$APLIST"
    say "nothing recorded in $RECORD; removed the launchd agents if present"; return 0
  fi
  while IFS= read -r line; do
    kind=${line%% *}; path=${line#* }
    case "$kind" in
      file|agentfile)
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
  say "AWOS Replay local models uninstalled. Remove the extension itself from chrome://extensions."
}

usage() { die "usage: install_replay.sh [--agent 4b|9b|none] | --status | --uninstall [--keep-model] | --help"; }

main() {
  local action=install keep=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --install) action=install ;;
      --status) action=status ;;
      --uninstall) action=uninstall ;;
      --keep-model) keep=1 ;;
      --agent) [ $# -ge 2 ] || die "--agent needs 4b, 9b or none"; AGENT_ARG="$2"; shift ;;
      --agent=*) AGENT_ARG="${1#--agent=}" ;;
      -h|--help) action=help ;;
      *) usage ;;
    esac
    shift
  done
  case "$AGENT_ARG" in ""|4b|9b|none) ;; *) die "--agent must be 4b, 9b or none (got '$AGENT_ARG')" ;; esac
  case "$action" in
    install) do_install ;;
    status) do_status ;;
    uninstall) do_uninstall "$keep" ;;
    help) sed -n '2,27p' "$0" 2>/dev/null || say "usage: install_replay.sh [--agent 4b|9b|none] [--status|--uninstall [--keep-model]]" ;;
  esac
}

main "$@"   # last line: a cut-off `curl | bash` download never runs half a script
