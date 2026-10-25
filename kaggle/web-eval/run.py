"""
AWOS Online-Mind2Web rollouts on a free Kaggle T4 x2 — kernel entry (live web, llama.cpp on CUDA, no paid API).

Kaggle pushes one code file, so this file only bootstraps. The harness is the repo's scaffold/ (python -m
scaffold.rl.web), unpacked from the private dataset kaggle/web-eval/stage.py builds:

    /kaggle/input/<awos-web-eval-data>/web_eval_src.tar.gz    scaffold/ (harness, dev ids)
    /kaggle/input/<awos-web-eval-data>/web_eval_config.json   the settings below (DEFAULTS), models included
    /kaggle/input/<awos-web-eval-data>/Online_Mind2Web.json   the gated task file (read by the harness only)
    /kaggle/input/<awos-web-eval-data>/<model>.gguf           every GGUF of every model (shards included)
    (optional kernel source) llama-cuda-<tag>-sm<arch>.tar.gz a llama-server an earlier run built

Steps:
    1. unpack scaffold/ to /kaggle/tmp (not kept as output); pip install the pinned Playwright, then
       python -m playwright install --with-deps chromium
    2. llama-server with CUDA, first that works of: the attached earlier build; the official prebuilt
       llama-<tag>-bin-ubuntu-cuda-<cuda>-x64 (+ its cudart libs; the release ships sm_75 as PTX only, so it
       needs a driver for CUDA >= that version); a source build (cmake -DGGML_CUDA=ON
       -DCMAKE_CUDA_ARCHITECTURES=75), saved to the output so the next run can attach it as a kernel source
    3. per model: one llama-server per GPU (CUDA_VISIBLE_DEVICES=<i>, 127.0.0.1:<base_port + i>, the CI flags
       --jinja --reasoning off --cache-prompt --temp 0, ctx_per_slot x slots), a 1-token smoke request, then
       python -m scaffold.rl.web run --model local --split <split> --workers N --base-url <gpu0> --base-url <gpu1>
       (worker i talks to server i mod n), the same caps as CI (max_steps, max_seconds per task)
    4. progress pings to https://ntfy.sh/<ntfy_topic> (counts only, labelled [run_name])
Outputs in /kaggle/working: <model>__web-<split>/ (trajectories.jsonl, judge/<task_id>/, run.json, summary.json,
summary.md, harness.log, validate.txt, llama-server-gpu<i>.log), summary.json + summary.md (all models),
env.json, and llama-cuda-<tag>-sm<arch>.tar.gz after a source build. The task file is never copied there.

Locally, no GPU:  python kaggle/web-eval/run.py --src . --inputs DIR --out /tmp/we --dry-run
                     (prints the commands, starts nothing)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

TASK_FILE = "Online_Mind2Web.json"
DEFAULTS = {
    "models": [],                   # [{"label", "file"}]: file = the GGUF llama-server -m loads (first shard)
    "split": "dev",
    "n_tasks": 0,                   # first N tasks of the split (0 = all): a smoke run before the full one
    "workers": 4,                   # harness worker processes (one Chromium each), round-robin over the servers
    "max_steps": 25,
    "max_seconds": 900,             # wall clock per task
    "gpus": 2,
    "slots_per_server": 2,          # llama-server -np; default ceil(workers / gpus)
    "ctx_per_slot": 12288,
    "threads": 2,
    "base_port": 8080,
    "llama_tag": "b11146",
    "prebuilt_cuda": "12.8",        # llama-<tag>-bin-ubuntu-cuda-<this>-x64.tar.gz
    "cuda_arch": "75",              # T4 = Turing sm_75
    "local_timeout_s": 180,         # one model call on a GPU; CI's CPU value is 600
    "server_start_timeout_s": 900,
    "deadline_hours": 11.0,         # Kaggle stops a session at 12 h: stop the harness before (it is resumable)
    "playwright_pin": "playwright==1.64.0",
    "ntfy_topic": "awos-rl-be26ddc76649",   # same topic as kaggle/rl-sft/train.py ("" = off)
    "run_name": "",
    "ping_every_tasks": 10,
    "ping_every_s": 600,
}
PROGRESS = re.compile(r"^\[(\d+)/(\d+)\] (\S+) (\S+) .*?steps=\d+ (?:(\d+(?:\.\d+)?)s)?")


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def on_kaggle() -> bool:
    return Path("/kaggle/working").exists()


def find_input(name: str, inputs: Path = Path("/kaggle/input")):
    for depth in range(0, 6):                              # /kaggle/input/<slug>/<name>, or deeper mounts
        hits = sorted(inputs.glob("/".join(["*"] * depth + [name]))) if inputs.exists() else []
        if hits:
            return hits[0]
    return None


def locate_src(inputs: Path, tmp: Path) -> Path:
    """The repo code: web_eval_src.tar.gz, or the folder Kaggle unpacks it into. Kaggle unpacks an uploaded .tar.gz
    into a same-stem folder (2026-10-25 smoke: the dataset listed web_eval_src/scaffold/..., never the archive)."""
    tar = find_input("web_eval_src.tar.gz", inputs)
    if tar:
        return extract(tar, tmp / "src")
    unpacked = find_input("web_eval_src", inputs)
    if unpacked and (unpacked / "scaffold").is_dir():
        return unpacked
    raise SystemExit("web_eval_src(.tar.gz) not found (attach the awos-web-eval-data dataset)")


def extract(tar_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:                                  # Python < 3.12 without extraction filters
            tf.extractall(dest)
    return dest


def ntfy(cfg: dict, message: str, title: str = "", priority: str = "default", tags: str = "") -> None:
    """Progress ping to https://ntfy.sh/<ntfy_topic> (as kaggle/rl-sft/train.py). Counts and timings only, never
    task text. Never raises, never blocks more than a few seconds."""
    topic = cfg.get("ntfy_topic")
    if not topic:
        return
    if cfg.get("run_name"):
        title = f"[{cfg['run_name']}] {title}"
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), method="POST")
        if title:
            req.add_header("Title", title)
        req.add_header("Priority", priority)
        if tags:
            req.add_header("Tags", tags)
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


# ------------------------------------------------------------------ commands --

def slots(cfg: dict) -> int:
    return max(1, int(cfg.get("slots_per_server") or -(-int(cfg["workers"]) // int(cfg["gpus"]))))


def server_cmd(cfg: dict, binary: str, gguf: str, alias: str, port: int) -> list:
    n = slots(cfg)
    return [binary, "-m", gguf, "--alias", alias, "--host", "127.0.0.1", "--port", str(port),
            "-ngl", "99", "-t", str(cfg["threads"]), "-c", str(int(cfg["ctx_per_slot"]) * n), "-np", str(n),
            "--jinja", "--reasoning", "off", "--cache-prompt", "--temp", "0", "--metrics"]


def server_plan(cfg: dict, binary: str, gguf: str, alias: str) -> list:
    """[(gpu, port, cmd, env_overrides)]: one llama-server per GPU, pinned with CUDA_VISIBLE_DEVICES."""
    lib = str(Path(binary).parent)
    plan = []
    for g in range(int(cfg["gpus"])):
        port = int(cfg["base_port"]) + g
        env = {"CUDA_VISIBLE_DEVICES": str(g), "LD_LIBRARY_PATH": lib + ":" + os.environ.get("LD_LIBRARY_PATH", "")}
        plan.append((g, port, server_cmd(cfg, binary, gguf, alias, port), env))
    return plan


def harness_cmd(cfg: dict, label: str, tasks_json: str, out: Path, ports: list) -> list:
    cmd = [sys.executable, "-m", "scaffold.rl.web", "run", "--model", "local", "--model-label", label,
           "--split", cfg["split"], "--max-steps", str(cfg["max_steps"]), "--max-seconds", str(cfg["max_seconds"]),
           "--workers", str(cfg["workers"]), "--tasks-json", tasks_json, "--out", str(out) + "/"]
    if int(cfg.get("n_tasks") or 0) > 0:
        cmd += ["--n", str(int(cfg["n_tasks"]))]
    for p in ports:
        cmd += ["--base-url", f"http://127.0.0.1:{p}/v1"]
    return cmd


def prebuilt_assets(cfg: dict) -> list:
    tag, cu = cfg["llama_tag"], cfg["prebuilt_cuda"]
    base = f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}"
    return [f"{base}/llama-{tag}-bin-ubuntu-cuda-{cu}-x64.tar.gz",
            f"{base}/cudart-llama-{tag}-bin-ubuntu-cuda-{cu}-x64.tar.gz"]


def build_name(cfg: dict) -> str:
    return f"llama-cuda-{cfg['llama_tag']}-sm{cfg['cuda_arch']}.tar.gz"


def build_cmds(cfg: dict, root: Path) -> list:
    src, bld = root / "llama.cpp", root / "llama.cpp" / "build"
    return [["git", "clone", "--depth", "1", "--branch", cfg["llama_tag"], "https://github.com/ggml-org/llama.cpp",
             str(src)],
            ["cmake", "-S", str(src), "-B", str(bld), "-DGGML_CUDA=ON",
             f"-DCMAKE_CUDA_ARCHITECTURES={cfg['cuda_arch']}", "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
             "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF"],
            ["cmake", "--build", str(bld), "--target", "llama-server", "-j", str(os.cpu_count() or 4)]]


def driver_cuda() -> tuple:
    """(major, minor) of the CUDA version the driver supports, from nvidia-smi; (0, 0) when unknown."""
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    except (OSError, subprocess.SubprocessError):
        return (0, 0)


def _find_bin(root: Path):
    hits = sorted(p for p in root.rglob("llama-server") if p.is_file())
    if not hits:
        return None
    hits[0].chmod(0o755)
    return hits[0]


def llama_candidates(cfg: dict, tmp: Path, out: Path):
    """Yield (how, path to llama-server), cheapest first. Each is tried for real (start + smoke) by the caller."""
    att = find_input(build_name(cfg))
    if att:
        b = _find_bin(extract(att, tmp / "llama-attached"))
        if b:
            yield "attached", b
    need = tuple(int(x) for x in str(cfg["prebuilt_cuda"]).split("."))
    have = driver_cuda()
    if have >= need:
        d = tmp / "llama-prebuilt"
        try:
            d.mkdir(parents=True, exist_ok=True)
            for url in prebuilt_assets(cfg):
                f = d / url.rsplit("/", 1)[1]
                log("download", url)
                urllib.request.urlretrieve(url, f)
                extract(f, d)
                f.unlink()
            b = _find_bin(d)
            if b:
                for lib in d.rglob("lib*.so*"):            # cudart / cublas next to the binary ($ORIGIN rpath)
                    if lib.parent != b.parent and not (b.parent / lib.name).exists():
                        shutil.copy2(lib, b.parent / lib.name)
                yield "prebuilt", b
        except Exception as e:
            log(f"prebuilt unavailable: {type(e).__name__}: {e}"[:300])
    else:
        log(f"driver supports CUDA {have[0]}.{have[1]} < {cfg['prebuilt_cuda']} (prebuilt has sm_75 as PTX only): skip it")
    root = tmp / "llama-src"
    t0 = time.time()
    if shutil.which("cmake") is None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "cmake"])
    with open(out / "llama-build.log", "w") as fh:
        for c in build_cmds(cfg, root):
            log("$", " ".join(c))
            subprocess.check_call(c, stdout=fh, stderr=subprocess.STDOUT)
    b = root / "llama.cpp" / "build" / "bin" / "llama-server"
    log(f"llama.cpp built from source in {time.time() - t0:.0f} s")
    with tarfile.open(out / build_name(cfg), "w:gz") as tf:   # attach this run as a kernel source to skip the build
        tf.add(b, arcname="llama-server")
    yield "source", b


# ------------------------------------------------------------------ servers --

def wait_health(port: int, timeout_s: float, proc) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server :{port} exited with {proc.returncode} before becoming healthy")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"llama-server :{port} not healthy after {timeout_s:.0f} s")


def smoke(port: int, alias: str) -> float:
    body = {"model": alias, "max_tokens": 4, "temperature": 0, "messages": [{"role": "user", "content": "Say OK."}],
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    if not (data.get("choices") or [{}])[0].get("message"):
        raise RuntimeError(f"llama-server :{port} smoke request returned no message")
    return time.time() - t0


def start_servers(cfg: dict, binary: Path, gguf: Path, alias: str, logdir: Path) -> list:
    procs = []
    try:
        for g, port, cmd, env in server_plan(cfg, str(binary), str(gguf), alias):
            log(f"gpu{g}:", " ".join(cmd))
            fh = open(logdir / f"llama-server-gpu{g}.log", "w")
            procs.append((port, subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                                 env={**os.environ, **env}), fh))
        for port, p, _ in procs:
            wait_health(port, cfg["server_start_timeout_s"], p)
            log(f"llama-server :{port} healthy; smoke {smoke(port, alias):.2f} s")
        return procs
    except Exception:
        stop_servers(procs)
        raise


def stop_servers(procs: list) -> None:
    for _, p, fh in procs:
        p.terminate()
    for _, p, fh in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
        fh.close()


# ------------------------------------------------------------------ harness --

def parse_progress(line: str):
    """The harness's per-task line: [i/total] <task_id> <TAG> ... <seconds>s -> dict (no task text in it)."""
    m = PROGRESS.match(line.strip())
    if not m:
        return None
    return {"i": int(m.group(1)), "total": int(m.group(2)), "tag": m.group(4), "seconds": float(m.group(5) or 0)}


def progress_line(label: str, st: dict, t0: float) -> str:
    done = st["done"]
    return (f"{label}: {done}/{st['total'] or '?'} tasks | answered {st['answered']} blocked {st['blocked']} "
            f"errors {st['errors']} | {st['task_s'] / max(1, done):.0f} s/task | {(time.time() - t0) / 60:.0f} min")


def run_harness(cfg: dict, cmd: list, src: Path, label: str, out: Path, deadline: float) -> dict:
    env = {**os.environ, "PYTHONPATH": str(src), "AWOS_LOCAL_MODEL": label,
           "AWOS_LOCAL_TIMEOUT_S": str(cfg["local_timeout_s"]), "PYTHONUNBUFFERED": "1"}
    st = {"done": 0, "total": 0, "answered": 0, "blocked": 0, "errors": 0, "task_s": 0.0, "deadline_hit": False}
    t0 = last_ping = time.time()
    with open(out / "harness.log", "w") as fh:
        p = subprocess.Popen(cmd, cwd=src, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            fh.write(line)
            fh.flush()
            ev = parse_progress(line)
            if ev:
                st["done"], st["total"] = ev["i"], ev["total"]
                st["task_s"] += ev["seconds"]
                st["answered"] += ev["tag"] == "ANSWERED"
                st["blocked"] += ev["tag"].startswith("BLOCKED")
                st["errors"] += ev["tag"] == "ERR"
                if ev["i"] % max(1, int(cfg["ping_every_tasks"])) == 0 or time.time() - last_ping > cfg["ping_every_s"]:
                    msg = progress_line(label, st, t0)
                    log(msg)
                    ntfy(cfg, msg, title="AWOS web eval")
                    last_ping = time.time()
            if time.time() > deadline:
                st["deadline_hit"] = True
                log("deadline reached: stopping the harness (the run is resumable)")
                p.terminate()
                break
        try:
            p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
    st["rc"] = p.returncode
    st["minutes"] = round((time.time() - t0) / 60, 1)
    return st


def summarize(src: Path, out: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(src)}
    subprocess.run([sys.executable, "-m", "scaffold.rl.web", "summarize", str(out)], cwd=src, env=env,
                   capture_output=True, text=True)
    v = subprocess.run([sys.executable, "-m", "scaffold.rl.web", "validate", str(out / "judge")], cwd=src, env=env,
                       capture_output=True, text=True)
    (out / "validate.txt").write_text(v.stdout + v.stderr)
    p = out / "summary.json"
    return json.loads(p.read_text()) if p.exists() else {}


def to_markdown(results: dict) -> str:
    lines = ["# Online-Mind2Web on Kaggle (T4 x2, llama.cpp CUDA)", "",
             "Success is not scored here: run WebJudge over each <model>__web-<split>/judge/ (blocked = failure).", "",
             "| model | episodes | answered | blocked | infra errors | steps/episode | model ms/step | s/episode |"
             " minutes | deadline hit |", "|---|---|---|---|---|---|---|---|---|---|"]
    for label, r in results["models"].items():
        o = (r.get("summary") or {}).get("overall") or {}
        lines.append(f"| {label} | {o.get('episodes', 0)} | {o.get('answered', '')} | {o.get('blocked', '')} | "
                     f"{(r.get('summary') or {}).get('infra_errors', '')} | {o.get('steps_per_episode', '')} | "
                     f"{o.get('model_ms_per_step', '')} | {o.get('seconds_per_episode', '')} | "
                     f"{(r.get('harness') or {}).get('minutes', '')} | {(r.get('harness') or {}).get('deadline_hit', '')} |"
                     + (f" error: {r['error']}" if r.get("error") else ""))
    lines += ["", f"llama.cpp: {results.get('llama')}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- main --

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="repo checkout instead of web_eval_src.tar.gz")
    ap.add_argument("--inputs", default=None, help="dir holding the dataset files (default: /kaggle/input)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the commands, install / start nothing")
    a = ap.parse_args(argv)
    inputs = Path(a.inputs) if a.inputs else Path("/kaggle/input")
    cfg_path = Path(a.config) if a.config else find_input("web_eval_config.json", inputs)
    cfg = dict(DEFAULTS, **(json.loads(cfg_path.read_text()) if cfg_path else {}))
    if not cfg["models"]:
        raise SystemExit("no models in web_eval_config.json")
    out = Path(a.out or ("/kaggle/working" if on_kaggle() else "./web-eval-out"))
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path("/kaggle/tmp/web-eval") if on_kaggle() else out / "tmp"
    t_start = time.time()
    deadline = t_start + float(cfg["deadline_hours"]) * 3600
    tasks_json = find_input(TASK_FILE, inputs)
    if not tasks_json:
        raise SystemExit(f"{TASK_FILE} not found under {inputs} (attach the awos-web-eval-data dataset)")
    ggufs = {}
    for m in cfg["models"]:
        g = find_input(m["file"], inputs)
        if not g and not a.dry_run:
            raise SystemExit(f"{m['file']} ({m['label']}) not found under {inputs}")
        ggufs[m["label"]] = g or Path(m["file"])
    if a.src:
        src = Path(a.src).resolve()
    elif a.dry_run:
        src = tmp / "src"
    else:
        src = locate_src(inputs, tmp)
    ports = [int(cfg["base_port"]) + g for g in range(int(cfg["gpus"]))]
    log(f"models: {', '.join(ggufs)} | split {cfg['split']} | {cfg['workers']} workers over {cfg['gpus']} GPU(s), "
        f"{slots(cfg)} slots each | caps {cfg['max_steps']} steps, {cfg['max_seconds']} s per task")
    if a.dry_run:
        for c in build_cmds(cfg, tmp / "llama-src"):
            log("build fallback:", " ".join(c))
        log("prebuilt:", " ".join(prebuilt_assets(cfg)))
        for label, g in ggufs.items():
            for gpu, port, cmd, env in server_plan(cfg, "llama-server", str(g), label):
                log(f"server gpu{gpu}: CUDA_VISIBLE_DEVICES={env['CUDA_VISIBLE_DEVICES']}", " ".join(cmd))
            log("harness:", " ".join(harness_cmd(cfg, label, str(tasks_json), out / f"{label}__web-{cfg['split']}",
                                                  ports)))
        return 0

    ntfy(cfg, f"start: {len(ggufs)} model(s) {' '.join(ggufs)} on {cfg['split']}", title="AWOS web eval", tags="rocket")
    results = {"config": {k: v for k, v in cfg.items() if k != "ntfy_topic"}, "models": {}, "llama": None}
    env_info = {"python": sys.version, "driver_cuda": ".".join(map(str, driver_cuda()))}
    try:
        env_info["nvidia_smi"] = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout
    except OSError:
        pass
    try:
        log("pip install", cfg["playwright_pin"])
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", cfg["playwright_pin"]])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                              stdout=subprocess.DEVNULL)
        cands = llama_candidates(cfg, tmp, out)
        binary = None
        for label, gguf in ggufs.items():
            mout = out / f"{label}__web-{cfg['split']}"
            mout.mkdir(parents=True, exist_ok=True)
            r = results["models"][label] = {"gguf": gguf.name}
            procs = None
            while procs is None:
                if binary is None:
                    try:
                        how, binary = next(cands)
                    except StopIteration:
                        raise SystemExit("no working llama-server (attached, prebuilt and source build all failed)")
                    results["llama"] = how
                    log(f"llama-server: {how} ({binary})")
                try:
                    procs = start_servers(cfg, binary, gguf, label, mout)
                except Exception as e:
                    log(f"llama-server ({results['llama']}) failed: {e}")
                    if results["llama"] == "source" or len(results["models"]) > 1:
                        r["error"] = str(e)[:300]
                        break
                    binary = None                               # first model: try the next candidate
            if procs is None:
                continue
            if time.time() > deadline:
                r["error"] = "deadline reached before the model started"
                stop_servers(procs)
                continue
            ntfy(cfg, f"{label}: servers up ({results['llama']} llama.cpp, {len(procs)} GPU(s))", title="AWOS web eval")
            try:
                r["harness"] = run_harness(cfg, harness_cmd(cfg, label, str(tasks_json), mout, ports), src, label,
                                           mout, deadline)
            finally:
                stop_servers(procs)
            r["summary"] = summarize(src, mout)
            o = r["summary"].get("overall") or {}
            ntfy(cfg, f"{label} done: {o.get('episodes', 0)} episodes, answered {o.get('answered')}, blocked "
                      f"{o.get('blocked')}, {o.get('model_ms_per_step')} ms/model step, {r['harness']['minutes']} min",
                 title="AWOS web eval", tags="white_check_mark")
    except BaseException as e:
        results["error"] = f"{type(e).__name__}: {e}"[:500]
        ntfy(cfg, results["error"], title="AWOS web eval failed", priority="high", tags="rotating_light")
        raise
    finally:
        results["minutes"] = round((time.time() - t_start) / 60, 1)
        (out / "env.json").write_text(json.dumps(env_info, indent=1) + "\n")
        (out / "summary.json").write_text(json.dumps(results, indent=1) + "\n")
        (out / "summary.md").write_text(to_markdown(results))
        shutil.rmtree(tmp, ignore_errors=True)
    print((out / "summary.md").read_text())
    ntfy(cfg, f"all done in {results['minutes']} min", title="AWOS web eval", tags="tada")
    return 0 if not any(r.get("error") for r in results["models"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
