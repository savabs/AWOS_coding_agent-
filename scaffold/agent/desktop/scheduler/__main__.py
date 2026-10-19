"""CLI: python -m scaffold.agent.desktop.scheduler {list,run,run-due,digest,plist,status}

    list                  the jobs in jobs.toml and when each is next due
    run <job> [--force]   run one job now (--force skips admission: AC/idle/quiet hours/lease)
    run-due               resume interrupted runs, run every due job once, write the digest
                          when its time has come (this is what launchd calls)
    digest [--notify]     write the morning digest now; prints the plain-text summary
    plist [--out FILE]    write the LaunchAgent plist and print install/uninstall commands
                          (never installs anything)
    status                state, pending approvals, last runs (JSON)

Global: --jobs FILE (default ~/.awos/jobs.toml or $AWOS_JOBS_FILE),
        --home DIR  (default [scheduler] home or ~/.awos/scheduler or $AWOS_SCHEDULER_HOME)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import digest as digest_mod
from . import launchd
from .jobs import JobError, load_config, slots_between
from .runner import Scheduler, utcnow


def _next_slot(job, now, tz):
    from datetime import timedelta
    s = slots_between(job.schedule, now, now + timedelta(days=8), tz)
    return s[0] if s else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.agent.desktop.scheduler",
                                 description="AWOS local scheduler + morning digest")
    ap.add_argument("--jobs", help="jobs.toml path")
    ap.add_argument("--home", help="scheduler state dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("job")
    r.add_argument("--force", action="store_true", help="skip admission checks (not the model lock)")
    sub.add_parser("run-due")
    d = sub.add_parser("digest")
    d.add_argument("--notify", action="store_true", help="also show a macOS notification (counts only)")
    d.add_argument("--out", help="output dir (default <home>/digest)")
    p = sub.add_parser("plist")
    p.add_argument("--out", help="write the plist here (default <home>/com.awos.scheduler.plist)")
    p.add_argument("--python", help="interpreter launchd should use (default: this one)")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.jobs, a.home)
    except JobError as e:
        print(f"jobs file error: {e}", file=sys.stderr)
        return 2
    sched = Scheduler(cfg)
    now = utcnow()
    if a.cmd == "list":
        if not cfg.jobs:
            print(f"no jobs in {cfg.path}")
        for j in cfg.jobs:
            nxt = _next_slot(j, now, cfg.tz)
            when = f"{nxt.astimezone(cfg.tz):%a %Y-%m-%d %H:%M %Z}" if nxt else "-"
            print(f"{j.name:24} {j.kind:13} {str(j.schedule):18} model={'yes' if j.uses_model else 'no':3} "
                  f"writes={j.writes:9} {'' if j.enabled else '(disabled) '}next={when}")
        if cfg.digest_at:
            print(f"{'(digest)':24} {'':13} daily {cfg.digest_at:%H:%M}")
        return 0
    if a.cmd == "run":
        try:
            job = cfg.job(a.job)
        except JobError as e:
            print(e, file=sys.stderr)
            return 2
        sched.note_jobs(now)
        rec = sched.run_job(job, slot=None, now=now, force=a.force)
        print(json.dumps(rec, indent=1))
        return 0 if rec["outcome"] in ("VERIFIED", "COMPLETED_UNVERIFIED", "DUPLICATE") else 1
    if a.cmd == "run-due":
        recs = sched.run_due(now)
        for rec in recs:
            print(json.dumps({k: rec.get(k) for k in ("run_id", "outcome", "reason", "resumed")}))
        slot = digest_mod.digest_due(sched, now)
        if slot is not None:
            out = digest_mod.write_digest(sched, now, do_notify=cfg.digest_notify, slot=slot)
            print(json.dumps({"digest": out["html"], "counts": out["counts"]}))
        return 0
    if a.cmd == "digest":
        out = digest_mod.write_digest(sched, now, Path(a.out) if a.out else None, do_notify=a.notify)
        print(out["summary"])
        print(f"HTML: {out['html']}")
        return 0
    if a.cmd == "plist":
        dst = Path(a.out) if a.out else cfg.home / f"{launchd.LABEL}.plist"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(launchd.plist_bytes(cfg, python=a.python or launchd.default_python(),
                                            jobs_file=str(cfg.path) if cfg.path else None))
        print(launchd.render_instructions(str(dst)))
        return 0
    if a.cmd == "status":
        print(json.dumps(sched.status(), indent=1, default=str))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
