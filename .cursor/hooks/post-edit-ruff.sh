#!/usr/bin/env bash
# afterFileEdit hook: run ruff check --fix on edited Python files (best-effort).
set -euo pipefail

input=$(cat)
file_path=$(echo "$input" | jq -r '.file_path // .path // .tool_input.file_path // .tool_input.path // empty')

if [[ -z "$file_path" || "$file_path" != *.py ]]; then
  exit 0
fi

# Resolve ruff: PATH, project venv, or skip silently
RUFF=""
for candidate in ruff "${BASH_SOURCE%/*}/../../../.venv/bin/ruff" ".venv/bin/ruff"; do
  if command -v "$candidate" &>/dev/null; then
    RUFF="$candidate"
    break
  fi
  if [[ -x "$candidate" ]]; then
    RUFF="$candidate"
    break
  fi
done

if [[ -z "$RUFF" ]]; then
  exit 0
fi

# Best-effort format; never block the edit on lint failure
"$RUFF" check --fix "$file_path" 2>/dev/null || true
"$RUFF" format "$file_path" 2>/dev/null || true
exit 0
