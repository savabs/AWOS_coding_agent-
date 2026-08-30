#!/usr/bin/env bash
# subagentStart hook: audit log for delegated subagents (allow all).
set -euo pipefail

input=$(cat)
subagent_type=$(echo "$input" | jq -r '.subagent_type // "unknown"')
task_preview=$(echo "$input" | jq -r '.task // .prompt // ""' | head -c 120)

log_dir="$(dirname "$0")/../../.awos"
mkdir -p "$log_dir"
echo "$(date -Iseconds) subagent=${subagent_type} task=${task_preview}" >> "$log_dir/cursor_subagent.log"

echo '{"permission":"allow"}'
exit 0
