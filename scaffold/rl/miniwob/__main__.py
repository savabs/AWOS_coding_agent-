"""
python -m scaffold.rl.miniwob <command>

  list [--split train|holdout]      included types with family/split, then excluded + reason
  holdout                           (re)write data/holdout.json from catalog.compute_holdout()
  utterances [--seeds 0-9] [--types a,b] [--out PATH]
                                    render pages in Chromium and record exact utterances
                                    (default out: data/utterances.json for the eval seeds,
                                    else the cache file ~/.cache/awos-rl/miniwob/<sha>/utterances.json)
  snapshot TYPE [--seed N] [--no-aria]   print the accessibility snapshot of one page
  oracle-check [--types ...] [--seeds ...]  run the oracle types through Playwright
  serve [--port N]                  serve the tasks for manual inspection
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import catalog


def _seeds(s: str):
    out = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.miniwob")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--split", choices=["train", "holdout"])
    sub.add_parser("holdout")
    p = sub.add_parser("utterances")
    p.add_argument("--seeds", default="0-9")
    p.add_argument("--types", default="")
    p.add_argument("--out", default="")
    p = sub.add_parser("snapshot")
    p.add_argument("type")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--no-aria", action="store_true")
    p = sub.add_parser("oracle-check")
    p.add_argument("--types", default="")
    p.add_argument("--seeds", default="0-4")
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)

    if a.cmd == "list":
        held = set(catalog.frozen_holdout())
        for n, t in sorted(catalog.INCLUDED.items()):
            split = "holdout" if n in held else "train"
            if a.split and split != a.split:
                continue
            print(f"{n:32s} {t.family:20s} {split:8s} {','.join(t.tags)}")
        if not a.split:
            print()
            for n, why in sorted(catalog.EXCLUDED.items()):
                print(f"EXCLUDED {n:28s} {why}")
        return 0
    if a.cmd == "holdout":
        path = catalog.DATA_DIR / "holdout.json"
        path.write_text(json.dumps(catalog.holdout_record(), indent=1) + "\n")
        print(path)
        return 0
    if a.cmd == "utterances":
        from .render import compute_utterances, write_utterances
        types = [t for t in a.types.split(",") if t] or sorted(catalog.INCLUDED)
        seeds = _seeds(a.seeds)
        utt = compute_utterances(types, seeds, progress=True)
        if a.out:
            out = Path(a.out)
        elif set(seeds) <= set(catalog.EVAL_SEEDS):
            out = catalog.DATA_DIR / "utterances.json"
        else:
            from .source import default_html_root
            out = default_html_root().parent / "utterances.json"
        if out.is_file():
            old = json.loads(out.read_text()).get("utterances", {})
            old.update(utt)
            utt = old
        write_utterances(out, utt)
        print(out, len(utt))
        return 0
    if a.cmd == "snapshot":
        from .render import aria_snapshot
        print(aria_snapshot(a.type, a.seed, aria_patch=not a.no_aria))
        return 0
    if a.cmd == "oracle-check":
        from .oracles import ORACLES
        from .render import run_oracle
        types = [t for t in a.types.split(",") if t] or sorted(ORACLES)
        bad = 0
        for t in types:
            for s in _seeds(a.seeds):
                r = run_oracle(t, s)
                ok = r["success"]
                bad += not ok
                print(f"{'PASS' if ok else 'FAIL'} {t}/s{s} {r['notes']}")
        return 1 if bad else 0
    if a.cmd == "serve":
        from .source import MiniWobSource
        src = MiniWobSource(port=a.port)
        base = src.start()
        print(f"serving {src.html_root} at {base}  e.g. {src.reset(src.task('click-button', 0))}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            src.stop()
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
