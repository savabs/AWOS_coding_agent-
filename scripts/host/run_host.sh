#!/bin/bash
# Entry point launchd runs for ai.awos.host (see scripts/host/install_launchd.py).
# Sources the env file (secrets live there, never in the plist), then execs the
# host worker loop. Override the worker command with AWOS_HOST_CMD.
set -u
ROOT="${AWOS_HOST_DIR:-$HOME/.awos/host}"
ENV_FILE="${AWOS_HOST_ENV_FILE:-$ROOT/host.env}"
PY="${AWOS_HOST_PYTHON:-python3}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/logs"

if [ -f "$ENV_FILE" ]; then
  perms=$(stat -f '%Lp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE")
  case "$perms" in
    600|400) ;;
    *) echo "run_host: refusing $ENV_FILE with mode $perms (want 600)" >&2; exit 78 ;;
  esac
  set -a; . "$ENV_FILE"; set +a
fi

cd "$REPO" || exit 1
echo "run_host: start $(date -u +%FT%TZ) root=$ROOT"
if [ -n "${AWOS_HOST_CMD:-}" ]; then
  exec /bin/bash -c "$AWOS_HOST_CMD"
fi
exec "$PY" -m scaffold.agent.host.worker
