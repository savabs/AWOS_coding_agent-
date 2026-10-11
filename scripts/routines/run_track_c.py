#!/usr/bin/env python3
"""Resumable driver for Track C (docs/specs/ablation_learning_over_use.md).

Runs job_series once per (repeat, series) — repeat 1 over all six series, then
repeats 2 and 3 — arms off,learn interleaved, each launch with its own fresh
state dir (one job_series timestamp = one repeat of one series). Before each
new launch the budget stop is applied: if this track's cumulative spend (every
results file under --out, failed attempts included) is >= --stop-usd, stop.

Resumable: progress.json maps "r<k>/<series>" -> job_series timestamp. A
launch whose results file is complete is skipped; one left partial (killed
runner) is continued with job_series --resume for the jobs it has no result
for (same state dir, same notebooks).

    caffeinate -i python scripts/routines/run_track_c.py --out runs/track_c/main
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
FAMILIES = ["f01", "f03", "f04", "f07", "f08", "f09"]


def spend(out: Path) -> float:
    total = 0.0
    for p in out.glob("job_series_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in data.get("results") or []:
            total += float(r.get("billed_usd") or r.get("cost_usd") or 0)
            total += sum(float(f.get("cost_usd") or 0) for f in (r.get("failed_attempts") or []))
    return total


def n_jobs(series_root: Path, series: str) -> int:
    return len([d for d in (series_root / series / "jobs").iterdir() if d.is_dir()])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--series-root", type=Path, default=REPO / "runs/track_c/series")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--stop-usd", type=float, default=2.50)
    ap.add_argument("--extra-spend", type=float, default=0.0,
                    help="track spend outside --out (pilot), counted toward the stop")
    a = ap.parse_args()
    out, sroot = a.out.resolve(), a.series_root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    prog_path = out / "progress.json"
    prog = json.loads(prog_path.read_text()) if prog_path.exists() else {}
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    for rep in range(1, a.repeats + 1):
        for fam in FAMILIES:
            series, key = f"learn_{fam}", f"r{rep}/learn_{fam}"
            want = 2 * n_jobs(sroot, series)
            ts = prog.get(key)
            done_rows = 0
            if ts:
                try:
                    d = json.loads((out / f"job_series_{ts}.json").read_text())
                    done_rows = len(d.get("results") or [])
                    if not d.get("partial") and done_rows >= want:
                        continue
                except (OSError, ValueError):
                    pass
            s = spend(out) + a.extra_spend
            if s >= a.stop_usd:
                print(f"[track_c] BUDGET STOP before {key}: spend ${s:.4f} >= ${a.stop_usd}", flush=True)
                prog["_stopped"] = {"before": key, "spend": s, "at": time.strftime("%Y%m%dT%H%M%S")}
                prog_path.write_text(json.dumps(prog, indent=2))
                return 0
            cmd = [PY, str(REPO / "scripts/job_series.py"), "run", "--series", series,
                   "--arms", "off,learn", "--series-root", str(sroot), "--out-root", str(out)]
            if ts:
                d = json.loads((out / f"job_series_{ts}.json").read_text())
                have = {}
                for r in d.get("results") or []:
                    have.setdefault(r["job"], set()).add(r["arm"])
                todo = [n for n in range(1, n_jobs(sroot, series) + 1)
                        if have.get(n) != {"off", "learn"}]
                cmd += ["--resume", ts, "--jobs", ",".join(map(str, todo))]
                print(f"[track_c] resuming {key} ts={ts} jobs={todo} (spend ${s:.4f})", flush=True)
            else:
                ts = time.strftime("%Y%m%dT%H%M%S")
                # job_series picks its own ts from the clock; record the one it creates.
                before = set(out.glob("job_series_*.json"))
                print(f"[track_c] launching {key} (spend so far ${s:.4f})", flush=True)
            log = (out / f"{key.replace('/', '_')}.log").open("a")
            proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            recorded = key in prog
            for line in proc.stdout:
                log.write(line)
                if line.startswith("[learn j") or line.startswith("[off j"):
                    if (" SOLVED" in line or " FAILED" in line or " INVALID" in line
                            or "UNSOLVED" in line):
                        print(line.rstrip(), flush=True)
                if not recorded:
                    new = set(out.glob("job_series_*.json")) - before
                    if new:
                        prog[key] = sorted(new)[-1].name[len("job_series_"):-len(".json")]
                        prog_path.write_text(json.dumps(prog, indent=2))
                        recorded = True
            proc.wait()
            log.close()
            if not recorded:
                new = set(out.glob("job_series_*.json")) - before
                if new:
                    prog[key] = sorted(new)[-1].name[len("job_series_"):-len(".json")]
                    prog_path.write_text(json.dumps(prog, indent=2))
            print(f"[track_c] {key} exit {proc.returncode}; spend ${spend(out) + a.extra_spend:.4f}",
                  flush=True)
            if proc.returncode not in (0,):
                print(f"[track_c] non-zero exit for {key}; stopping for inspection", flush=True)
                return proc.returncode
    print(f"[track_c] all launches complete; spend ${spend(out) + a.extra_spend:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
