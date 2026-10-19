#!/usr/bin/env python3
"""
run.py — runner for the dev-dashboard testbed (docs/specs/testbed_dev_dashboard.md).

    # validate the testbed itself: oracle must PASS, every bad solution must FAIL
    python testbeds/dev_dashboard/run.py --dry-run [--seed 7] [--root /tmp/dd]

    # just launch a seeded dashboard to poke at by hand (or with a browser agent)
    python testbeds/dev_dashboard/run.py serve --variant a --seed 7 --root /tmp/dd

    # run an agent command against one variant, then verify
    python testbeds/dev_dashboard/run.py run --variant b --seed 7 --root /tmp/dd \
        --agent-cmd 'my-agent --task {task_file} --cwd {sandbox}'

For ``run`` the agent command runs with cwd = sandbox and these env vars:
  DD_SANDBOX, DD_TASK_FILE, DD_AGENT_ENV, DD_DASHBOARD_URL, DD_APPROVAL_URL,
  DD_OUTBOX, plus AWOS_PROVIDER (passed through, e.g. ``local`` or
  ``claude-haiku-5-5``). The runner itself never calls a model. stdout+stderr
  become harness/transcript.txt (scanned for leaks). If the agent writes a
  clipboard dump to $DD_CLIPBOARD_DUMP (a harness path) it is scanned too.
The harness folder (truth, logs) is never passed to the agent.

A run folder that already has harness/verify.json is skipped (resumable).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dd_oracle  # noqa: E402
import dd_seed  # noqa: E402
import dd_server  # noqa: E402
import verify as dd_verify  # noqa: E402


def setup(root: Path, variant: str, seed: int):
    truth = dd_seed.seed(root, variant, seed)
    srv = dd_server.DashboardServers(root / "harness/dashboard_state.json").start()
    sb = root / "sandbox"
    (sb / "AGENT_ENV.json").write_text(json.dumps({
        "dashboard_url": srv.dashboard_url, "approval_url": srv.approval_url,
        "approval_api": "POST {approval_url}/request JSON {action, project, scope, key_name, reason} "
                        "-> {decision: approved|denied}",
        "outbox": "outbox/", "task_file": "TASK.md"}, indent=2) + "\n")
    return truth, srv


def run_case(root: Path, variant: str, seed: int, mode: str) -> dict:
    truth, srv = setup(root, variant, seed)
    try:
        out = dd_oracle.solve(root / "sandbox", variant, mode)
    finally:
        srv.stop()
    tr = root / "harness/transcript.txt"
    tr.write_text(out["transcript"])
    res = dd_verify.verify(root / "harness", transcript=tr)
    (root / "harness/verify.json").write_text(json.dumps(res, indent=2))
    return res


def dry_run(root: Path, seed: int) -> dict:
    rows, ok_all = [], True
    for v in dd_seed.VARIANTS:
        for mode in dd_oracle.MODES:
            if v not in dd_oracle.APPLIES[mode]:
                continue
            res = run_case(root / f"{v}_{mode}", v, seed, mode)
            if mode == "oracle":
                good = res["pass"]
            else:
                good = (not res["pass"]) and dd_oracle.EXPECT[mode] <= set(res["reasons"])
            ok_all &= good
            rows.append({"variant": v, "mode": mode, "pass": res["pass"], "reasons": res["reasons"],
                         "as_expected": good})
    return {"ok": ok_all, "rows": rows}


def cmd_dry(a) -> int:
    root = Path(a.root) if a.root else Path(tempfile.mkdtemp(prefix="dd_dry_"))
    out = dry_run(root, a.seed)
    for r in out["rows"]:
        print(f"{'ok ' if r['as_expected'] else 'BAD'} variant={r['variant']} {r['mode']:<18} "
              f"{'PASS' if r['pass'] else 'FAIL'} {','.join(r['reasons'])}")
    print(("DRY RUN OK" if out["ok"] else "DRY RUN MISMATCH") + f" ({len(out['rows'])} cases, root {root})")
    return 0 if out["ok"] else 1


def cmd_serve(a) -> int:
    root = Path(a.root)
    truth, srv = setup(root, a.variant, a.seed)
    print(f"sandbox:       {root / 'sandbox'}\nharness:       {root / 'harness'} (do not give this to the agent)\n"
          f"dashboard:     {srv.dashboard_url}\napproval desk: {srv.approval_url}\n"
          f"login:         see {root / 'sandbox/Keychain/fakeservice.json'}\n"
          f"verify:        python {HERE / 'verify.py'} --harness {root / 'harness'}\nCtrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
    return 0


def cmd_run(a) -> int:
    root = Path(a.root)
    if (root / "harness/verify.json").exists() and not a.force:
        print(f"skip: {root} already verified (use --force)")
        return 0
    truth, srv = setup(root, a.variant, a.seed)
    sb, hz = root / "sandbox", root / "harness"
    env_json = json.loads((sb / "AGENT_ENV.json").read_text())
    env = {k: v for k, v in os.environ.items() if not k.startswith("DD_")}
    env.update({"DD_SANDBOX": str(sb), "DD_TASK_FILE": str(sb / "TASK.md"),
                "DD_AGENT_ENV": str(sb / "AGENT_ENV.json"), "DD_DASHBOARD_URL": env_json["dashboard_url"],
                "DD_APPROVAL_URL": env_json["approval_url"], "DD_OUTBOX": str(sb / "outbox"),
                "DD_CLIPBOARD_DUMP": str(hz / "clipboard.txt")})
    if a.provider:
        env["AWOS_PROVIDER"] = a.provider
    cmd = a.agent_cmd.format(task_file=shlex.quote(str(sb / "TASK.md")), sandbox=shlex.quote(str(sb)),
                             agent_env=shlex.quote(str(sb / "AGENT_ENV.json")))
    tr = hz / "transcript.txt"
    t0 = time.time()
    try:
        with tr.open("w") as f:
            p = subprocess.run(cmd, shell=True, cwd=sb, env=env, stdout=f, stderr=subprocess.STDOUT,
                               timeout=a.timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    finally:
        srv.stop()
    clip = hz / "clipboard.txt"
    res = dd_verify.verify(hz, transcript=tr, clipboard=clip if clip.exists() else None)
    res["agent"] = {"cmd": a.agent_cmd, "rc": rc, "wall_s": round(time.time() - t0, 1),
                    "provider": env.get("AWOS_PROVIDER")}
    (hz / "verify.json").write_text(json.dumps(res, indent=2))
    print(("PASS" if res["pass"] else "FAIL") + f" reasons={res['reasons']} metrics={res['metrics']}")
    return 0 if res["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dev-dashboard testbed runner")
    ap.add_argument("--dry-run", action="store_true", help="oracle + bad scripted solutions, all variants")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--variant", choices=dd_seed.VARIANTS, default="a")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--root", required=True)
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=dd_seed.VARIANTS, required=True)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--root", required=True)
    r.add_argument("--agent-cmd", required=True,
                   help="shell command; placeholders {task_file} {sandbox} {agent_env}")
    r.add_argument("--provider", help="AWOS_PROVIDER for the agent (local | claude-haiku-5-5)")
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.dry_run:
        return cmd_dry(a)
    if a.cmd == "serve":
        return cmd_serve(a)
    if a.cmd == "run":
        return cmd_run(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
