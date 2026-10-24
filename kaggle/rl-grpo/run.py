"""
AWOS online RL (GRPO) feasibility run — Kaggle kernel entry (NEXT_WEEK_PLAN run 6: 2B, T4, go / no-go).

Kaggle pushes one code file, so this file only bootstraps; the trainer is the repo's
scaffold/rl/train/grpo.py, unpacked from the attached dataset (built by kaggle/rl-grpo/stage.py):

    /kaggle/input/<awos-rl-grpo-data>/awos_src.tar.gz      `git archive` of the repo commit to run
    /kaggle/input/<awos-rl-grpo-data>/grpo_config.json     {"kernel": {...}, "grpo": {GRPOConfig fields}}

Steps:
    1. unpack the repo to /kaggle/tmp/awos (outside /kaggle/working, so it is not kept as output)
    2. pip install the SFT kernel's pins (read from kaggle/rl-sft/train.py PINS, not edited) + playwright,
       remove torchao (peft 0.21 rejects Kaggle's), `playwright install --with-deps chromium`
    3. speed probe: scripts/rl/grpo_speed.py --measure-env -> /kaggle/working/speed.json (the go / no-go
       number: rollouts/hour on this T4 with one in-process HF model)
    4. unless kernel.speed_only: GRPO for grpo.iterations iterations on factory TRAIN tasks ->
       /kaggle/working/{grpo_log.jsonl, rollouts.jsonl, ck/, final/adapter/}; resumes from the newest
       checkpoint of kernel.resume_from (an earlier run of this kernel attached as a kernel source; looked up
       before step 2 so a missing checkpoint fails fast)
Locally (CPU smoke, no Kaggle):  python kaggle/rl-grpo/run.py --repo . --config grpo_config.json --out /tmp/g
No paid API is called anywhere in this path; GPU time is the only cost (grpo.max_hours caps it).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

PLAYWRIGHT_PIN = "playwright==1.64.0"


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def on_kaggle() -> bool:
    return Path("/kaggle/working").exists()


def find_input(name: str) -> Path:
    hits = sorted(Path("/kaggle/input").glob(f"*/{name}")) + sorted(Path("/kaggle/input").glob(f"*/*/{name}"))
    if not hits:
        raise SystemExit(f"{name} not found under /kaggle/input (attach the awos-rl-grpo-data dataset)")
    return hits[0]


def unpack_repo(tar_path: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:                                  # Python < 3.12 without extraction filters
            tf.extractall(dest)
    return dest


def sft_pins(repo: Path) -> list:
    """PINS of kaggle/rl-sft/train.py (importable: top level is stdlib only)."""
    spec = importlib.util.spec_from_file_location("awos_rl_sft_train", repo / "kaggle" / "rl-sft" / "train.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return list(mod.PINS)


def install(repo: Path, kcfg: dict) -> None:
    pins = sft_pins(repo) + [PLAYWRIGHT_PIN]
    log("pip install", *pins)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pins])
    subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchao"])
    cmd = [sys.executable, "-m", "playwright", "install"] + (["--with-deps"] if kcfg.get("with_deps", True) else []) + ["chromium"]
    log(*cmd)
    subprocess.check_call(cmd)


def _iter_no(d: Path) -> int:
    try:
        return int(d.name.split("_", 1)[1])
    except (IndexError, ValueError):
        return -1


def find_resume(spec: str, inputs: Path = Path("/kaggle/input")) -> Path:
    """Newest complete ck/iter_*/ of an earlier run. ``spec`` is a path, or a kernel slug ("owner/" prefix
    allowed, as stage.py accepts it). Kaggle mounts kernel sources at /kaggle/input/<slug>/ or deeper at
    /kaggle/input/notebooks/<owner>/<slug>/, so roots are searched at depths 1-4 (same as the SFT kernel's
    find_resume_checkpoint). Raises SystemExit when nothing is found, so call it before install/probe."""
    p = Path(spec)
    roots = [p] if p.is_absolute() and p.is_dir() else []
    if not roots:
        slug = spec.rstrip("/").rsplit("/", 1)[-1]
        for depth in range(1, 5):
            roots += [d for d in inputs.glob("/".join(["*"] * (depth - 1) + [f"{slug}*"])) if d.is_dir()]
    cands = [d for r in roots for d in ([r] if r.name.startswith("iter_") else r.rglob("iter_*"))
             if d.parent.name == "ck" and (d / "state.json").exists() and _iter_no(d) >= 0]
    if not cands:
        raise SystemExit(f"resume_from={spec!r}: no ck/iter_*/state.json under {inputs} (attach the kernel)")
    return max(cands, key=_iter_no)


def copy_resume(src: Path, out: Path) -> None:
    """Copy a checkpoint found by find_resume into out/ck (grpo.train resumes the newest there)."""
    dst = out / "ck" / src.name
    if not dst.exists():
        shutil.copytree(src, dst)
    log(f"resume: copied {src} -> {dst}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="repo checkout to use instead of the dataset tarball")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    cfg_path = Path(a.config) if a.config else find_input("grpo_config.json")
    conf = json.loads(cfg_path.read_text())
    kcfg, gcfg = dict(conf.get("kernel") or {}), dict(conf.get("grpo") or {})
    out = Path(a.out or ("/kaggle/working" if on_kaggle() else "./grpo-out"))
    out.mkdir(parents=True, exist_ok=True)
    # resolve the resume checkpoint first: a missing one should fail before install + probe spend GPU time
    resume = find_resume(kcfg["resume_from"]) if kcfg.get("resume_from") and not kcfg.get("speed_only") else None
    repo = Path(a.repo).resolve() if a.repo else unpack_repo(find_input("awos_src.tar.gz"), Path("/kaggle/tmp/awos"))
    if on_kaggle() and kcfg.get("pip_install", True):
        install(repo, kcfg)
    sys.path.insert(0, str(repo))
    env = {"PYTHONPATH": os.pathsep.join(x for x in (str(repo), os.environ.get("PYTHONPATH", "")) if x)}
    if kcfg.get("speed_probe", True):
        cmd = [sys.executable, str(repo / "scripts" / "rl" / "grpo_speed.py"), "--model", gcfg.get("base_model", ""),
               "--dtype", gcfg.get("dtype", "auto"), "--measure-env", "--out", str(out / "speed.json")]
        for k in ("completion", "steps", "iterations", "tasks_per_iter", "group", "budget_hours"):
            if k in kcfg:
                cmd += [f"--{k.replace('_', '-')}", str(kcfg[k])]
        log(*cmd)
        rc = subprocess.call(cmd, cwd=repo, env={**os.environ, **env})
        log(f"speed probe exit {rc}")
    if kcfg.get("speed_only"):
        return 0
    if resume is not None:
        copy_resume(resume, out)
    from scaffold.rl.train.grpo import GRPOConfig, train
    cfg = GRPOConfig.from_dict(gcfg)
    row = train(cfg, out, log=log)
    (out / "result.json").write_text(json.dumps(row, indent=1) + "\n")
    log("done", json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
