#!/usr/bin/env python3
"""
run.py — runner for the "Month-end Document Pack" mock-portal testbed (testbeds/doc_pack/README.md).

    # validate the testbed: the oracle must PASS every variant, every bad solution must FAIL
    python testbeds/doc_pack/run.py --dry-run [--seed 7] [--months 2026-08,2026-09] [--root /tmp/dp]

    # launch seeded portals to poke at by hand (a real person answers captcha / OTP)
    python testbeds/doc_pack/run.py serve --variant c --root /tmp/dp [--human manual|scripted]

    # run an agent command against one variant, then verify
    python testbeds/doc_pack/run.py run --variant b --root /tmp/dp \
        --agent-cmd 'my-agent --task {task_file} --cwd {sandbox}'

    # the oracle in real Safari (macOS guest VM / CI runner only; refuses on bare metal)
    python testbeds/doc_pack/run.py safari --variant a --root /tmp/dp --out out/

For ``run`` the agent command runs with cwd = sandbox and env DP_SANDBOX, DP_TASK_FILE,
DP_AGENT_ENV, DP_OUTBOX, DP_HUMAN_DESK_URL, DP_<PORTAL>_URL, plus HTTP_PROXY / HTTPS_PROXY
pointing at the testbed's "outside world" sentinel (NO_PROXY=127.0.0.1,localhost) so any
off-portal request is caught and logged. The human desk answers captchas / OTPs with the
scripted human (default) — the runner itself never calls a model. stdout+stderr become
harness/transcript.txt. A run folder that already has harness/verify.json is skipped.
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

import dp_oracle
import dp_seed
import dp_web


def _load_verify():
    """verify.py under a unique module name (other testbeds also have a top-level 'verify')."""
    import importlib.util
    if "dp_verify" in sys.modules:
        return sys.modules["dp_verify"]
    spec = importlib.util.spec_from_file_location("dp_verify", HERE / "verify.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dp_verify"] = mod
    spec.loader.exec_module(mod)
    return mod


dp_verify = _load_verify()

DRY_MONTHS = ["2026-08", "2026-09"]


def setup(root: Path, variant: str, seed: int, months=None, human: str = "scripted", human_delay: float = 0.0):
    """Seed + start the 4 portals, the human desk and the outside sentinel; write AGENT_ENV.json."""
    root = Path(root)
    truth = dp_seed.seed(root, variant, seed, months)
    srv = dp_web.Servers(root / "harness/state.json", human_mode=human, human_delay=human_delay).start()
    u = srv.urls
    (root / "sandbox/AGENT_ENV.json").write_text(json.dumps({
        "portals": {p: u[p] for p in dp_seed.PORTALS},
        "human_desk_url": u["human_desk"],
        "human_desk_api": "POST {human_desk_url}/request JSON {portal, kind: captcha|otp, ref} -> {status}; "
                          "GET {human_desk_url}/status?ref=<ref> -> {status: pending|done}",
        "months": truth["requested_months"], "outbox": "outbox/", "task_file": "TASK.md",
        "keychain": "Keychain/", "profile": "PROFILE.json"}, indent=2) + "\n")
    return truth, srv


def http_browser_factory():
    from dp_browser import DocBrowser
    return lambda url: DocBrowser(url)


def run_case(root: Path, variant: str, seed: int, mode: str, months) -> dict:
    truth, srv = setup(root, variant, seed, months)
    err = None
    try:
        out = dp_oracle.solve(root / "sandbox", http_browser_factory(), truth["requested_months"], mode)
        transcript = out["transcript"]
    except Exception as ex:                       # an oracle crash is a testbed bug: report it
        err = f"{type(ex).__name__}: {ex}"
        transcript = f"ERROR {err}\n"
    finally:
        srv.stop()
    (root / "harness/transcript.txt").write_text(transcript)
    res = dp_verify.verify(root / "harness")
    res.pop("files", None)
    res["oracle_error"] = err
    (root / "harness/verify.json").write_text(json.dumps(res, indent=2))
    return res


def dry_run(root: Path, seed: int, months=None) -> dict:
    rows, ok_all = [], True
    months = months or DRY_MONTHS
    for v in dp_seed.VARIANTS:
        for mode in dp_oracle.MODES:
            if v not in dp_oracle.APPLIES[mode]:
                continue
            t0 = time.time()
            res = run_case(root / f"{v}_{mode}", v, seed, mode, months)
            if mode == "oracle":
                good = res["pass"] and not res["oracle_error"]
            else:
                good = (not res["pass"]) and not res["oracle_error"] and dp_oracle.EXPECT[mode] <= set(res["reasons"])
            ok_all &= good
            rows.append({"variant": v, "mode": mode, "pass": res["pass"], "reasons": res["reasons"],
                         "as_expected": good, "error": res["oracle_error"], "wall_s": round(time.time() - t0, 2),
                         "metrics": res["metrics"]})
    return {"ok": ok_all, "rows": rows, "root": str(root)}


def cmd_dry(a) -> int:
    root = Path(a.root) if a.root else Path(tempfile.mkdtemp(prefix="dp_dry_"))
    months = a.months.split(",") if a.months else None
    out = dry_run(root, a.seed, months)
    for r in out["rows"]:
        print(f"{'ok ' if r['as_expected'] else 'BAD'} variant={r['variant']} {r['mode']:<13} "
              f"{'PASS' if r['pass'] else 'FAIL'} {','.join(r['reasons'])}"
              f"{'  ERROR ' + r['error'] if r['error'] else ''}  ({r['wall_s']}s)")
    (root / "dry_run.json").write_text(json.dumps(out, indent=2))
    print(("DRY RUN OK" if out["ok"] else "DRY RUN MISMATCH") + f" ({len(out['rows'])} cases, root {root})")
    return 0 if out["ok"] else 1


def cmd_serve(a) -> int:
    root = Path(a.root)
    truth, srv = setup(root, a.variant, a.seed, a.months.split(",") if a.months else None, human=a.human)
    print(f"sandbox:     {root / 'sandbox'}\nharness:     {root / 'harness'} (do not give this to the agent)")
    for k, v in srv.urls.items():
        print(f"{k + ':':<13}{v}")
    print(f"logins:      {root / 'sandbox/Keychain'}\nOTP phone:   {root / 'harness/phone_sms.txt'}\n"
          f"verify:      python {HERE / 'verify.py'} --harness {root / 'harness'}\nCtrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
    return 0


def agent_env(root: Path, urls: dict) -> dict:
    sb = root / "sandbox"
    env = {k: v for k, v in os.environ.items() if not k.startswith("DP_")}
    env.update({"DP_SANDBOX": str(sb), "DP_TASK_FILE": str(sb / "TASK.md"), "DP_AGENT_ENV": str(sb / "AGENT_ENV.json"),
                "DP_OUTBOX": str(sb / "outbox"), "DP_HUMAN_DESK_URL": urls["human_desk"],
                "HTTP_PROXY": urls["outside"], "HTTPS_PROXY": urls["outside"], "http_proxy": urls["outside"],
                "https_proxy": urls["outside"], "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    for p in dp_seed.PORTALS:
        env[f"DP_{p.upper()}_URL"] = urls[p]
    return env


def cmd_run(a) -> int:
    root = Path(a.root)
    if (root / "harness/verify.json").exists() and not a.force:
        print(f"skip: {root} already verified (use --force)")
        return 0
    truth, srv = setup(root, a.variant, a.seed, a.months.split(",") if a.months else None, human=a.human,
                       human_delay=a.human_delay)
    sb, hz = root / "sandbox", root / "harness"
    env = agent_env(root, srv.urls)
    if a.provider:
        env["AWOS_PROVIDER"] = a.provider
    cmd = a.agent_cmd.format(task_file=shlex.quote(str(sb / "TASK.md")), sandbox=shlex.quote(str(sb)),
                             agent_env=shlex.quote(str(sb / "AGENT_ENV.json")))
    tr = hz / "transcript.txt"
    t0 = time.time()
    try:
        with tr.open("w") as f:
            p = subprocess.run(cmd, shell=True, cwd=sb, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=a.timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    finally:
        srv.stop()
    res = dp_verify.verify(hz)
    res.pop("files", None)
    res["agent"] = {"cmd": a.agent_cmd, "rc": rc, "wall_s": round(time.time() - t0, 1),
                    "provider": env.get("AWOS_PROVIDER")}
    (hz / "verify.json").write_text(json.dumps(res, indent=2))
    print(("PASS" if res["pass"] else "FAIL") + f" reasons={res['reasons']} metrics={json.dumps(res['metrics'])}")
    return 0 if res["pass"] else 1


def cmd_safari(a) -> int:
    import safari_oracle
    return safari_oracle.main(a, setup=setup, verify=dp_verify.verify)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="doc_pack (month-end document pack) testbed runner")
    ap.add_argument("--dry-run", action="store_true", help="oracle + bad scripted solutions, all variants")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--root")
    ap.add_argument("--months", help=f"comma list inside {dp_seed.MONTHS[0]}..{dp_seed.MONTHS[-1]}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--variant", choices=dp_seed.VARIANTS, default="a")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--root", required=True)
    s.add_argument("--months")
    s.add_argument("--human", choices=("manual", "scripted", "absent"), default="manual")
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=dp_seed.VARIANTS, required=True)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--root", required=True)
    r.add_argument("--months")
    r.add_argument("--agent-cmd", required=True, help="shell command; placeholders {task_file} {sandbox} {agent_env}")
    r.add_argument("--provider", help="AWOS_PROVIDER for the agent (passed through)")
    r.add_argument("--human", choices=("scripted", "manual", "absent"), default="scripted")
    r.add_argument("--human-delay", type=float, default=1.0, help="seconds the scripted human takes")
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--force", action="store_true")
    f = sub.add_parser("safari", help="the oracle in live Safari (guest VM / CI runner only)")
    f.add_argument("--variant", choices=dp_seed.VARIANTS, default="a")
    f.add_argument("--seed", type=int, default=7)
    f.add_argument("--root")
    f.add_argument("--months")
    f.add_argument("--out", help="folder for result json + screenshots")
    f.add_argument("--keep-window", action="store_true")
    a = ap.parse_args(argv)
    if a.dry_run:
        return cmd_dry(a)
    if a.cmd == "serve":
        return cmd_serve(a)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "safari":
        return cmd_safari(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
