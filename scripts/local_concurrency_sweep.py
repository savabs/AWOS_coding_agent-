#!/usr/bin/env python3
"""local_concurrency_sweep.py — throughput/latency/memory of llama-server vs slots.

Pre-registered in docs/specs/local_model_reality_2026-10.md (Part 2).

For each setting it (re)starts llama-server through scripts/local_model.sh with
AWOS_LOCAL_SLOTS=np and AWOS_LOCAL_CONTEXT=ctx_per_slot*np, sends one warm-up
request, then fires N distinct ~2k-token prompts at once at /completion
(n_predict 256, ignore_eos, temperature 0) and records wall time, aggregate
generated tok/s, per-request latency, server timings, RSS / footprint peak and
system free memory. Results are written per setting to --out (resumable: a
setting already in the file is skipped). The server is stopped at the end.

    python3 scripts/local_concurrency_sweep.py --slots 1,2,4,8 --out sweep.json
    python3 scripts/local_concurrency_sweep.py --slots 8 --kv-unified --ctx-total 32768 ...
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "local_model.sh"
PORT = int(os.environ.get("AWOS_LOCAL_PORT", 8080))
BASE = f"http://127.0.0.1:{PORT}"
PID_FILE = Path(os.environ.get("AWOS_MODELS_DIR", Path.home() / ".cache/awos-models")) / f"llama-server.{PORT}.pid"

WORDS = ("alpha beta gamma delta parse token buffer index cache queue thread lock "
         "value field record schema router socket stream batch merge split filter "
         "reduce yield import return class method object string number list dict "
         "error retry budget ledger memory kernel worker planner verify reward").split()


def post(path: str, body: dict, timeout: float = 900) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def make_prompt(i: int, target_tokens: int) -> str:
    rng = random.Random(1000 + i)
    head = f"Document {i}. Summarize the following log in detail.\n"
    words, out, n = [], head, 0
    while n < target_tokens:          # grow until at least the target
        words.extend(rng.choice(WORDS) for _ in range(100))
        out = head + " ".join(words)
        n = len(post("/tokenize", {"content": out})["tokens"])
    while n > target_tokens:          # trim to the target
        words = words[: int(len(words) * target_tokens / n) - 1]
        out = head + " ".join(words)
        n = len(post("/tokenize", {"content": out})["tokens"])
    return out


def log_buffers() -> list[str]:
    """Buffer-size lines llama-server printed at load (model, KV, recurrent state, compute)."""
    log = PID_FILE.with_name(f"llama-server.{PORT}.log")
    try:
        lines = log.read_text(errors="replace").splitlines()
    except Exception:
        return []
    return [re.sub(r"^[\d.]+ \w ", "", l).strip() for l in lines
            if re.search(r"buffer size|n_ctx_slot|kv_unified|n_ctx_seq", l)][:40]


def server_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout
    return round(int(out.strip() or 0) / 1024, 1)


def footprint_mb(pid: int) -> float | None:
    out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True).stdout
    m = re.search(r"Footprint:\s*([\d.]+)\s*([KMG]B)", out)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return round(v * {"KB": 1 / 1024, "MB": 1, "GB": 1024}[u], 1)


def free_pct() -> int | None:
    out = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
    m = re.search(r"free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if m else None


def swap_used_mb() -> float | None:
    out = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True).stdout
    m = re.search(r"used = ([\d.]+)M", out)
    return float(m.group(1)) if m else None


def battery() -> str:
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    return out.strip().splitlines()[-1].split("\t")[-1] if out else ""


def start_server(np_: int, ctx: int, extra: str) -> None:
    env = {**os.environ, "AWOS_LOCAL_SLOTS": str(np_), "AWOS_LOCAL_CONTEXT": str(ctx),
           "AWOS_LOCAL_EXTRA_ARGS": extra}
    subprocess.run(["bash", str(SCRIPT), "stop"], env=env, capture_output=True)
    r = subprocess.run(["bash", str(SCRIPT), "start"], env=env, capture_output=True, text=True)
    print(r.stdout[-400:], r.stderr[-400:], flush=True)
    if r.returncode != 0:
        raise SystemExit(f"server failed to start for np={np_}")


def one(prompt: str, n_predict: int, t0: float) -> dict:
    s = time.monotonic()
    r = post("/completion", {"prompt": prompt, "n_predict": n_predict, "ignore_eos": True,
                             "temperature": 0, "cache_prompt": False})
    e = time.monotonic()
    t = r.get("timings", {})
    return {"start_s": round(s - t0, 2), "latency_s": round(e - s, 2),
            "predicted_n": t.get("predicted_n"), "prompt_n": t.get("prompt_n"),
            "decode_tps": round(t.get("predicted_per_second") or 0, 2),
            "prefill_tps": round(t.get("prompt_per_second") or 0, 1),
            "prompt_ms": round(t.get("prompt_ms") or 0), "predicted_ms": round(t.get("predicted_ms") or 0)}


def run_setting(np_: int, ctx: int, extra: str, n_req: int, prompt_tokens: int, n_predict: int) -> dict:
    start_server(np_, ctx, extra)
    pid = server_pid()
    rss_idle, fp_idle = rss_mb(pid), footprint_mb(pid)
    post("/completion", {"prompt": "Hello", "n_predict": 16, "temperature": 0})   # warm-up
    prompts = [make_prompt(i, prompt_tokens) for i in range(n_req)]
    peak = {"rss": rss_idle, "fp": fp_idle or 0, "free_min": free_pct() or 100}
    swap0 = swap_used_mb()
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak["rss"] = max(peak["rss"], rss_mb(pid))
            f = footprint_mb(pid)
            if f:
                peak["fp"] = max(peak["fp"], f)
            fp = free_pct()
            if fp is not None:
                peak["free_min"] = min(peak["free_min"], fp)
            stop.wait(1.0)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_req) as pool:
        reqs = list(pool.map(lambda p: one(p, n_predict, t0), prompts))
    wall = time.monotonic() - t0
    stop.set()
    th.join(timeout=5)
    gen = sum(r["predicted_n"] or 0 for r in reqs)
    lat = [r["latency_s"] for r in reqs]
    return {
        "np": np_, "ctx_total": ctx, "ctx_per_slot": ctx // np_ if "--kv-unified" not in extra else ctx,
        "extra_args": extra, "n_requests": n_req, "prompt_tokens": [r["prompt_n"] for r in reqs],
        "n_predict": n_predict, "wall_s": round(wall, 2), "generated_tokens": gen,
        "aggregate_gen_tps": round(gen / wall, 2),
        "aggregate_total_tps": round((gen + sum(r["prompt_n"] or 0 for r in reqs)) / wall, 2),
        "latency_mean_s": round(sum(lat) / len(lat), 2), "latency_max_s": max(lat),
        "latency_min_s": min(lat),
        "rss_idle_mb": rss_idle, "rss_peak_mb": peak["rss"],
        "footprint_idle_mb": fp_idle, "footprint_peak_mb": peak["fp"],
        "system_free_pct_min": peak["free_min"], "swap_used_mb_before": swap0,
        "swap_used_mb_after": swap_used_mb(), "battery": battery(),
        "server_buffers": log_buffers(), "requests": reqs, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", default="1,2,4,8")
    ap.add_argument("--ctx-per-slot", type=int, default=12288)
    ap.add_argument("--ctx-total", type=int, default=None, help="fixed total context (kv-unified variant)")
    ap.add_argument("--kv-unified", action="store_true")
    ap.add_argument("--requests", type=int, default=8)
    ap.add_argument("--prompt-tokens", type=int, default=2000)
    ap.add_argument("--n-predict", type=int, default=256)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-server", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    data = json.loads(out.read_text()) if out.exists() else {"runs": []}
    extra = "--kv-unified" if a.kv_unified else ""
    try:
        for np_ in [int(x) for x in a.slots.split(",")]:
            key = f"np{np_}{'-kvu' if extra else ''}-r{a.requests}"
            if any(r.get("key") == key for r in data["runs"]):
                print(f"skip {key} (done)")
                continue
            ctx = a.ctx_total or a.ctx_per_slot * np_
            print(f"== {key}: -np {np_} -c {ctx} {extra}", flush=True)
            res = run_setting(np_, ctx, extra, a.requests, a.prompt_tokens, a.n_predict)
            res["key"] = key
            data["runs"].append(res)
            out.write_text(json.dumps(data, indent=1))
            print(json.dumps({k: v for k, v in res.items() if k != "requests"}), flush=True)
    finally:
        if not a.keep_server:
            subprocess.run(["bash", str(SCRIPT), "stop"], capture_output=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
