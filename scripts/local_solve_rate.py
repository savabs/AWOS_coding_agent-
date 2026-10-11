#!/usr/bin/env python3
"""local_solve_rate.py — run single-job real issues fully local, resumably.

Pre-registered in docs/specs/local_model_reality_2026-10.md (Part 1).

For each series under --series-root it runs
    job_series.py run --series <s> --arms off --jobs 1
with AWOS_PROVIDER=local, a keyless env file (so no call can reach a cloud
provider: a leak fails instead of spending) and the cloud keys removed from
the child's environment. A series that already has a valid result JSON in
--out-root is skipped, so the driver can be re-run after a battery stop.
It starts the local server (1 slot, script defaults) if it is not up.

    python3 scripts/local_solve_rate.py --series-root DIR --out-root DIR --env-file FILE
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
STRIP = ("KEY", "TOKEN", "SECRET")


def done(out_root: Path) -> dict[str, dict]:
    res = {}
    for f in sorted(glob.glob(str(out_root / "job_series_*.json"))):
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        for r in d.get("results", []):
            if not r.get("invalid"):
                res[d["series"]] = {**r, "_file": f}
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series-root", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--series", default=None, help="comma list (default: all under root, sorted)")
    a = ap.parse_args()
    root, out_root = Path(a.series_root), Path(a.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    names = a.series.split(",") if a.series else sorted(p.name for p in root.iterdir() if p.is_dir())
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in STRIP)}
    env.update({"AWOS_PROVIDER": "local", "AWOS_LOCAL_MODEL": "qwen3.5-9b",
                "AWOS_SERIES_MODEL": os.environ.get("AWOS_SERIES_MODEL", "claude-haiku-5-5"),
                "AWOS_LOCAL_SLOTS": "1", "PYTHONUNBUFFERED": "1"})
    st = subprocess.run(["bash", str(REPO / "scripts/local_model.sh"), "start"], env=env,
                        capture_output=True, text=True)
    print(st.stdout[-300:], st.stderr[-300:], flush=True)
    for s in names:
        if s in done(out_root):
            print(f"skip {s} (result exists)", flush=True)
            continue
        print(f"== {s} {time.strftime('%H:%M:%S')}", flush=True)
        subprocess.run([PY, str(REPO / "scripts/job_series.py"), "run", "--series", s,
                        "--arms", "off", "--jobs", "1", "--series-root", str(root),
                        "--out-root", str(out_root), "--env-file", a.env_file], env=env, cwd=REPO)
        r = done(out_root).get(s)
        print(f"-- {s}: " + (json.dumps({k: r.get(k) for k in
              ("solved", "turns", "llm_calls", "minutes", "timed_out", "cost_usd",
               "input_tokens", "output_tokens", "hidden_summary")}) if r else "no valid result"),
              flush=True)
    res = done(out_root)
    solved = sum(1 for s in names if res.get(s, {}).get("solved"))
    print(f"SOLVED {solved}/{len(names)} (valid results: {sum(1 for s in names if s in res)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
