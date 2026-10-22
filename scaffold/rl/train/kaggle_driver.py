"""
kaggle_driver.py — stage, push, wait for and fetch the Kaggle SFT run (kaggle CLI 2.2.4).

    python -m scaffold.rl.train.kaggle_driver stage --data <dataset.py output> --out stage/ --size 4B \\
        [--user U] [--set epochs=1 --set lora_r=32 ...]
    python -m scaffold.rl.train.kaggle_driver push  --stage stage/ [--dry-run]
    python -m scaffold.rl.train.kaggle_driver wait  --kernel U/awos-rl-sft [--timeout-min 330]
    python -m scaffold.rl.train.kaggle_driver fetch --kernel U/awos-rl-sft --out kaggle-out/

stage builds two folders (no network, no credentials):
    stage/dataset/  train.jsonl valid.jsonl stats.json train_config.json dataset-metadata.json
    stage/kernel/   train.py (kaggle/rl-sft/train.py) kernel-metadata.json (GPU T4, internet, private)
Every row is re-checked here (defence in depth): an eval split, or a row without the rendered
prompt/completion, aborts the stage.

push: `kaggle datasets create` (first time) or `kaggle datasets version`, waits until the dataset is
ready, then `kaggle kernels push`. Credentials come only from the environment (KAGGLE_USERNAME +
KAGGLE_KEY, or KAGGLE_API_TOKEN); they are never written to disk or printed. --dry-run prints the
commands instead of running them (used by CI and tests before the account exists).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from scaffold.rl.train.dataset import EVAL_SPLITS, TRAIN_SPLITS

REPO = Path(__file__).resolve().parents[3]
KERNEL_SRC = REPO / "kaggle" / "rl-sft" / "train.py"
KERNEL_META_TEMPLATE = REPO / "kaggle" / "rl-sft" / "kernel-metadata.json"
DATASET_SLUG = "awos-rl-sft-data"
KERNEL_SLUG = "awos-rl-sft"
SIZES = {
    "2B": {"base_model": "Qwen/Qwen3.5-2B", "base_revision": "15852e8c16360a2fea060d615a32b45270f8a8fc"},
    "4B": {"base_model": "Qwen/Qwen3.5-4B", "base_revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"},
}


def _val(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def validate_rows(path: Path) -> int:
    n = 0
    for i, ln in enumerate(path.read_text().splitlines()):
        if not ln.strip():
            continue
        r = json.loads(ln)
        split = (r.get("meta") or {}).get("split")
        if split in EVAL_SPLITS or split not in TRAIN_SPLITS:
            raise SystemExit(f"{path.name}:{i + 1}: split={split!r} is not trainable; refusing to stage")
        if not r.get("prompt") or not r.get("completion") or not r["completion"].endswith("<|im_end|>"):
            raise SystemExit(f"{path.name}:{i + 1}: missing rendered prompt/completion (rebuild with dataset.py)")
        n += 1
    return n


def stage(data: Path, out: Path, size: str, user: str, overrides: dict, dataset_slug: str = DATASET_SLUG,
          kernel_slug: str = KERNEL_SLUG) -> dict:
    data, out = data.expanduser(), out.expanduser()
    if size not in SIZES:
        raise SystemExit(f"size must be one of {list(SIZES)}")
    ds, kn = out / "dataset", out / "kernel"
    for d in (ds, kn):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    counts = {}
    for name in ("train.jsonl", "valid.jsonl"):
        src = data / name
        counts[name] = validate_rows(src) if src.exists() else 0
        if src.exists():
            shutil.copy(src, ds / name)
    if not counts["train.jsonl"]:
        raise SystemExit(f"no training rows in {data}")
    if (data / "stats.json").exists():
        shutil.copy(data / "stats.json", ds / "stats.json")
    cfg = {**SIZES[size], "size": size, **overrides}
    (ds / "train_config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (ds / "dataset-metadata.json").write_text(json.dumps({
        "title": dataset_slug, "id": f"{user}/{dataset_slug}", "licenses": [{"name": "CC0-1.0"}]}, indent=2) + "\n")
    shutil.copy(KERNEL_SRC, kn / "train.py")
    meta = json.loads(KERNEL_META_TEMPLATE.read_text())
    meta.update({"id": f"{user}/{kernel_slug}", "title": kernel_slug, "dataset_sources": [f"{user}/{dataset_slug}"]})
    (kn / "kernel-metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    info = {"rows": counts, "config": cfg, "dataset": f"{user}/{dataset_slug}", "kernel": f"{user}/{kernel_slug}"}
    (out / "stage.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def _kaggle_env() -> dict:
    env = dict(os.environ)
    if not env.get("KAGGLE_USERNAME") or not (env.get("KAGGLE_KEY") or env.get("KAGGLE_API_TOKEN")):
        raise SystemExit("Kaggle credentials missing: set KAGGLE_USERNAME and KAGGLE_KEY (or KAGGLE_API_TOKEN)")
    return env


def run(cmd: List[str], dry: bool, check: bool = True) -> subprocess.CompletedProcess:
    print("$ " + " ".join(cmd), flush=True)
    if dry:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    p = subprocess.run(cmd, env=_kaggle_env(), capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    if check and p.returncode:
        raise SystemExit(f"command failed ({p.returncode}): {' '.join(cmd)}")
    return p


def dataset_status(ref: str, dry: bool) -> str:
    if dry:
        return "missing"
    p = run(["kaggle", "datasets", "status", ref], dry, check=False)
    out = (p.stdout + p.stderr).strip().lower()
    if p.returncode or "not found" in out or "404" in out:
        return "missing"
    return out.splitlines()[-1] if out else "unknown"


def push(stage_dir: Path, dry: bool = False, message: str = "awos rl-sft data", wait_s: int = 900) -> dict:
    info = json.loads((stage_dir / "stage.json").read_text())
    ds, kn = str(stage_dir / "dataset"), str(stage_dir / "kernel")
    st = dataset_status(info["dataset"], dry)
    if st == "missing":
        run(["kaggle", "datasets", "create", "-p", ds, "-q"], dry)        # private by default
    else:
        run(["kaggle", "datasets", "version", "-p", ds, "-m", message, "-q"], dry)
    t0 = time.time()
    while not dry:
        st = dataset_status(info["dataset"], dry)
        if st == "ready":
            break
        if time.time() - t0 > wait_s:
            raise SystemExit(f"dataset {info['dataset']} not ready after {wait_s}s (status {st!r})")
        time.sleep(15)
    run(["kaggle", "kernels", "push", "-p", kn], dry)
    return {"dataset": info["dataset"], "kernel": info["kernel"], "dry_run": dry}


def kernel_status(kernel: str, dry: bool = False) -> str:
    """-> 'complete' | 'running' | 'queued' | 'error' | 'cancel...' | 'unknown'"""
    if dry:
        return "complete"
    p = run(["kaggle", "kernels", "status", kernel], dry, check=False)
    txt = (p.stdout + p.stderr).lower()
    for s in ("complete", "error", "cancel", "running", "queued"):
        if s in txt:
            return s
    return "unknown"


def wait(kernel: str, timeout_min: float, poll_s: int = 60, dry: bool = False) -> str:
    t0 = time.time()
    while True:
        s = kernel_status(kernel, dry)
        print(f"[{(time.time() - t0) / 60:.0f} min] {kernel}: {s}", flush=True)
        if s in ("complete", "error", "cancel"):
            return s
        if time.time() - t0 > timeout_min * 60:
            return "timeout"
        time.sleep(poll_s)


def fetch(kernel: str, out: Path, dry: bool = False) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    run(["kaggle", "kernels", "output", kernel, "-p", str(out), "-o"], dry)
    if not dry and not (out / "adapter" / "adapter_model.safetensors").exists():
        hits = list(out.rglob("adapter_model.safetensors"))
        if not hits:
            raise SystemExit(f"no adapter in the output of {kernel} (see {out}/*.log)")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.kaggle_driver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--data", required=True, type=Path)
    s.add_argument("--out", required=True, type=Path)
    s.add_argument("--size", default="4B", choices=list(SIZES))
    s.add_argument("--user", default=os.environ.get("KAGGLE_USERNAME") or "KAGGLE_USER")
    s.add_argument("--set", action="append", default=[], help="train_config override key=value (JSON value)")
    s.add_argument("--dataset-slug", default=DATASET_SLUG)
    s.add_argument("--kernel-slug", default=KERNEL_SLUG)
    p = sub.add_parser("push")
    p.add_argument("--stage", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--message", default="awos rl-sft data")
    w = sub.add_parser("wait")
    w.add_argument("--kernel", required=True)
    w.add_argument("--timeout-min", type=float, default=330)
    w.add_argument("--dry-run", action="store_true")
    f = sub.add_parser("fetch")
    f.add_argument("--kernel", required=True)
    f.add_argument("--out", required=True, type=Path)
    f.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "stage":
        overrides = {k: _val(v) for k, _, v in (x.partition("=") for x in a.set)}
        print(json.dumps(stage(a.data, a.out, a.size, a.user, overrides, a.dataset_slug, a.kernel_slug), indent=1))
    elif a.cmd == "push":
        print(json.dumps(push(a.stage, a.dry_run, a.message)))
    elif a.cmd == "wait":
        s = wait(a.kernel, a.timeout_min, dry=a.dry_run)
        print(s)
        return 0 if s == "complete" else 1
    elif a.cmd == "fetch":
        print(fetch(a.kernel, a.out, a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
