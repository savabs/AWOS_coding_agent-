"""Run `job_series.py validate` for fresh series in parallel; one log per series in
_tools/logs/, a JSON summary in _tools/validate.json (merged across runs).

usage: validate_all.py [-j N] [series ...]      (default: every series dir with jobs/)
"""
import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build import ROOT

REPO = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_tools" / "logs"
OUT = ROOT / "_tools" / "validate.json"


def one(name):
    LOGS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "job_series.py"), "validate",
                           "--series", name, "--series-root", str(ROOT)],
                          capture_output=True, text=True, cwd=REPO)
    dt = time.time() - t0
    out = proc.stdout + proc.stderr
    (LOGS / f"validate_{name}.log").write_text(out)
    line = next((l for l in out.splitlines() if re.match(r"\s+1 ", l)), "")
    probs = [l.strip() for l in out.splitlines() if l.strip().startswith("- ")]
    return name, {"ok": proc.returncode == 0, "seconds": round(dt), "line": line.strip(),
                  "problems": probs}


ap = argparse.ArgumentParser()
ap.add_argument("-j", type=int, default=4)
ap.add_argument("--exclude", default=None, help="skip series whose name contains this")
ap.add_argument("series", nargs="*")
a = ap.parse_args()
names = a.series or sorted(p.name for p in ROOT.iterdir() if (p / "jobs").is_dir())
names = [n for n in names if not (a.exclude and a.exclude in n)]
res = json.loads(OUT.read_text()) if OUT.exists() else {}
with ThreadPoolExecutor(a.j) as ex:
    for name, r in ex.map(one, names):
        res[name] = r
        print(f"{'OK ' if r['ok'] else 'BAD'} {name:<32} {r['seconds']:>4}s {r['line'][:150]}", flush=True)
        for p in r["problems"]:
            print("      ", p[:300], flush=True)
        OUT.write_text(json.dumps(res, indent=1, sort_keys=True))
