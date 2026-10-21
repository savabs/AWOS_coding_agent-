#!/bin/bash
# GitHub macOS runner ONLY (disposable VM): pre-grant Apple Events (Automation) + Reminders/Calendar/
# Contacts data access from the runner's
# processes to the apps MacArena's setup/evaluator scripts drive with osascript. MacArena's own VM
# image has these consents already; on a stock runner the first `tell application "Notes"` raises a
# "hosted-compute-agent wants access to control Notes" prompt and the script hangs (seen in run 1).
# Same row format as actions/runner-images configure-tccdb-macos.sh. Never run this on a real Mac.
set -u
if [ "$(sysctl -n kern.hv_vmm_present)" != "1" ] || [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  echo "refusing: not a GitHub-hosted runner VM"; exit 2
fi
CLIENTS="/opt/hca/hosted-compute-agent /bin/bash /bin/sh /usr/bin/osascript /usr/bin/python3 $(command -v python3)"
TARGETS="com.apple.Notes com.apple.reminders com.apple.iCal com.apple.AddressBook com.apple.systemevents
com.apple.finder com.apple.Safari com.apple.TextEdit com.apple.Automator com.apple.calculator
com.apple.systempreferences com.apple.ScriptEditor2 com.apple.Music com.apple.ActivityMonitor com.apple.Chess
com.apple.Preview com.apple.shortcuts com.apple.Stickies com.apple.freeform com.apple.FontBook com.apple.Maps
com.apple.Dictionary com.apple.stocks com.apple.QuickTimePlayerX com.apple.Photos com.apple.mail"
NOW=$(date +%s)
SQL=""
for c in $CLIENTS; do
  for t in $TARGETS; do
    SQL+="INSERT OR REPLACE INTO access VALUES('kTCCServiceAppleEvents','$c',1,2,3,1,NULL,NULL,0,'$t',NULL,NULL,$NOW,NULL,NULL,'UNUSED',$NOW);"
  done
done
# Data-access consent (run 3: "hosted-compute-agent would like to access your reminders" blocked
# the Reminders evaluator even with Apple Events granted).
for c in $CLIENTS; do
  for svc in kTCCServiceReminders kTCCServiceCalendar kTCCServiceAddressBook; do
    SQL+="INSERT OR REPLACE INTO access VALUES('$svc','$c',1,2,3,1,NULL,NULL,NULL,'UNUSED',NULL,0,$NOW,NULL,NULL,'UNUSED',$NOW);"
  done
done
for DB in "$HOME/Library/Application Support/com.apple.TCC/TCC.db" "/Library/Application Support/com.apple.TCC/TCC.db"; do
  if sudo sqlite3 "$DB" "$SQL"; then echo "granted in $DB"; else echo "FAILED for $DB"; fi
done
echo "AppleEvents rows now: $(sqlite3 "$HOME/Library/Application Support/com.apple.TCC/TCC.db" "select count(*) from access where service='kTCCServiceAppleEvents';" 2>&1)"
