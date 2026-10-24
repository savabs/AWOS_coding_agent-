"""
stage.py — build the two folders the GRPO Kaggle run needs. No network, no credentials, never pushes.

    python kaggle/rl-grpo/stage.py --out stage-grpo/ [--user U] [--ref HEAD] [--set grpo.iterations=20 ...]

    stage-grpo/dataset/   awos_src.tar.gz (`git archive <ref>`: tracked files only), grpo_config.json,
                          dataset-metadata.json (KAGGLE_USER/awos-rl-grpo-data, private)
    stage-grpo/kernel/    run.py, kernel-metadata.json (GPU T4, internet on, private)
Then the owner runs (printed at the end, not executed):
    kaggle datasets create -p stage-grpo/dataset   (or: kaggle datasets version -p ... -m "...")
    kaggle kernels push -p stage-grpo/kernel
The config is validated with GRPOConfig (split must be "train") before anything is written.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATASET_SLUG = "awos-rl-grpo-data"


def _val(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def build_config(sets) -> dict:
    conf = json.loads((HERE / "grpo_config.json").read_text())
    for s in sets:
        k, _, v = s.partition("=")
        sect, _, key = k.strip().partition(".")
        if sect not in ("kernel", "grpo") or not key:
            raise SystemExit(f"--set wants kernel.KEY=V or grpo.KEY=V, got {s!r}")
        conf[sect][key] = _val(v)
    sys.path.insert(0, str(REPO))
    from scaffold.rl.train.grpo import GRPOConfig
    GRPOConfig.from_dict(conf["grpo"]).validate()
    return conf


def stage(out: Path, user: str, ref: str, sets) -> Path:
    conf = build_config(sets)
    ds, kd = out / "dataset", out / "kernel"
    for d in (ds, kd):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    with open(ds / "awos_src.tar.gz", "wb") as fh:
        subprocess.check_call(["git", "-C", str(REPO), "archive", "--format=tar.gz", ref], stdout=fh)
    (ds / "grpo_config.json").write_text(json.dumps(conf, indent=1) + "\n")
    (ds / "dataset-metadata.json").write_text(json.dumps({"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}",
                                                          "licenses": [{"name": "other"}], "isPrivate": True}, indent=1) + "\n")
    shutil.copy(HERE / "run.py", kd / "run.py")
    meta = json.loads((HERE / "kernel-metadata.json").read_text().replace("KAGGLE_USER", user))
    slug = conf["kernel"].get("resume_from")
    if slug:
        meta["kernel_sources"] = [slug if "/" in slug else f"{user}/{slug}"]
    (kd / "kernel-metadata.json").write_text(json.dumps(meta, indent=1) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kaggle/rl-grpo/stage.py")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--user", default="KAGGLE_USER")
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args(argv)
    out = stage(a.out, a.user, a.ref, a.set)
    print(f"staged {out}\nnext (owner, with KAGGLE credentials in the environment):\n"
          f"  kaggle datasets create -p {out}/dataset      # or: kaggle datasets version -p {out}/dataset -m update\n"
          f"  kaggle kernels push -p {out}/kernel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
