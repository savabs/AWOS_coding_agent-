"""CLI: python -m scaffold.agent.desktop.pack {sources-check,run,report,zip,status}

    sources-check                     validate sources.toml and list the sources (exit 1 on problems)
    run [--month 2026-10] [--source ID] [--force] [--runner module:function]
                                      collect the month (default: last month); re-runs retry only
                                      missing / failed / needs_you sources (--force: all)
                                      --live: the live Safari runner (pack/runner.py, guest VM only);
                                      --wait-for-human [--human-timeout S]: at a login / OTP /
                                      captcha, notify inside the VM and wait for the owner
    teach --source ID [--demo-month M] [--from-trace F] [--yes]
                                      record the owner doing the job once in the VM's Safari (or take
                                      a saved demo trace) and store a replayable skill under
                                      ~/.awos/pack/skills/<ID>/vN (pack/teach.py)
    skills [--source ID]              list taught skills (version, demo month, period bound)
    report [--month M]                write report.html/.txt/.json into Pack/M/ and print the text
    zip [--month M] [--out FILE]      write the accountant's zip (report + documents not set aside)
    status [--month M]                JSON: per-month totals (or one month's sources)

Global: --sources FILE (default ~/.awos/pack/sources.toml or $AWOS_PACK_SOURCES),
        --root DIR (overrides [pack] root), --downloads DIR (overrides [pack] downloads).
The runner is ``module:function`` with the signature ``runner(source, period, ctx)``
(see pack/__init__.py); also settable via $AWOS_PACK_RUNNER. Without one, every source
with a taught skill is reported as failed ("no skill runner is wired in").
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

from .config import ConfigError, load_config
from .report import build, make_zip, render_text, write_report
from .run import MONTH_RE, PackBusy, load_state, previous_month, run_month, totals


def resolve_runner(spec: Optional[str]) -> Optional[Callable]:
    spec = spec or os.environ.get("AWOS_PACK_RUNNER")
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    if not mod or not fn:
        raise SystemExit(f"--runner must look like package.module:function, got {spec!r}")
    return getattr(importlib.import_module(mod), fn)


def cmd_teach(cfg, a) -> int:
    from .teach import TeachRefused, teach
    try:
        src = cfg.source(a.source)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    month = a.demo_month or previous_month()
    if not MONTH_RE.match(month):
        print(f"--demo-month must look like 2026-10, got {month!r}", file=sys.stderr)
        return 2
    try:
        out = teach(src, demo_month=month, trace=a.from_trace, yes=a.yes,
                    skills_root=Path(a.skills).expanduser() if a.skills else None)
    except TeachRefused as e:
        print("not taught -- " + "; ".join(e.reasons), file=sys.stderr)
        return 4
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    p = out["program"]
    print(f"taught {src.id} v{p['version']}: {out['path']}")
    print(f"  steps: {len(p['setup'])}, handlers: {len(p['handlers'])}, "
          f"sign-in / OTP / captcha steps left to you: {p['pack']['dropped_steps']}, "
          f"month bound: {p['pack']['period_bound']}")
    for t in p["pack"]["period_templates"]:
        print(f"  {t['where']}: {t['template']}")
    for w in out["warnings"]:
        print(f"  note: {w}")
    return 0


def cmd_skills(cfg, a) -> int:
    from .teach import load_skill, skills_dir
    root = Path(a.skills).expanduser() if a.skills else skills_dir()
    rows = {}
    for s in cfg.sources:
        if a.source and s.id != a.source:
            continue
        p = load_skill(s, root)
        rows[s.id] = None if p is None else {
            "version": p["version"], "demo_month": (p.get("pack") or {}).get("demo_period"),
            "period_bound": (p.get("pack") or {}).get("period_bound"), "steps": len(p.get("setup", []))}
    print(json.dumps(rows, indent=1, sort_keys=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.agent.desktop.pack",
                                 description="Month-end document pack")
    ap.add_argument("--sources")
    ap.add_argument("--root")
    ap.add_argument("--downloads")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources-check")
    r = sub.add_parser("run")
    r.add_argument("--month")
    r.add_argument("--source")
    r.add_argument("--force", action="store_true")
    r.add_argument("--runner")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--live", action="store_true", help="replay taught skills in the VM's Safari")
    r.add_argument("--wait-for-human", action="store_true")
    r.add_argument("--human-timeout", type=float, default=600.0)
    t = sub.add_parser("teach")
    t.add_argument("--source", required=True)
    t.add_argument("--demo-month", help="the month you will download in the demonstration (default: last month)")
    t.add_argument("--from-trace", help="build the skill from a saved awos.demo_trace/1 file instead of recording")
    t.add_argument("--yes", action="store_true", help="consent to recording without the prompt")
    t.add_argument("--skills", help="skills folder (default ~/.awos/pack/skills)")
    k = sub.add_parser("skills")
    k.add_argument("--source")
    k.add_argument("--skills")
    for name in ("report", "zip", "status"):
        p = sub.add_parser(name)
        p.add_argument("--month")
        if name == "zip":
            p.add_argument("--out")
    a = ap.parse_args(argv)

    try:
        cfg = load_config(a.sources, root=a.root, downloads=a.downloads)
    except ConfigError as e:
        print(f"sources file problem ({e.path}):", file=sys.stderr)
        for p in e.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    month = getattr(a, "month", None) or previous_month()
    if not MONTH_RE.match(month):
        print(f"--month must look like 2026-10, got {month!r}", file=sys.stderr)
        return 2

    if a.cmd == "sources-check":
        print(f"{cfg.path}: {len(cfg.sources)} source(s) OK; pack root {cfg.root}; downloads {cfg.downloads}")
        for s in cfg.sources:
            flag = "" if s.enabled else "  (disabled)"
            print(f"  {s.id:<24} {s.kind:<15} {s.cadence:<9} expect {s.expected.count} "
                  f"{'/'.join(s.expected.types)}  skill={s.skill or '-'}"
                  f"{'  pdf-password=keychain:' + s.password_hint if s.password_hint else ''}{flag}")
        for w in cfg.warnings:
            print(f"  warning: {w}")
        return 0
    if a.cmd == "teach":
        return cmd_teach(cfg, a)
    if a.cmd == "skills":
        return cmd_skills(cfg, a)
    if a.cmd == "run":
        try:
            if a.live:
                from .runner import LiveRunner
                runner = LiveRunner(wait_for_human=a.wait_for_human, human_timeout_s=a.human_timeout)
            else:
                runner = resolve_runner(a.runner)
        except (ImportError, AttributeError) as e:
            print(f"cannot load runner: {e}", file=sys.stderr)
            return 2
        log = (lambda s: None) if a.quiet else (lambda s: print(s, file=sys.stderr))
        try:
            st = run_month(cfg, month, runner=runner, only=a.source, force=a.force, log=log)
        except PackBusy as e:
            print(str(e), file=sys.stderr)
            return 3
        except ConfigError as e:
            print(str(e), file=sys.stderr)
            return 1
        write_report(cfg, month, st)
        print(render_text(build(cfg, month, st)), end="")
        return 0
    if a.cmd == "report":
        paths = write_report(cfg, month)
        print(render_text(build(cfg, month)), end="")
        print(f"\nwritten: {paths['html']}")
        return 0
    if a.cmd == "zip":
        res = make_zip(cfg, month, Path(a.out) if a.out else None)
        print(f"{res['path']}: {len(res['included'])} document(s)")
        for x in res["excluded"]:
            print(f"  not included: {x['path']} ({x['why']})")
        return 0
    if a.cmd == "status":
        if getattr(a, "month", None):
            st = load_state(cfg.root, month)
            print(json.dumps({"month": month, "totals": totals(st),
                              "sources": {k: {"status": v.get("status"), "reason": v.get("reason", ""),
                                              "files": len(v.get("files", []))}
                                          for k, v in st["sources"].items()}}, indent=1, sort_keys=True))
            return 0
        months = sorted(p.name for p in Path(cfg.root).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]") if p.is_dir())
        print(json.dumps({m: totals(load_state(cfg.root, m)) for m in months}, indent=1, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
