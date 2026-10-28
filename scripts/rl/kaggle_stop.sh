#!/usr/bin/env bash
# Remote stop switch for the Kaggle training kernel (kaggle/rl-sft/train.py, config stop_url).
#
#   bash scripts/rl/kaggle_stop.sh stop      # running kernels stop within ~5 steps, save adapter + metrics
#   bash scripts/rl/kaggle_stop.sh clear     # remove the file (optional: only stops written after a
#                                            # run started count, so a stale file never stops the next run)
#   bash scripts/rl/kaggle_stop.sh status
#
# Writes kaggle/STOP (a UTC timestamp) on the `rl-control` branch through the GitHub API, so no local
# checkout or branch switch is needed. raw.githubusercontent.com caches for up to ~5 minutes.
set -euo pipefail
REPO="${REPO:-savabs/AWOS_coding_agent-}"
BRANCH=rl-control
FILE=kaggle/STOP
cmd="${1:-status}"

ensure_branch() {
  gh api "repos/$REPO/git/ref/heads/$BRANCH" >/dev/null 2>&1 && return
  sha=$(gh api "repos/$REPO/git/ref/heads/claude/bench-run" --jq .object.sha)
  gh api -X POST "repos/$REPO/git/refs" -f ref="refs/heads/$BRANCH" -f sha="$sha" >/dev/null
  echo "created branch $BRANCH"
}
file_sha() { gh api "repos/$REPO/contents/$FILE?ref=$BRANCH" --jq .sha 2>/dev/null || true; }

case "$cmd" in
  stop)
    ensure_branch
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    sha=$(file_sha)
    args=(-X PUT "repos/$REPO/contents/$FILE" -f message="kaggle stop $now" -f branch="$BRANCH"
          -f content="$(printf '%s\n' "$now" | base64)")
    [ -n "$sha" ] && args+=(-f sha="$sha")
    gh api "${args[@]}" >/dev/null
    echo "stop requested at $now; the kernel checks every few steps (raw GitHub caches up to ~5 min)"
    ;;
  clear)
    sha=$(file_sha)
    [ -z "$sha" ] && { echo "no stop file"; exit 0; }
    gh api -X DELETE "repos/$REPO/contents/$FILE" -f message="kaggle stop cleared" -f branch="$BRANCH" -f sha="$sha" >/dev/null
    echo "stop file removed"
    ;;
  status)
    body=$(gh api "repos/$REPO/contents/$FILE?ref=$BRANCH" --jq .content 2>/dev/null | base64 --decode 2>/dev/null || true)
    echo "${body:-no stop file}"
    ;;
  *) echo "usage: $0 stop|clear|status" >&2; exit 2 ;;
esac
