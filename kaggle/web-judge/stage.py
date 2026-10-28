"""
stage.py — build the two folders the WebJudge-7B Kaggle run needs. No network, no credentials, never pushes.

    python kaggle/web-judge/stage.py --out stage-wj/ --runs A=runs/web/A B=runs/web/B [--levels FILE] \
        [--user U] [--set max_model_len=32768 ...]

    stage-wj/dataset/   web_judge_src.tar.gz (scaffold/rl/web_judge + scripts/web/judge.py from the working tree),
                        runs/<label>.tar.gz (each run's judge-layout task folders), judge_config.json,
                        levels.json (optional), dataset-metadata.json (KAGGLE_USER/awos-web-judge-data, private)
    stage-wj/kernel/    run.py, kernel-metadata.json (GPU T4 x2, internet on for the HF download, private)
Every run is validated with scaffold.rl.web_judge.layout.find_episodes before anything is written.
Then the owner runs (printed at the end, not executed):
    kaggle datasets create -p stage-wj/dataset   (or: kaggle datasets version -p ... -m "...")
    kaggle kernels push -p stage-wj/kernel
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATASET_SLUG = "awos-web-judge-data"
SRC_PATHS = ["scaffold/rl/__init__.py", "scaffold/rl/web_judge", "scripts/web/judge.py"]


def _val(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def build_config(sets) -> dict:
    cfg = json.loads((HERE / "judge_config.json").read_text())
    for s in sets:
        k, sep, v = s.partition("=")
        if not sep or k.strip() not in cfg:
            raise SystemExit(f"--set wants KEY=V with KEY in judge_config.json, got {s!r}")
        cfg[k.strip()] = _val(v)
    return cfg


def _skip_cache(ti: tarfile.TarInfo):
    return None if "__pycache__" in ti.name or ti.name.endswith(".pyc") else ti


def stage(out: Path, runs, user: str = "KAGGLE_USER", sets=(), levels=None) -> dict:
    sys.path.insert(0, str(REPO))
    from scaffold.rl.web_judge.layout import find_episodes
    counts = {}
    for label, path in runs:
        counts[label] = len(find_episodes(Path(path)))          # raises LayoutError on a bad run
        if not counts[label]:
            raise SystemExit(f"run {label}: no result.json under {path}")
    cfg = build_config(sets)
    ds, kd = out / "dataset", out / "kernel"
    for d in (ds, kd):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    with tarfile.open(ds / "web_judge_src.tar.gz", "w:gz") as tf:
        for p in SRC_PATHS:
            tf.add(REPO / p, arcname=p, filter=_skip_cache)
    (ds / "runs").mkdir()
    for label, path in runs:
        with tarfile.open(ds / "runs" / f"{label}.tar.gz", "w:gz") as tf:
            tf.add(Path(path), arcname=".", filter=_skip_cache)
    if levels:
        shutil.copy(levels, ds / "levels.json")
    (ds / "judge_config.json").write_text(json.dumps(cfg, indent=1) + "\n")
    (ds / "dataset-metadata.json").write_text(json.dumps({"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}",
                                                          "licenses": [{"name": "other"}], "isPrivate": True}, indent=1) + "\n")
    shutil.copy(HERE / "run.py", kd / "run.py")
    meta = json.loads((HERE / "kernel-metadata.json").read_text().replace("KAGGLE_USER", user))
    (kd / "kernel-metadata.json").write_text(json.dumps(meta, indent=1) + "\n")
    return {"out": str(out), "episodes": counts, "config": cfg}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kaggle/web-judge/stage.py")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--runs", nargs="+", required=True, help="LABEL=DIR (judge layout)")
    ap.add_argument("--levels", type=Path, default=None)
    ap.add_argument("--user", default="KAGGLE_USER")
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args(argv)
    runs = []
    for s in a.runs:
        label, sep, path = s.partition("=")
        runs.append((label, path) if sep else (Path(s).name, s))
    info = stage(a.out, runs, a.user, a.set, a.levels)
    print(f"staged {a.out}: {info['episodes']}\nnext (owner, with KAGGLE credentials in the environment):\n"
          f"  kaggle datasets create -p {a.out}/dataset --dir-mode tar   # or: kaggle datasets version -p ... -m update\n"
          f"  kaggle kernels push -p {a.out}/kernel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
