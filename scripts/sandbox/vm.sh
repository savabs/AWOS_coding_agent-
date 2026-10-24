#!/bin/bash
# AWOS sandbox: all computer-use control happens inside a macOS VM (Tart, Apple Virtualization),
# never on the owner's Mac. The host only serves the local model; the guest reaches it at the
# host's bridge address.
#
#   scripts/sandbox/vm.sh start            # boot the VM (window) with isolated networking
#   scripts/sandbox/vm.sh start --headless # boot without a window
#   scripts/sandbox/vm.sh ip | ssh [cmd...] | stop | status
#   scripts/sandbox/vm.sh snapshot <name>  # save the current (stopped) VM as a clean state
#   scripts/sandbox/vm.sh reset <name>     # throw the VM away and restore that state
#   scripts/sandbox/vm.sh sync             # rsync the AWOS repo into the guest (~/awos)
#   scripts/sandbox/vm.sh model-url        # the base URL the guest uses for the host model
#
# Guest login (cirruslabs base image): admin / admin. Change it after first boot if you like.
set -euo pipefail
VM="${AWOS_VM:-awos-sandbox}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE="$HOME/.awos/sandbox"
mkdir -p "$STATE"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile="$STATE/known_hosts" -o ConnectTimeout=10)
GUEST_USER="${AWOS_VM_USER:-admin}"

ip() { tart ip "$VM" --wait 120; }

host_bridge_ip() {
  # The host side of the VM network (first three octets of the guest IP, .1).
  local g; g="$(ip)"; echo "${g%.*}.1"
}

case "${1:-}" in
  start)
    shift
    args=(--net-softnet)                       # guest isolated from host + other VMs
    if [[ "${AWOS_VM_ALLOW_HOST_MODEL:-1}" == "1" ]]; then
      args+=(--net-softnet-allow=192.168.64.1/32)   # ...except the host model server
    fi
    [[ "${1:-}" == "--headless" ]] && args+=(--no-graphics)
    nohup tart run "$VM" "${args[@]}" > "$STATE/run.log" 2>&1 &
    echo $! > "$STATE/run.pid"
    echo "starting $VM (pid $(cat "$STATE/run.pid")); waiting for IP..."
    ip
    ;;
  stop)   tart stop "$VM" || true ;;
  status) tart list | sed -n "1p;/$VM/p" ;;
  ip)     ip ;;
  ssh)    shift; exec ssh "${SSH_OPTS[@]}" "$GUEST_USER@$(ip)" "$@" ;;
  snapshot)
    name="${2:?snapshot name}"; tart stop "$VM" 2>/dev/null || true
    tart clone "$VM" "$VM-snap-$name"; echo "saved $VM-snap-$name" ;;
  reset)
    name="${2:?snapshot name}"; tart stop "$VM" 2>/dev/null || true
    tart delete "$VM"; tart clone "$VM-snap-$name" "$VM"; echo "restored $VM from $VM-snap-$name" ;;
  sync)
    rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
      --exclude .git --exclude .awos --exclude '__pycache__' --exclude '.venv' --exclude 'node_modules' \
      "$REPO/" "$GUEST_USER@$(ip):awos/"
    echo "synced repo -> $GUEST_USER@$(ip):~/awos" ;;
  model-url) echo "http://$(host_bridge_ip):8080/v1" ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
