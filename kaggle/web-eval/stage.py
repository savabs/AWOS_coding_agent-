"""
stage.py — settings, staging and output collection for the Online-Mind2Web Kaggle run (kaggle/web-eval/run.py).

    python kaggle/web-eval/stage.py settings [--format env|specs|json]
        ci/rl_web_kaggle.env (+ CTX_PER_SLOT from ci/rl_web.env, MODEL_SPECS / LLAMA_TAG from ci/rl_eval.env)
    python kaggle/web-eval/stage.py stage --tasks-json FILE --models-dir DIR --out stage-we/ [--user U]
    python kaggle/web-eval/stage.py collect --kernel U/awos-web-eval --out kaggle_out/ [--dry-run]

stage builds (no network, no credentials, never pushes):
    stage-we/dataset/  web_eval_src.tar.gz (scaffold/ from the working tree), Online_Mind2Web.json (the gated
                       task file, as given), every GGUF of every model (hard links when possible),
                       web_eval_config.json, dataset-metadata.json (KAGGLE_USER/<DATASET_SLUG>, private)
    stage-we/kernel/   run.py, kernel-metadata.json (GPU T4 x2, internet on, private; LLAMA_BUILD_SOURCE as
                       a kernel source when set)
    stage-we/stage.json  {dataset, kernel, models}: what `python -m scaffold.rl.train.kaggle_driver push` reads
The task file's content is never printed: only its task count.
collect: `kaggle kernels output` (kaggle_driver credentials handling), then lists each model's run folder
(<model>__web-<split>/ with judge/) for the GitHub artifacts.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts" / "rl"))
from ci_matrix import model_specs, parse_env  # noqa: E402

TASK_FILE = "Online_Mind2Web.json"
SRC_PATHS = ["scaffold"]
MAX_MODELS = 4                      # artifact upload slots in .github/workflows/rl-web-kaggle.yml
DEFAULT_ENV = REPO / "ci" / "rl_web_kaggle.env"
WEB_ENV = REPO / "ci" / "rl_web.env"
EVAL_ENV = REPO / "ci" / "rl_eval.env"


def _int(env: dict, k: str, default: int, lo: int, hi: int) -> int:
    v = int(str(env.get(k) or default))
    if not lo <= v <= hi:
        raise SystemExit(f"{k}={v} out of range [{lo}, {hi}]")
    return v


def load_settings(path: Path = DEFAULT_ENV, web_env: Path = WEB_ENV, eval_env: Path = EVAL_ENV) -> dict:
    env, web, ev = parse_env(Path(path)), parse_env(Path(web_env)), parse_env(Path(eval_env))
    specs = model_specs(ev)
    models = env.get("MODELS", "").split()
    if not models:
        raise SystemExit("MODELS is empty")
    if len(models) > MAX_MODELS:
        raise SystemExit(f"at most {MAX_MODELS} models per run (got {len(models)})")
    for m in models:
        if m not in specs:
            raise SystemExit(f"unknown model {m!r}; known: {', '.join(specs)}")
    split = env.get("SPLIT") or "dev"
    if split not in ("dev", "all"):
        raise SystemExit(f"SPLIT must be dev or all, not {split!r}")
    gpus = _int(env, "GPUS", 2, 1, 2)
    workers = _int(env, "WORKERS", 4, 1, 16)
    s = {"MODELS": models, "SPLIT": split, "WORKERS": workers, "GPUS": gpus,
         "MAX_STEPS": _int(env, "MAX_STEPS", 25, 1, 100), "MAX_SECONDS": _int(env, "MAX_SECONDS", 900, 60, 7200),
         "N_TASKS": _int(env, "N_TASKS", 0, 0, 300),
         "KERNEL_SLUG": env.get("KERNEL_SLUG") or "awos-web-eval",
         "DATASET_SLUG": env.get("DATASET_SLUG") or "awos-web-eval-data",
         "RUN_NAME": env.get("RUN_NAME") or "", "LLAMA_BUILD_SOURCE": env.get("LLAMA_BUILD_SOURCE") or "",
         "DEADLINE_HOURS": float(env.get("DEADLINE_HOURS") or 11),
         "LLAMA_TAG": env.get("LLAMA_TAG") or ev.get("LLAMA_TAG") or "b11146",
         "CTX_PER_SLOT": int(env.get("CTX_PER_SLOT") or web.get("CTX_PER_SLOT") or 12288),
         "SLOTS_PER_SERVER": -(-workers // gpus),
         "specs": {m: specs[m] for m in models}}
    for k in ("KERNEL_SLUG", "DATASET_SLUG"):
        if not s[k].replace("-", "").isalnum() or not 5 <= len(s[k]) <= 50:
            raise SystemExit(f"bad {k} {s[k]!r}")
    if not 0 < s["DEADLINE_HOURS"] < 12:
        raise SystemExit("DEADLINE_HOURS must be under Kaggle's 12 h session limit")
    return s


def load_kernel():
    """kaggle/web-eval/run.py as a module (by path: kaggle/web-judge has a run.py too)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("awos_web_eval_kernel", HERE / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def kernel_config(s: dict, ntfy_topic=None) -> dict:
    cfg = dict(load_kernel().DEFAULTS)
    cfg.update({"models": [{"label": m, "file": s["specs"][m]["file"]} for m in s["MODELS"]], "split": s["SPLIT"],
                "n_tasks": s["N_TASKS"],
                "workers": s["WORKERS"], "gpus": s["GPUS"], "slots_per_server": s["SLOTS_PER_SERVER"],
                "max_steps": s["MAX_STEPS"], "max_seconds": s["MAX_SECONDS"], "ctx_per_slot": s["CTX_PER_SLOT"],
                "llama_tag": s["LLAMA_TAG"], "deadline_hours": s["DEADLINE_HOURS"], "run_name": s["RUN_NAME"]})
    if ntfy_topic is not None:
        cfg["ntfy_topic"] = ntfy_topic
    return cfg


def model_files(spec: dict, models_dir: Path) -> list:
    """Every file of a model: the GGUF itself, or all its shards (<stem>-0000k-of-0000n.gguf)."""
    f = spec["file"]
    if "-00001-of-" in f:
        stem = f.split("-00001-of-")[0]
        files = sorted(models_dir.glob(f"{stem}-*-of-*.gguf"))
    else:
        files = [models_dir / f]
    if not files or not files[0].is_file() or files[0].name != f:
        raise SystemExit(f"{spec['model']}: {f} not in {models_dir}")
    if files[0].stat().st_size != int(spec["bytes"]):
        raise SystemExit(f"{spec['model']}: {f} is {files[0].stat().st_size} bytes, MODEL_SPECS says {spec['bytes']}")
    return files


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _skip(ti: tarfile.TarInfo):
    n = ti.name
    if "__pycache__" in n or n.endswith(".pyc") or "/.venv" in n or Path(n).name == TASK_FILE:
        return None
    return ti


def count_tasks(path: Path) -> int:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("data") or data.get("tasks") or list(data.values())
    return len(data)


def stage(out: Path, tasks_json: Path, models_dir: Path, settings: dict, user: str = "KAGGLE_USER",
          ntfy_topic=None) -> dict:
    out, tasks_json, models_dir = Path(out), Path(tasks_json), Path(models_dir)
    n_tasks = count_tasks(tasks_json)                     # must parse; its content is never printed
    if not n_tasks:
        raise SystemExit(f"{TASK_FILE}: no tasks")
    files = {m: model_files(settings["specs"][m], models_dir) for m in settings["MODELS"]}
    ds, kd = out / "dataset", out / "kernel"
    for d in (ds, kd):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    with tarfile.open(ds / "web_eval_src.tar.gz", "w:gz") as tf:
        for p in SRC_PATHS:
            tf.add(REPO / p, arcname=p, filter=_skip)
    shutil.copy(tasks_json, ds / TASK_FILE)
    for fs in files.values():
        for f in fs:
            _link(f, ds / f.name)
    cfg = kernel_config(settings, ntfy_topic)
    (ds / "web_eval_config.json").write_text(json.dumps(cfg, indent=1) + "\n")
    dslug, kslug = settings["DATASET_SLUG"], settings["KERNEL_SLUG"]
    (ds / "dataset-metadata.json").write_text(json.dumps({"title": dslug, "id": f"{user}/{dslug}",
                                                          "licenses": [{"name": "other"}], "isPrivate": True},
                                                         indent=1) + "\n")
    shutil.copy(HERE / "run.py", kd / "run.py")
    meta = json.loads((HERE / "kernel-metadata.json").read_text())
    meta.update({"id": f"{user}/{kslug}", "title": kslug, "dataset_sources": [f"{user}/{dslug}"],
                 "is_private": True, "enable_gpu": True, "enable_internet": True})
    if settings.get("LLAMA_BUILD_SOURCE"):              # an earlier run's llama-cuda-<tag>-sm75.tar.gz
        meta["kernel_sources"] = [settings["LLAMA_BUILD_SOURCE"]]
    (kd / "kernel-metadata.json").write_text(json.dumps(meta, indent=1) + "\n")
    info = {"dataset": f"{user}/{dslug}", "kernel": f"{user}/{kslug}", "models": settings["MODELS"],
            "split": settings["SPLIT"], "tasks_in_file": n_tasks,
            "files": sorted(p.name for p in ds.iterdir())}
    (out / "stage.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def run_dirs(out: Path) -> list:
    """The model run folders in a fetched kernel output: <model>__web-<split>/ holding judge/."""
    return sorted(p for p in Path(out).rglob("*__web-*") if p.is_dir() and (p / "judge").is_dir())


def collect(kernel: str, out: Path, dry: bool = False) -> list:
    from scaffold.rl.train.kaggle_driver import run as kaggle_run
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    kaggle_run(["kaggle", "kernels", "output", kernel, "-p", str(out), "-o"], dry)
    leak = list(out.rglob(TASK_FILE))
    for p in leak:                                         # never upload the gated task file
        p.unlink()
    if leak:
        print(f"::warning::removed {len(leak)} copy(ies) of {TASK_FILE} from the kernel output")
    return run_dirs(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kaggle/web-eval/stage.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("settings", "stage"):
        p = sub.add_parser(name)
        p.add_argument("--env", type=Path, default=DEFAULT_ENV)
        p.add_argument("--web-env", type=Path, default=WEB_ENV)
        p.add_argument("--specs", type=Path, default=EVAL_ENV)
        if name == "settings":
            p.add_argument("--format", default="env", choices=["env", "specs", "json"])
        else:
            p.add_argument("--tasks-json", required=True, type=Path)
            p.add_argument("--models-dir", required=True, type=Path)
            p.add_argument("--out", required=True, type=Path)
            p.add_argument("--user", default=os.environ.get("KAGGLE_USERNAME") or "KAGGLE_USER")
    c = sub.add_parser("collect")
    c.add_argument("--kernel", required=True)
    c.add_argument("--out", required=True, type=Path)
    c.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        for d in collect(a.kernel, a.out, a.dry_run):
            print(d)
        return 0
    s = load_settings(a.env, a.web_env, a.specs)
    if a.cmd == "settings":
        if a.format == "specs":                            # label repo file rev sha256: the GGUF downloads
            for m in s["MODELS"]:
                sp = s["specs"][m]
                print(m, sp["repo"], sp["file"], sp["rev"], sp["sha256"])
        elif a.format == "json":
            print(json.dumps(s, indent=1))
        else:
            for k, v in s.items():
                if k != "specs":
                    print(f"{k}={' '.join(v) if isinstance(v, list) else v}")
        return 0
    info = stage(a.out, a.tasks_json, a.models_dir, s, a.user)
    print(f"staged {a.out}: dataset {info['dataset']} ({len(info['files'])} files, {info['tasks_in_file']} tasks in "
          f"the task file), kernel {info['kernel']}, models {' '.join(info['models'])}\n"
          f"next: python -m scaffold.rl.train.kaggle_driver push --stage {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
