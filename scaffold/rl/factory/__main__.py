"""
python -m scaffold.rl.factory list   [--split train|ood|ood300] [--family F] [--kind K] [--tag T]
python -m scaffold.rl.factory stats  [--json]
python -m scaffold.rl.factory export --out tasks.jsonl [--split S] [--no-spec]
python -m scaffold.rl.factory serve  [--task ID ...] [--port N]     (prints start URLs; Ctrl-C to stop)
python -m scaffold.rl.factory check  [--family F] [--split S]     (replay every oracle over HTTP)
"""
from __future__ import annotations

import argparse
import json
import sys
import time


def _filter(tasks, a):
    out = []
    for t in tasks:
        if getattr(a, "family", None) and t.family != a.family:
            continue
        if getattr(a, "kind", None) and t.kind != a.kind:
            continue
        if getattr(a, "tag", None) and a.tag not in t.tags:
            continue
        out.append(t)
    return out


def check(src, tasks) -> dict:
    from .catalog import oracle_traj
    from .htmldriver import SimBrowser
    from .server import http_transport
    src.start()
    ok, bad = 0, {}
    for t in tasks:
        try:
            br = SimBrowser(http_transport())
            br.open(src.reset(t))
            steps = src.oracle(t)
            for s in steps:
                br.run_step(s)
            sc = src.outcome(t, oracle_traj(t.id, steps))
            if sc.success and not sc.unsafe:
                ok += 1
            else:
                bad[t.id] = sc.notes
        except Exception as e:
            bad[t.id] = f"{type(e).__name__}: {e}"
    return {"checked": len(tasks), "admitted": ok, "failed": bad}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.factory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--split")
    p.add_argument("--family")
    p.add_argument("--kind")
    p.add_argument("--tag")
    p = sub.add_parser("stats")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("export")
    p.add_argument("--out", required=True)
    p.add_argument("--split")
    p.add_argument("--no-spec", action="store_true", help="drop meta.app_spec (smaller file)")
    p = sub.add_parser("serve")
    p.add_argument("--task", action="append", default=[])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("check")
    p.add_argument("--family")
    p.add_argument("--split")
    a = ap.parse_args(argv)

    from .source import FactorySource
    src = FactorySource()
    if a.cmd == "list":
        for t in _filter(src.tasks(a.split), a):
            print(f"{t.id}\t{t.split}\t{t.kind}\t{','.join(t.tags)}\t{t.goal}")
    elif a.cmd == "stats":
        st = src.stats()
        if a.json:
            print(json.dumps(st, indent=2))
        else:
            print(f"generated {st['generated']}  admitted {st['admitted']}  rejected {len(st['rejected'])}")
            print(f"component types: {st['component_types']}")
            for k in ("split", "kind", "group", "family", "tag", "aria", "theme", "domain"):
                print(f"{k:7s} " + "  ".join(f"{x}={n}" for x, n in st[k].items()))
            for tid, why in st["rejected"].items():
                print(f"REJECTED {tid}: {why}")
    elif a.cmd == "export":
        n = 0
        with open(a.out, "w") as f:
            for t in src.tasks(a.split):
                d = t.to_dict()
                d["oracle"] = src.oracle(t)
                if a.no_spec:
                    d["meta"].pop("app_spec", None)
                f.write(json.dumps(d) + "\n")
                n += 1
        print(f"wrote {n} tasks to {a.out}")
    elif a.cmd == "serve":
        from .server import FactoryServer
        src.server = FactoryServer(src.reg, port=a.port)
        src.base = src.server.start()
        ids = a.task or [t.id for t in src.tasks()][:a.limit]
        for tid in ids:
            t = src.catalog.entry(tid).task
            print(f"{tid}\t{src.reset(t)}\t{t.goal}")
        print(f"serving on {src.base} (Ctrl-C to stop)", file=sys.stderr)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        src.stop()
    elif a.cmd == "check":
        res = check(src, _filter(src.tasks(a.split), a))
        src.stop()
        print(json.dumps(res, indent=2))
        return 0 if not res["failed"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
