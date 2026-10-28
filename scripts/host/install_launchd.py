#!/usr/bin/env python3
"""Generate (and only with --apply, install) the AWOS host launchd user agents.

Spec: docs/specs/host_ops.md

Default is a dry run: the plists are written under <host root>/launchd/ and the
exact launchctl commands are printed. Nothing touches ~/Library or launchctl
unless --apply is given.

Two agents:
  ai.awos.host           the worker loop; KeepAlive + RunAtLoad, so launchd
                         restarts it after a crash and starts it at login.
  ai.awos.host.watchdog  runs `watchdog check --notify` every 5 minutes.

Secrets never go into a plist. The plist only carries the *path* of an env
file (default ~/.awos/host/host.env, chmod 600) that run_host.sh sources.

Usage:
  python scripts/host/install_launchd.py              # dry run, print commands
  python scripts/host/install_launchd.py --stdout     # print the worker plist
  python scripts/host/install_launchd.py --uninstall  # print removal commands
  python scripts/host/install_launchd.py --apply      # actually install (manual only)
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "ai.awos.host"
WATCHDOG_LABEL = "ai.awos.host.watchdog"
REPO = Path(__file__).resolve().parents[2]


def host_root(root: str | None = None) -> Path:
    if root:
        return Path(root).expanduser()
    env = os.environ.get("AWOS_HOST_DIR")
    return Path(env).expanduser() if env else Path.home() / ".awos" / "host"


def default_python() -> str:
    venv = REPO / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def worker_plist(root: Path, repo: Path, python: str, env_file: Path) -> dict:
    logs = root / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(repo / "scripts" / "host" / "run_host.sh")],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {
            "AWOS_HOST_DIR": str(root),
            "AWOS_HOST_ENV_FILE": str(env_file),
            "AWOS_HOST_PYTHON": python,
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        # Restart after a crash, but not after a clean `exit 0` (e.g. stop request).
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,          # no tight crash loops
        "ProcessType": "Standard",       # long jobs: don't let launchd throttle CPU
        "StandardOutPath": str(logs / "host.out.log"),
        "StandardErrorPath": str(logs / "host.err.log"),
    }


def watchdog_plist(root: Path, repo: Path, python: str) -> dict:
    logs = root / "logs"
    return {
        "Label": WATCHDOG_LABEL,
        "ProgramArguments": [python, "-m", "scaffold.agent.host.watchdog",
                             "--root", str(root), "check", "--notify"],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {"AWOS_HOST_DIR": str(root)},
        "StartInterval": 300,
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "watchdog.out.log"),
        "StandardErrorPath": str(logs / "watchdog.err.log"),
    }


WORKER_MODULE = "scaffold.agent.host.worker"


def worker_available(repo: Path) -> bool:
    """True when the default worker entry point exists in `repo`.

    The worker plist has KeepAlive (SuccessfulExit=false): installing it while
    `python -m scaffold.agent.host.worker` does not exist makes launchd restart
    a failing process every ThrottleInterval seconds, forever.
    """
    base = repo / Path(*WORKER_MODULE.split("."))
    return base.with_suffix(".py").is_file() or (base / "__main__.py").is_file()


def install_commands(agents_dir: Path, staged: list[Path]) -> list[str]:
    uid = os.getuid()
    cmds = [f"mkdir -p {shlex.quote(str(agents_dir))}"]
    for p in staged:
        dst = agents_dir / p.name
        cmds += [f"cp {shlex.quote(str(p))} {shlex.quote(str(dst))}",
                 f"plutil -lint {shlex.quote(str(dst))}",
                 f"launchctl bootstrap gui/{uid} {shlex.quote(str(dst))}"]
    cmds.append(f"launchctl print gui/{uid}/{LABEL} | head -20   # verify")
    return cmds


def uninstall_commands(agents_dir: Path) -> list[str]:
    uid = os.getuid()
    cmds = []
    for label in (WATCHDOG_LABEL, LABEL):
        cmds += [f"launchctl bootout gui/{uid}/{label}",
                 f"rm -f {shlex.quote(str(agents_dir / (label + '.plist')))}"]
    return cmds


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="host dir (default $AWOS_HOST_DIR or ~/.awos/host)")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--python", default=None)
    ap.add_argument("--env-file", default=None, help="default <root>/host.env")
    ap.add_argument("--agents-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    ap.add_argument("--out-dir", default=None, help="where to stage plists (default <root>/launchd)")
    ap.add_argument("--no-watchdog", action="store_true")
    ap.add_argument("--stdout", action="store_true", help="print the worker plist and exit")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="really copy into ~/Library/LaunchAgents and launchctl bootstrap")
    ap.add_argument("--allow-missing-worker", action="store_true",
                    help="install even though scaffold/agent/host/worker is absent "
                         "(only when host.env sets AWOS_HOST_CMD)")
    a = ap.parse_args(argv)

    root = host_root(a.root)
    repo = Path(a.repo).resolve()
    python = a.python or default_python()
    env_file = Path(a.env_file).expanduser() if a.env_file else root / "host.env"
    agents_dir = Path(a.agents_dir).expanduser()

    if a.uninstall:
        cmds = uninstall_commands(agents_dir)
        if a.apply:
            for c in cmds:
                subprocess.run(c, shell=True, check=False)
        else:
            print("# dry run — run these to uninstall:")
            print("\n".join(cmds))
        return 0

    plists = {LABEL: worker_plist(root, repo, python, env_file)}
    if not a.no_watchdog:
        plists[WATCHDOG_LABEL] = watchdog_plist(root, repo, python)

    if a.stdout:
        sys.stdout.write(plistlib.dumps(plists[LABEL]).decode())
        return 0

    out_dir = Path(a.out_dir).expanduser() if a.out_dir else root / "launchd"
    out_dir.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    staged = []
    for label, data in plists.items():
        p = out_dir / f"{label}.plist"
        p.write_bytes(plistlib.dumps(data))
        staged.append(p)
        print(f"wrote {p}")

    if not env_file.exists():
        print(f"\nNOTE: env file {env_file} does not exist. Create it (chmod 600) with e.g.\n"
              f"  OPENROUTER_API_KEY=...\n  AWOS_HOST_NOTIFY=osascript")

    missing_worker = not worker_available(repo)
    if missing_worker:
        print(f"\nWARNING: {WORKER_MODULE} does not exist in {repo}. With KeepAlive, "
              "launchd would restart the failing worker every 30s forever. Set "
              "AWOS_HOST_CMD in the env file and pass --allow-missing-worker, or "
              "wait for the worker module.")
    cmds = install_commands(agents_dir, staged)
    if a.apply and missing_worker and not a.allow_missing_worker:
        print("refusing --apply: worker entry point missing (see WARNING above)",
              file=sys.stderr)
        return 2
    if a.apply:
        for c in cmds:
            print(f"$ {c}")
            subprocess.run(c, shell=True, check=False)
    else:
        print("\n# dry run — nothing installed. To install, run:")
        print("\n".join(cmds))
        print("\n# To remove later:  python scripts/host/install_launchd.py --uninstall")
        print("# Keep the Mac awake on AC:  bash scripts/host/pmset_advice.sh")
    if shutil.which("plutil") is None:
        print("(plutil not found; skip the lint step)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
