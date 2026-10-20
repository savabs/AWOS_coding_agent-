"""python -m scaffold.agent.desktop.live {probe,demo,vm-run} — see probe.py."""
from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import probe as P


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    P.load_live_env()                                  # AWOS_* settings vm-run left for the helper
    ap = argparse.ArgumentParser(prog="python -m scaffold.agent.desktop.live",
                                 description="live macOS control (guest VM only) and the host-side VM runner")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("probe", help="permissions, VM check, frontmost app, compressed tree")
    p.add_argument("--app", default="Safari")
    p.add_argument("--max-nodes", type=int, default=800)
    p.add_argument("--json", action="store_true")
    d = sub.add_parser("demo", help="live end-to-end demo in the guest (dd | teach_t3)")
    d.add_argument("--target", required=True, choices=("dd", "teach_t3"))
    d.add_argument("--variant", default="a", choices=("a", "b", "c"))
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--instance", default="i03")
    d.add_argument("--root")
    d.add_argument("--out")
    d.add_argument("--keep-window", action="store_true")
    v = sub.add_parser("vm-run", help="host side: run a live command inside the Tart guest over ssh")
    v.add_argument("--vm", help="tart VM name (default $AWOS_VM or awos-guest)")
    v.add_argument("--ip", help="guest IP (skips `tart ip`)")
    v.add_argument("--user", default="admin")
    v.add_argument("--guest-dir", default="AWOS_coding_agent")
    v.add_argument("--guest-python", default="$HOME/AWOS_coding_agent/.venv/bin/python")
    v.add_argument("--no-sync", action="store_true")
    v.add_argument("--build-helper", action="store_true", help="(re)build AWOS Helper.app in the guest first")
    v.add_argument("--direct", action="store_true", help="run python directly over ssh (no helper; no AX)")
    v.add_argument("--model-url", help="AWOS_LOCAL_BASE_URL for the guest (default http://<host bridge>:8080/v1)")
    v.add_argument("--tunnel", type=int, metavar="PORT",
                   help="reverse-tunnel the host's 127.0.0.1:PORT into the guest (model URL becomes loopback)")
    v.add_argument("--pull", default="live_results", help="local folder for ~/.awos/live_results")
    v.add_argument("--dry-run", action="store_true", help="print the commands only")
    v.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        return P.probe(a.app, max_nodes=a.max_nodes, as_json=a.json)
    if a.cmd == "demo":
        return P.demo(a.target, variant=a.variant, seed=a.seed, instance=a.instance, root=a.root, out_dir=a.out,
                      keep_window=a.keep_window)
    if a.cmd == "vm-run":
        cmd = list(a.command[1:]) if a.command[:1] == ["--"] else list(a.command)
        return P.vm_run(cmd, vm=a.vm, ip=a.ip, dry_run=a.dry_run, user=a.user, guest_dir=a.guest_dir,
                        guest_python=a.guest_python, sync=not a.no_sync, build_helper=a.build_helper,
                        direct=a.direct, model_url=a.model_url, tunnel_port=a.tunnel, pull=a.pull)
    ap.print_help()
    return 3


if __name__ == "__main__":
    sys.exit(main())
