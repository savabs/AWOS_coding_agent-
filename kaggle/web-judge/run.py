"""
AWOS WebJudge-7B evaluation of Online-Mind2Web runs — Kaggle kernel entry (free GPU, no paid API).

Kaggle pushes one code file, so this file only bootstraps. The judge is the repo's scaffold/rl/web_judge +
scripts/web/judge.py, unpacked from the attached dataset, which kaggle/web-judge/stage.py builds:

    /kaggle/input/<awos-web-judge-data>/web_judge_src.tar.gz   scaffold/rl/web_judge/ + scripts/web/judge.py
    /kaggle/input/<awos-web-judge-data>/runs/<label>.tar.gz    one judge-layout run per file
                                                               (<task_id>/result.json + trajectory/)
    /kaggle/input/<awos-web-judge-data>/judge_config.json      the settings below (DEFAULTS)
    (optional) levels.json                                     Online-Mind2Web task file, for by-level rates

Steps:
    1. unpack the judge and the runs to /kaggle/tmp (not kept as output)
    2. pip install the pinned vLLM (vllm_pin). The upstream model card serves WebJudge-7B with
       `vllm serve osunlp/WebJudge-7B`; here it is pinned to a revision, fp16 (T4 has no bf16), tensor parallel
       over both T4s, at most max_images images per prompt (upstream MAX_IMAGE=50)
    3. start the OpenAI-compatible server on 127.0.0.1:<port>, wait for /health
    4. python scripts/web/judge.py --backend webjudge7b --runs <label>=<dir> ... --out /kaggle/working/results.json
       (upstream request: max_tokens=512, temperature=0)
Outputs in /kaggle/working: results.json (+ results.json.verdicts.jsonl), vllm.log, judge.log, env.json.

Locally, no GPU:  python kaggle/web-judge/run.py --repo . --runs-dir DIR --config kaggle/web-judge/judge_config.json \
                     --out /tmp/wj --dry-run           (prints the commands, starts nothing)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

DEFAULTS = {
    "model": "osunlp/WebJudge-7B",
    "revision": "bb550f8e301855933db5490eb8128217b3aca5dc",
    "vllm_pin": "vllm==0.31.0",
    "dtype": "half",
    "tensor_parallel": 2,
    # sized for 2x T4 (16 GB): with upstream's 65536 / 50 images / the preprocessor's 12.8 MP max_pixels, vLLM's
    # start-up profiling ran out of memory (2026-10-26 smoke; weights alone take 7.9 GB per GPU). A deviation
    # from the official judge: screenshots above ~1 MP are downscaled (ours are 1280x800 = 1.02 MP). See NOTICE.
    "max_model_len": 40960,
    "max_images": 30,
    "gpu_memory_utilization": 0.9,
    "mm_max_pixels": 1003520,
    "enforce_eager": True,
    "port": 8000,
    "server_start_timeout_s": 2400,
    "workers": 4,
    "image_workers": 8,
    "score_threshold": 3,
    "judge_blocked": False,
    "levels_file": None,
}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def on_kaggle() -> bool:
    return Path("/kaggle/working").exists()


def find_input(name: str, inputs: Path = Path("/kaggle/input")):
    for depth in range(1, 5):
        hits = sorted(inputs.glob("/".join(["*"] * depth) + "/" + name)) if inputs.exists() else []
        if hits:
            return hits[0]
    return None


def extract(tar_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:                                  # Python < 3.12 without extraction filters
            tf.extractall(dest)
    return dest


def collect_runs(runs_dir: Path, tmp: Path) -> list:
    """<label>.tar.gz files are extracted to tmp/runs/<label>; plain <label>/ directories are used as is."""
    out = []
    for p in sorted(runs_dir.iterdir()):
        if p.name.endswith((".tar.gz", ".tgz", ".tar")):
            label = p.name.split(".tar")[0].split(".tgz")[0]
            out.append((label, extract(p, tmp / "runs" / label)))
        elif p.is_dir():
            out.append((p.name, p))
    if not out:
        raise SystemExit(f"no runs under {runs_dir} (want <label>.tar.gz or <label>/)")
    return out


TORCH_CHECK = ("import torch, json; print(json.dumps({'torch': torch.__version__, 'cuda': torch.version.cuda, "
               "'ok': torch.cuda.is_available() and torch.cuda.device_count() > 0}))")


def fix_torch_stack() -> dict:
    """After `pip install vllm` on Kaggle (2026-10-26 judge smoke):
    1. vLLM pulls a CUDA 13 torch while Kaggle's preinstalled torchaudio is a CUDA 12.8 build, and transformers
       imports torchaudio, so vLLM dies at import. The judge needs no audio: remove torchaudio.
    2. A CUDA 13 torch needs a newer driver than CUDA 12.8 (our prebuilt cu12.8 llama.cpp runs on these T4s).
       If torch cannot see the GPUs, reinstall the same torch (+ torchvision) built for cu128."""
    def check() -> dict:
        p = subprocess.run([sys.executable, "-c", TORCH_CHECK], capture_output=True, text=True)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"ok": False, "error": (p.stderr or "")[-400:]}
    subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchaudio"])
    first = check()
    log("torch after vllm install:", first)
    if first.get("ok"):
        return {"first": first}
    ver = str(first.get("torch") or "").split("+")[0]
    pkgs = [f"torch=={ver}"] if ver else ["torch"]
    import importlib.metadata as md
    try:
        pkgs.append(f"torchvision=={md.version('torchvision').split('+')[0]}")
    except md.PackageNotFoundError:
        pass
    log("torch cannot use the GPU: reinstalling for cu128:", pkgs)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--force-reinstall", "--no-deps",
                           "--index-url", "https://download.pytorch.org/whl/cu128", *pkgs])
    second = check()
    log("torch after cu128 reinstall:", second)
    return {"first": first, "reinstalled": pkgs, "second": second}


def vllm_cmd(cfg: dict) -> list:
    cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", cfg["model"],
           "--revision", cfg["revision"], "--served-model-name", cfg["model"], "--host", "127.0.0.1",
           "--port", str(cfg["port"]), "--dtype", cfg["dtype"], "--tensor-parallel-size", str(cfg["tensor_parallel"]),
           "--max-model-len", str(cfg["max_model_len"]), "--gpu-memory-utilization", str(cfg["gpu_memory_utilization"]),
           "--limit-mm-per-prompt", json.dumps({"image": cfg["max_images"]})]
    if cfg.get("mm_max_pixels"):
        cmd += ["--mm-processor-kwargs", json.dumps({"max_pixels": int(cfg["mm_max_pixels"])})]
    if cfg.get("enforce_eager"):
        cmd += ["--enforce-eager"]                      # no CUDA-graph capture: saves memory on 16 GB T4s
    return cmd


def judge_cmd(repo: Path, cfg: dict, runs: list, out: Path, levels) -> list:
    cmd = [sys.executable, str(repo / "scripts" / "web" / "judge.py"), "--backend", "webjudge7b",
           "--base-url", f"http://127.0.0.1:{cfg['port']}/v1", "--model", cfg["model"],
           "--out", str(out / "results.json"), "--workers", str(cfg["workers"]),
           "--image-workers", str(cfg["image_workers"]), "--score-threshold", str(cfg["score_threshold"]),
           "--runs", *[f"{label}={path}" for label, path in runs]]
    if cfg.get("judge_blocked"):
        cmd.append("--judge-blocked")
    if levels:
        cmd += ["--levels", str(levels)]
    return cmd


def wait_health(port: int, timeout_s: float, proc) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if proc.poll() is not None:
            raise SystemExit(f"vLLM exited with {proc.returncode} before becoming healthy (see vllm.log)")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    log(f"vLLM healthy after {time.time() - t0:.0f} s")
                    return
        except Exception:
            pass
        time.sleep(10)
    raise SystemExit(f"vLLM not healthy after {timeout_s:.0f} s")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="repo checkout instead of web_judge_src.tar.gz")
    ap.add_argument("--runs-dir", default=None, help="dir of <label>.tar.gz / <label>/ (default: dataset runs/)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the commands, install / start nothing")
    a = ap.parse_args(argv)
    cfg_path = Path(a.config) if a.config else find_input("judge_config.json")
    cfg = dict(DEFAULTS, **(json.loads(cfg_path.read_text()) if cfg_path else {}))
    out = Path(a.out or ("/kaggle/working" if on_kaggle() else "./web-judge-out"))
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path("/kaggle/tmp/web-judge") if on_kaggle() else out / "tmp"
    if a.repo:
        repo = Path(a.repo).resolve()
    else:
        # Kaggle unpacks an uploaded .tar.gz into a same-stem folder (seen 2026-10-25 on the web-eval dataset)
        src, unpacked = find_input("web_judge_src.tar.gz"), find_input("web_judge_src")
        if src:
            repo = extract(src, tmp / "src")
        elif unpacked and unpacked.is_dir():
            repo = unpacked
        else:
            raise SystemExit("web_judge_src(.tar.gz) not found under /kaggle/input (attach awos-web-judge-data)")
    runs_dir = Path(a.runs_dir) if a.runs_dir else (find_input("runs") or Path("/kaggle/input/missing-runs"))
    runs = collect_runs(runs_dir, tmp)
    levels = Path(cfg["levels_file"]) if cfg.get("levels_file") else find_input("levels.json")
    vcmd, jcmd = vllm_cmd(cfg), judge_cmd(repo, cfg, runs, out, levels)
    log("runs:", ", ".join(f"{label}={p}" for label, p in runs))
    log("server:", " ".join(vcmd))
    log("judge:", " ".join(jcmd))
    if a.dry_run:
        return 0
    env_info = {"config": cfg, "python": sys.version}
    if on_kaggle():
        log("pip install", cfg["vllm_pin"])
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", cfg["vllm_pin"]])
        env_info["torch_fix"] = fix_torch_stack()
    try:
        env_info["nvidia_smi"] = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout
        env_info["driver"] = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                                            capture_output=True, text=True).stdout.strip()
    except OSError:
        pass
    (out / "env.json").write_text(json.dumps(env_info, indent=1) + "\n")
    with open(out / "vllm.log", "w") as vlog:
        proc = subprocess.Popen(vcmd, stdout=vlog, stderr=subprocess.STDOUT)
        try:
            wait_health(cfg["port"], cfg["server_start_timeout_s"], proc)
            with open(out / "judge.log", "w") as jlog:
                rc = subprocess.call(jcmd, cwd=repo, stdout=jlog, stderr=subprocess.STDOUT,
                                     env={**os.environ, "PYTHONPATH": str(repo)})
            log(f"judge exit {rc}")
            print((out / "judge.log").read_text()[-4000:])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
    shutil.rmtree(tmp / "runs", ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
