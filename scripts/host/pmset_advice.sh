#!/bin/bash
# Print (never apply) power settings that keep an always-on AWOS host alive.
# Spec: docs/specs/host_ops.md. Runs this week died from laptop sleep and battery.
set -u
echo "# Current power source:"
if command -v pmset >/dev/null 2>&1; then
  pmset -g batt | sed 's/^/#   /'
  echo "# Current AC settings (sleep / disksleep / standby):"
  pmset -g custom 2>/dev/null | grep -E '^(AC Power|Battery Power)|^ (sleep|disksleep|displaysleep|standby|powernap|autopoweroff|tcpkeepalive) ' | sed 's/^/#   /'
else
  echo "#   pmset not available (not macOS)"
fi
cat <<'EOF'

# Recommended (NOT applied — review, then run yourself; needs sudo):
#   -c = on AC (charger) only. Battery settings are left alone on purpose:
#   an always-on host should run on AC; the watchdog pauses the queue on low battery.
sudo pmset -c sleep 0          # never system-sleep on AC
sudo pmset -c disksleep 0      # keep disks spinning on AC
sudo pmset -c displaysleep 10  # the display may sleep; the system must not
sudo pmset -c standby 0        # no deep standby on AC
sudo pmset -c powernap 0       # avoid dark-wake churn
sudo pmset -c tcpkeepalive 1   # keep network connections alive
sudo pmset -c womp 1           # wake on network access

# Lid closed: macOS sleeps a closed laptop unless an external display is attached
# (clamshell). Keep the lid open, or use an external display + AC.

# Per-session alternative without changing settings (stops when the shell exits):
caffeinate -dimsu -w "$(pgrep -f scaffold.agent.host.worker | head -1)"

# To revert to defaults:
sudo pmset -c restoredefaults
EOF
