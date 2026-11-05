#!/bin/bash
# Installs the AWOS Replay run-log service (scripts/awos_logsink.py) as a login service (launchd), so every run log the
# extension produces lands in <dir> (default: the project's runlogs/). Listens on 127.0.0.1:8099 only.
#   bash scripts/install_logsink.sh [dir]        install / update
#   bash scripts/install_logsink.sh --remove     stop and remove it
set -euo pipefail
LABEL=com.awos.logsink
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$HOME/.awos/awos_logsink.py"
  echo "removed $LABEL"; exit 0
fi
DIR="${1:-$HOME/Projects/AWOS_coding_agent/runlogs}"
mkdir -p "$DIR" "$HOME/.awos" "$HOME/Library/LaunchAgents"
cp "$HERE/awos_logsink.py" "$HOME/.awos/awos_logsink.py"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$HOME/.awos/awos_logsink.py</string>
    <string>--dir</string>
    <string>$DIR</string>
    <string>--port</string>
    <string>8099</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.awos/logsink.err</string>
</dict>
</plist>
EOF
plutil -lint -s "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
curl -s http://127.0.0.1:8099/health && echo && echo "installed: run logs now go to $DIR"
