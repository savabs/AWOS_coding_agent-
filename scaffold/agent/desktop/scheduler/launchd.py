"""launchd.py: a per-user LaunchAgent plist for the scheduler. Generated, never installed.

One agent, ``com.awos.scheduler``, runs ``python -m scaffold.agent.desktop.scheduler
run-due`` at every job's calendar slot (StartCalendarInterval) plus the digest
time, and every N minutes when an "every N" job exists (StartInterval). run-due
itself decides what is due, so an extra wake-up is harmless.

launchd behaviour this relies on (documented in launchd.plist(5)):
  * a StartCalendarInterval fire missed while the Mac slept is delivered ONCE on
    wake (several missed fires coalesce), which matches run-due's catch-up-once;
  * RunAtLoad false: installing or logging in does not start a run;
  * ProcessType Background + LowPriorityIO + Nice: throttled CPU and I/O, so
    the owner's foreground work keeps priority.

This module only builds text. ``install_commands`` returns the commands for
the owner to run; nothing here calls launchctl.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .jobs import Config

LABEL = "com.awos.scheduler"
REPO = Path(__file__).resolve().parents[4]


def calendar_entries(cfg: Config) -> List[Dict[str, int]]:
    """StartCalendarInterval dicts (launchd Weekday: 0/7 = Sunday, 1 = Monday)."""
    seen, out = set(), []

    def add(d: Dict[str, int]) -> None:
        k = tuple(sorted(d.items()))
        if k not in seen:
            seen.add(k)
            out.append(d)
    for j in cfg.jobs:
        s = j.schedule
        if not j.enabled or s.kind != "calendar":
            continue
        if len(s.days) == 7:
            add({"Hour": s.at.hour, "Minute": s.at.minute})
        else:
            for d in s.days:
                add({"Weekday": (d + 1) % 7, "Hour": s.at.hour, "Minute": s.at.minute})
    if cfg.digest_at:
        add({"Hour": cfg.digest_at.hour, "Minute": cfg.digest_at.minute})
    return out


def start_interval(cfg: Config) -> Optional[int]:
    every = [j.schedule.every_s for j in cfg.jobs if j.enabled and j.schedule.kind == "every"]
    return min(every) if every else None


def plist_dict(cfg: Config, python: Optional[str] = None, jobs_file: Optional[str] = None,
               repo: Optional[Path] = None) -> dict:
    repo = Path(repo or REPO)
    home = Path(cfg.home)
    args = [python or sys.executable, "-m", "scaffold.agent.desktop.scheduler"]
    if jobs_file or cfg.path:
        args += ["--jobs", str(jobs_file or cfg.path)]
    args += ["--home", str(home), "run-due"]
    d = {
        "Label": LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {"PYTHONPATH": str(repo), "NO_PROXY": "127.0.0.1,localhost",
                                 "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "LowPriorityBackgroundIO": True,
        "Nice": 10,
        "StandardOutPath": str(home / "launchd.out.log"),
        "StandardErrorPath": str(home / "launchd.err.log"),
        "ExitTimeOut": 30,
    }
    cal = calendar_entries(cfg)
    if cal:
        d["StartCalendarInterval"] = cal
    iv = start_interval(cfg)
    if iv:
        d["StartInterval"] = iv
    return d


def plist_bytes(cfg: Config, **kw) -> bytes:
    return plistlib.dumps(plist_dict(cfg, **kw), fmt=plistlib.FMT_XML, sort_keys=True)


def agent_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"


def install_commands(plist_file: str) -> Dict[str, List[str]]:
    dst = "~/Library/LaunchAgents/" + LABEL + ".plist"
    q = shlex.quote(plist_file)
    return {
        "install": [
            "mkdir -p ~/Library/LaunchAgents",
            f"plutil -lint {q}",
            f"cp {q} {dst}",
            f"launchctl bootstrap gui/$(id -u) {dst}",
            f"launchctl print gui/$(id -u)/{LABEL} | head -20   # check it is loaded",
        ],
        "run_once_now": [f"launchctl kickstart gui/$(id -u)/{LABEL}"],
        "uninstall": [
            f"launchctl bootout gui/$(id -u)/{LABEL}",
            f"rm {dst}",
        ],
    }


def render_instructions(plist_file: str) -> str:
    c = install_commands(plist_file)
    lines = [f"Plist written to {plist_file}. Nothing was installed.", "",
             "To enable (run these yourself):"]
    lines += ["  " + x for x in c["install"]]
    lines += ["", "To trigger one run now:"] + ["  " + x for x in c["run_once_now"]]
    lines += ["", "To disable:"] + ["  " + x for x in c["uninstall"]]
    return "\n".join(lines) + "\n"


def default_python() -> str:
    return os.environ.get("AWOS_SCHEDULER_PYTHON") or sys.executable
