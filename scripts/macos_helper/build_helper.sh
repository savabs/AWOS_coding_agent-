#!/bin/bash
# Build "AWOS Helper.app": the one app the owner grants Accessibility + Input Monitoring.
# Everything that observes or drives the Mac UI runs through it, so the grant is scoped to
# AWOS instead of a terminal or the whole Claude Code process.
#
#   scripts/macos_helper/build_helper.sh            # builds ~/Applications/AWOS Helper.app
#   scripts/macos_helper/run_helper.sh <module> ... # runs `python -m <module> ...` inside it
#
# The bundle is ad-hoc signed (codesign -s -). Rebuilding changes the signature, so macOS may
# ask for the permissions again; build once and leave it.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${AWOS_HELPER_PYTHON:-/Users/becmachlean/Projects/AWOS_coding_agent/.venv/bin/python}"
APP="${AWOS_HELPER_APP:-$HOME/Applications/AWOS Helper.app}"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.awos.helper</string>
  <key>CFBundleName</key><string>AWOS Helper</string>
  <key>CFBundleDisplayName</key><string>AWOS Helper</string>
  <key>CFBundleExecutable</key><string>awos-helper</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSAccessibilityUsageDescription</key><string>AWOS reads and operates app controls only for tasks you taught or approved.</string>
</dict>
</plist>
PLIST

# The executable: run one queued job (written by run_helper.sh), log, write the exit code.
cat > "$APP/Contents/MacOS/awos-helper" <<EXE
#!/bin/bash
JOBS="\$HOME/.awos/helper"
mkdir -p "\$JOBS"
JOB="\$JOBS/job.args"
[ -f "\$JOB" ] || { echo "no job" > "\$JOBS/last.log"; exit 0; }
cd "$REPO"
export PYTHONPATH="$REPO"
# shellcheck disable=SC2046
"$PY" -m \$(cat "\$JOB") > "\$JOBS/last.log" 2>&1
echo \$? > "\$JOBS/last.exit"
rm -f "\$JOB"
EXE
chmod +x "$APP/Contents/MacOS/awos-helper"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "built: $APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true
